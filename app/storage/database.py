from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Index,
    String,
    Text,
    and_,
    create_engine,
    delete,
    func,
    inspect as sqlalchemy_inspect,
    or_,
    select,
    text,
    update,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Session, relationship
from sqlalchemy.pool import StaticPool

from app.config import normalize_phone, phone_variants

JOB_LEASE_SECONDS = 5 * 60


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_datetime(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def conversation_protocol(conversation_id: int) -> str:
    """Return the short, non-sensitive support reference for a conversation."""
    return f"CWB-{int(conversation_id):08d}"


class Base(DeclarativeBase):
    pass


class ConversationRecord(Base):
    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True)
    phone = Column(String(32), unique=True, nullable=False, index=True)
    # Nullable keeps the ORM compatible with databases created before support
    # references existed; Repository.initialize backfills old rows.
    protocol = Column(String(32), nullable=True)
    chat_name = Column(String(255), nullable=True)
    status = Column(String(32), nullable=False, default="bot_active", index=True)
    paused_reason = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)

    messages = relationship("MessageRecord", back_populates="conversation", cascade="all, delete-orphan")


class RecoverySkipRecord(Base):
    __tablename__ = "recovery_skips"

    id = Column(Integer, primary_key=True)
    conversation_id = Column(Integer, ForeignKey("conversations.id"), nullable=False, unique=True, index=True)
    skipped_last_message_id = Column(Integer, nullable=False)
    operator = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class RecoverySendClaimRecord(Base):
    __tablename__ = "recovery_send_claims"

    conversation_id = Column(Integer, ForeignKey("conversations.id"), primary_key=True)
    expected_last_message_id = Column(Integer, nullable=False)
    token = Column(String(64), unique=True, nullable=False, index=True)
    status = Column(String(20), nullable=False, default="sending", index=True)
    invalidated = Column(Boolean, nullable=False, default=False)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class MessageRecord(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True)
    conversation_id = Column(Integer, ForeignKey("conversations.id"), nullable=False, index=True)
    direction = Column(String(16), nullable=False)
    kind = Column(String(32), nullable=False)
    text = Column(Text, nullable=False, default="")
    provider_message_id = Column(String(255), nullable=True, index=True)
    raw = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, index=True)

    conversation = relationship("ConversationRecord", back_populates="messages")


class InboundEventRecord(Base):
    __tablename__ = "inbound_events"
    __table_args__ = (Index("ix_inbound_events_phone_received_at", "phone", "received_at"),)

    id = Column(Integer, primary_key=True)
    external_id = Column(String(255), unique=True, nullable=False, index=True)
    phone = Column(String(32), nullable=True)
    payload = Column(JSON, nullable=False)
    received_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)

    jobs = relationship("JobRecord", back_populates="event", cascade="all, delete-orphan")
    batch_links = relationship("BatchEventRecord", back_populates="event", cascade="all, delete-orphan")


class JobRecord(Base):
    """Legacy one-event job table kept for compatibility with existing data."""

    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True)
    event_id = Column(Integer, ForeignKey("inbound_events.id"), nullable=False, index=True)
    status = Column(String(20), nullable=False, default="pending", index=True)
    attempts = Column(Integer, nullable=False, default=0)
    available_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, index=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    finished_at = Column(DateTime(timezone=True), nullable=True)

    event = relationship("InboundEventRecord", back_populates="jobs")


class MessageBatchRecord(Base):
    __tablename__ = "message_batches"

    id = Column(Integer, primary_key=True)
    phone = Column(String(32), nullable=False, index=True)
    status = Column(String(20), nullable=False, default="pending", index=True)
    attempts = Column(Integer, nullable=False, default=0)
    available_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, index=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    finished_at = Column(DateTime(timezone=True), nullable=True)

    event_links = relationship("BatchEventRecord", back_populates="batch", cascade="all, delete-orphan")


class BatchEventRecord(Base):
    __tablename__ = "message_batch_events"

    id = Column(Integer, primary_key=True)
    batch_id = Column(Integer, ForeignKey("message_batches.id"), nullable=False, index=True)
    event_id = Column(Integer, ForeignKey("inbound_events.id"), nullable=False, unique=True, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)

    batch = relationship("MessageBatchRecord", back_populates="event_links")
    event = relationship("InboundEventRecord", back_populates="batch_links")


class AuditEventRecord(Base):
    __tablename__ = "audit_events"

    id = Column(Integer, primary_key=True)
    event_type = Column(String(64), nullable=False, index=True)
    subject = Column(String(255), nullable=True)
    detail = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class SystemSettingRecord(Base):
    __tablename__ = "system_settings"

    key = Column(String(64), primary_key=True)
    value = Column(JSON, nullable=False, default=dict)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class AdminSessionRecord(Base):
    __tablename__ = "admin_sessions"

    id = Column(Integer, primary_key=True)
    session_id = Column(String(128), unique=True, nullable=False, index=True)
    operator = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    last_seen_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True, index=True)


def build_engine(database_url: str) -> Engine:
    kwargs: dict[str, Any] = {"future": True, "pool_pre_ping": True}
    if database_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if ":memory:" in database_url:
            kwargs["poolclass"] = StaticPool
    return create_engine(database_url, **kwargs)


class Repository:
    def __init__(self, engine: Engine):
        self.engine = engine

    def initialize(self) -> None:
        with self.engine.begin() as connection:
            if self.engine.dialect.name == "postgresql":
                connection.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
                    {"lock_key": "robo-schema-initialize"},
                )
            Base.metadata.create_all(connection)
            inspector = sqlalchemy_inspect(connection)
            conversation_columns = {
                column["name"] for column in inspector.get_columns("conversations")
            }
            if "protocol" not in conversation_columns:
                connection.execute(text("ALTER TABLE conversations ADD COLUMN protocol VARCHAR(32)"))
            event_columns = {
                column["name"] for column in inspector.get_columns("inbound_events")
            }
            if "phone" not in event_columns:
                connection.execute(text("ALTER TABLE inbound_events ADD COLUMN phone VARCHAR(32)"))
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_inbound_events_phone_received_at "
                    "ON inbound_events (phone, received_at)"
                )
            )
            # create_all does not add indexes to an already existing table.
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_conversations_protocol "
                    "ON conversations (protocol)"
                )
            )

        with Session(self.engine) as session:
            records = session.scalars(select(ConversationRecord).order_by(ConversationRecord.id)).all()
            for record in records:
                if not record.protocol:
                    record.protocol = conversation_protocol(record.id)
            unindexed_events = session.scalars(
                select(InboundEventRecord).where(InboundEventRecord.phone.is_(None))
            ).all()
            for event in unindexed_events:
                payload = dict(event.payload or {})
                phone = normalize_phone(
                    str(payload.get("phone") or payload.get("participantPhone") or "")
                )
                if phone:
                    event.phone = phone
            session.commit()

    def healthcheck(self) -> bool:
        try:
            with Session(self.engine) as session:
                session.execute(select(1)).scalar_one()
            return True
        except Exception:
            return False

    @staticmethod
    def session_expiry(seconds: int) -> datetime:
        return utc_now() + timedelta(seconds=max(1, int(seconds)))

    def get_setting(self, key: str, default: Any = None) -> Any:
        with Session(self.engine) as session:
            record = session.get(SystemSettingRecord, str(key))
            if record is None:
                return default
            return record.value

    def set_setting(self, key: str, value: Any) -> Any:
        normalized_key = str(key).strip()
        if not normalized_key:
            raise ValueError("Chave de configuração inválida")
        with Session(self.engine, expire_on_commit=False) as session:
            record = session.get(SystemSettingRecord, normalized_key)
            if record is None:
                record = SystemSettingRecord(key=normalized_key, value=value)
                session.add(record)
            else:
                record.value = value
                record.updated_at = utc_now()
            session.commit()
            return record.value

    def get_bot_control_state(self) -> dict[str, Any]:
        value = self.get_setting(
            "bot_control",
            {"mode": "active", "reason": None, "operator": None, "updated_at": None},
        )
        if not isinstance(value, dict):
            value = {}
        mode = str(value.get("mode") or "active")
        if mode not in {"active", "paused", "maintenance"}:
            mode = "active"
        return {
            "mode": mode,
            "reason": value.get("reason"),
            "operator": value.get("operator"),
            "updated_at": value.get("updated_at"),
        }

    def set_bot_control_state(self, mode: str, reason: str | None, operator: str | None) -> dict[str, Any]:
        normalized_mode = str(mode or "").strip().lower()
        if normalized_mode not in {"active", "paused", "maintenance"}:
            raise ValueError("Estado global do robô inválido")
        state = {
            "mode": normalized_mode,
            "reason": str(reason or "").strip()[:255] or None,
            "operator": str(operator or "").strip()[:255] or None,
            "updated_at": utc_now().isoformat(),
        }
        self.set_setting("bot_control", state)
        return state

    def register_admin_session(self, session_id: str, operator: str, expires_at: datetime) -> None:
        normalized_id = str(session_id).strip()
        if not normalized_id:
            raise ValueError("Sessão administrativa inválida")
        with Session(self.engine) as session:
            record = session.scalar(
                select(AdminSessionRecord).where(AdminSessionRecord.session_id == normalized_id)
            )
            now = utc_now()
            if record is None:
                record = AdminSessionRecord(
                    session_id=normalized_id,
                    operator=str(operator),
                    created_at=now,
                    last_seen_at=now,
                    expires_at=expires_at,
                    revoked_at=None,
                )
                session.add(record)
            else:
                record.operator = str(operator)
                record.last_seen_at = now
                record.expires_at = expires_at
                record.revoked_at = None
            session.commit()

    def touch_admin_session(self, session_id: str) -> bool:
        with Session(self.engine) as session:
            record = session.scalar(
                select(AdminSessionRecord).where(AdminSessionRecord.session_id == str(session_id))
            )
            now = utc_now()
            if (
                record is None
                or record.revoked_at is not None
                or _utc_datetime(record.expires_at) <= now
            ):
                return False
            record.last_seen_at = now
            session.commit()
            return True

    def list_active_admin_sessions(self) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            now = utc_now()
            records = session.scalars(
                select(AdminSessionRecord)
                .where(
                    AdminSessionRecord.revoked_at.is_(None),
                    AdminSessionRecord.expires_at > now,
                )
                .order_by(AdminSessionRecord.last_seen_at.desc(), AdminSessionRecord.id.desc())
            ).all()
            return [
                {
                    "session_id": record.session_id,
                    "operator": record.operator,
                    "created_at": record.created_at,
                    "last_seen_at": record.last_seen_at,
                    "expires_at": record.expires_at,
                }
                for record in records
            ]

    def revoke_admin_sessions(self) -> int:
        with Session(self.engine) as session:
            now = utc_now()
            records = session.scalars(
                select(AdminSessionRecord).where(
                    AdminSessionRecord.revoked_at.is_(None),
                    AdminSessionRecord.expires_at > now,
                )
            ).all()
            for record in records:
                record.revoked_at = now
            session.commit()
            return len(records)

    @staticmethod
    def _find_conversation(session: Session, phone: str) -> ConversationRecord | None:
        variants = phone_variants(phone)
        if not variants:
            return None
        record = session.scalar(
            select(ConversationRecord).where(ConversationRecord.phone == variants[0])
        )
        if record is not None or len(variants) == 1:
            return record
        return session.scalar(
            select(ConversationRecord).where(ConversationRecord.phone == variants[1])
        )

    @classmethod
    def _find_conversation_reference(cls, session: Session, reference: str) -> ConversationRecord | None:
        candidate = str(reference or "").strip()
        if candidate.upper().startswith("CWB-"):
            return session.scalar(
                select(ConversationRecord).where(
                    ConversationRecord.protocol == candidate.upper()
                )
            )
        return cls._find_conversation(session, candidate)

    @staticmethod
    def _ensure_protocol(session: Session, record: ConversationRecord) -> None:
        session.flush()
        if not record.protocol:
            record.protocol = conversation_protocol(record.id)

    def get_or_create_conversation(self, phone: str, chat_name: str | None = None) -> ConversationRecord:
        normalized_phone = normalize_phone(phone)
        with Session(self.engine, expire_on_commit=False) as session:
            record = self._find_conversation(session, normalized_phone)
            if record is None:
                record = ConversationRecord(
                    phone=normalized_phone,
                    chat_name=chat_name,
                    status="bot_active",
                )
                session.add(record)
                self._ensure_protocol(session, record)
            elif chat_name and not record.chat_name:
                record.chat_name = chat_name
                self._ensure_protocol(session, record)
            else:
                self._ensure_protocol(session, record)
            record.updated_at = utc_now()
            session.commit()
            return record

    def get_conversation(self, phone: str) -> ConversationRecord | None:
        with Session(self.engine, expire_on_commit=False) as session:
            return self._find_conversation(session, phone)

    def conversation_status_counts(self) -> dict[str, int]:
        with Session(self.engine) as session:
            rows = session.execute(
                select(ConversationRecord.status, func.count(ConversationRecord.id)).group_by(
                    ConversationRecord.status
                )
            ).all()
        counts = {str(status): int(total) for status, total in rows}
        for status in ("bot_active", "human_pending", "human_active", "closed"):
            counts.setdefault(status, 0)
        counts["total"] = sum(value for key, value in counts.items() if key != "total")
        return counts

    def list_conversations(
        self,
        statuses: tuple[str, ...] | None = None,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 200))
        with Session(self.engine) as session:
            latest_messages = (
                select(
                    MessageRecord.conversation_id.label("conversation_id"),
                    MessageRecord.id.label("id"),
                    MessageRecord.direction.label("direction"),
                    MessageRecord.text.label("text"),
                    MessageRecord.created_at.label("created_at"),
                    func.row_number()
                    .over(
                        partition_by=MessageRecord.conversation_id,
                        order_by=(MessageRecord.created_at.desc(), MessageRecord.id.desc()),
                    )
                    .label("row_number"),
                )
                .subquery()
            )
            statement = select(
                ConversationRecord,
                latest_messages.c.id,
                latest_messages.c.direction,
                latest_messages.c.text,
                latest_messages.c.created_at,
            ).outerjoin(
                latest_messages,
                (latest_messages.c.conversation_id == ConversationRecord.id)
                & (latest_messages.c.row_number == 1),
            )
            if statuses:
                statement = statement.where(ConversationRecord.status.in_(statuses))
            statement = statement.order_by(
                ConversationRecord.updated_at.desc(), ConversationRecord.id.desc()
            )
            rows = session.execute(statement.limit(safe_limit)).all()
            result: list[dict[str, Any]] = []
            for record, last_message_id, last_direction, last_text, last_created_at in rows:
                result.append(
                    {
                        "protocol": record.protocol,
                        "phone": record.phone,
                        "chat_name": record.chat_name,
                        "status": record.status,
                        "paused_reason": record.paused_reason,
                        "updated_at": record.updated_at,
                        "last_message_id": int(last_message_id) if last_message_id else None,
                        "last_message": (last_text or "")[:400],
                        "last_message_direction": last_direction,
                        "last_message_at": last_created_at,
                    }
                )
            return result

    @staticmethod
    def _recovery_filter() -> Any:
        skipped = select(RecoverySkipRecord.id).where(
            RecoverySkipRecord.conversation_id == ConversationRecord.id
        ).exists()
        has_customer_message = select(MessageRecord.id).where(
            MessageRecord.conversation_id == ConversationRecord.id,
            MessageRecord.direction == "inbound",
        ).exists()
        return and_(
            ~skipped,
            has_customer_message,
            ConversationRecord.status.in_(("human_pending", "bot_active")),
        )

    @staticmethod
    def _recovery_search_filter(search: str | None, latest_messages: Any) -> Any | None:
        term = str(search or "").strip()
        if not term:
            return None
        pattern = f"%{term}%"
        clauses = [
            ConversationRecord.chat_name.ilike(pattern),
            ConversationRecord.phone.ilike(pattern),
            latest_messages.c.text.ilike(pattern),
        ]
        normalized = normalize_phone(term)
        if normalized:
            clauses.append(ConversationRecord.phone.in_(phone_variants(normalized)))
        return or_(*clauses)

    def list_recovery_conversations(
        self,
        *,
        before: datetime,
        limit: int = 50,
        offset: int = 0,
        search: str | None = None,
    ) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 200))
        safe_offset = max(0, int(offset))
        with Session(self.engine) as session:
            latest_messages = (
                select(
                    MessageRecord.conversation_id.label("conversation_id"),
                    MessageRecord.id.label("id"),
                    MessageRecord.direction.label("direction"),
                    MessageRecord.text.label("text"),
                    MessageRecord.created_at.label("created_at"),
                    func.row_number()
                    .over(
                        partition_by=MessageRecord.conversation_id,
                        order_by=(MessageRecord.created_at.desc(), MessageRecord.id.desc()),
                    )
                    .label("row_number"),
                )
                .subquery()
            )
            conditions = [
                ConversationRecord.updated_at < before,
                self._recovery_filter(),
            ]
            search_filter = self._recovery_search_filter(search, latest_messages)
            if search_filter is not None:
                conditions.append(search_filter)
            statement = (
                select(
                    ConversationRecord,
                    latest_messages.c.id,
                    latest_messages.c.direction,
                    latest_messages.c.text,
                    latest_messages.c.created_at,
                )
                .outerjoin(
                    latest_messages,
                    (latest_messages.c.conversation_id == ConversationRecord.id)
                    & (latest_messages.c.row_number == 1),
                )
                .where(*conditions)
                .order_by(ConversationRecord.updated_at.asc(), ConversationRecord.id.asc())
                .offset(safe_offset)
                .limit(safe_limit)
            )
            rows = session.execute(statement).all()
            return [
                {
                    "protocol": record.protocol,
                    "phone": record.phone,
                    "chat_name": record.chat_name,
                    "status": record.status,
                    "paused_reason": record.paused_reason,
                    "updated_at": record.updated_at,
                    "last_message_id": int(last_message_id) if last_message_id else None,
                    "last_message": (last_text or "")[:400],
                    "last_message_direction": last_direction,
                    "last_message_at": last_message_at,
                }
                for record, last_message_id, last_direction, last_text, last_message_at in rows
            ]

    def count_recovery_conversations(self, *, before: datetime, search: str | None = None) -> int:
        with Session(self.engine) as session:
            latest_messages = (
                select(
                    MessageRecord.conversation_id.label("conversation_id"),
                    MessageRecord.direction.label("direction"),
                    MessageRecord.text.label("text"),
                    func.row_number()
                    .over(
                        partition_by=MessageRecord.conversation_id,
                        order_by=(MessageRecord.created_at.desc(), MessageRecord.id.desc()),
                    )
                    .label("row_number"),
                )
                .subquery()
            )
            conditions = [
                ConversationRecord.updated_at < before,
                self._recovery_filter(),
            ]
            search_filter = self._recovery_search_filter(search, latest_messages)
            if search_filter is not None:
                conditions.append(search_filter)
            statement = (
                select(func.count(ConversationRecord.id))
                .outerjoin(
                    latest_messages,
                    (latest_messages.c.conversation_id == ConversationRecord.id)
                    & (latest_messages.c.row_number == 1),
                )
                .where(*conditions)
            )
            return int(session.scalar(statement) or 0)

    def conversation_detail(self, reference: str, *, limit: int = 80) -> dict[str, Any] | None:
        safe_limit = max(1, min(int(limit), 200))
        with Session(self.engine) as session:
            conversation = self._find_conversation_reference(session, reference)
            if conversation is None:
                return None
            self._ensure_protocol(session, conversation)
            records = session.scalars(
                select(MessageRecord)
                .where(MessageRecord.conversation_id == conversation.id)
                .order_by(MessageRecord.created_at.desc(), MessageRecord.id.desc())
                .limit(safe_limit)
            ).all()
            messages = [
                {
                    "id": int(item.id),
                    "direction": item.direction,
                    "kind": item.kind,
                    "text": item.text or "",
                    "provider_message_id": item.provider_message_id,
                    "created_at": item.created_at,
                }
                for item in reversed(records)
            ]
            audit_records = session.scalars(
                select(AuditEventRecord)
                .where(AuditEventRecord.subject.in_(phone_variants(conversation.phone)))
                .order_by(AuditEventRecord.created_at.asc(), AuditEventRecord.id.asc())
                .limit(200)
            ).all()
            audit = [
                {
                    "id": int(item.id),
                    "event_type": item.event_type,
                    "subject": item.subject,
                    "detail": dict(item.detail or {}),
                    "created_at": item.created_at,
                }
                for item in audit_records
            ]
            error_count = sum("error" in str(item["event_type"]).lower() for item in audit)
            handoff_count = sum(
                "handoff" in str(item["event_type"]).lower()
                or bool(item["detail"].get("handoff"))
                for item in audit
            )
            latest = messages[-1] if messages else None
            return {
                "protocol": conversation.protocol,
                "phone": conversation.phone,
                "chat_name": conversation.chat_name,
                "status": conversation.status,
                "paused_reason": conversation.paused_reason,
                "created_at": conversation.created_at,
                "updated_at": conversation.updated_at,
                "last_message_id": latest["id"] if latest else None,
                "messages": messages,
                "audit": audit,
                "diagnostics": {
                    "message_count": len(messages),
                    "inbound_count": sum(item["direction"] == "inbound" for item in messages),
                    "outbound_count": sum(item["direction"] == "outbound" for item in messages),
                    "audit_count": len(audit),
                    "error_count": error_count,
                    "handoff_count": handoff_count,
                },
            }

    def set_conversation_status(self, phone: str, status: str, reason: str | None = None) -> None:
        normalized_phone = normalize_phone(phone)
        with Session(self.engine) as session:
            record = self._find_conversation(session, normalized_phone)
            if record is None:
                record = ConversationRecord(
                    phone=normalized_phone,
                    status=status,
                    paused_reason=reason,
                )
                session.add(record)
                self._ensure_protocol(session, record)
            else:
                self._ensure_protocol(session, record)
                record.status = status
                record.paused_reason = reason
                record.updated_at = utc_now()
            session.commit()

    def skip_recovery_conversation(
        self,
        phone: str,
        expected_last_message_id: int,
        operator: str | None = None,
    ) -> bool:
        with Session(self.engine) as session:
            conversation = self._find_conversation(session, phone)
            if conversation is None:
                return False
            latest_message_id = session.scalar(
                select(MessageRecord.id)
                .where(MessageRecord.conversation_id == conversation.id)
                .order_by(MessageRecord.created_at.desc(), MessageRecord.id.desc())
                .limit(1)
            )
            if int(latest_message_id or 0) != int(expected_last_message_id):
                return False
            record = session.scalar(
                select(RecoverySkipRecord).where(
                    RecoverySkipRecord.conversation_id == conversation.id
                )
            )
            if record is None:
                record = RecoverySkipRecord(
                    conversation_id=conversation.id,
                    skipped_last_message_id=int(expected_last_message_id),
                    operator=operator,
                )
                session.add(record)
            else:
                record.skipped_last_message_id = int(expected_last_message_id)
                record.operator = operator
                record.created_at = utc_now()
            session.commit()
            return True

    @staticmethod
    def _has_newer_inbound_event(session: Session, phone: str, after: datetime) -> bool:
        target_phones = set(phone_variants(phone)) or {normalize_phone(phone)}
        if not target_phones:
            return False
        event_id = session.scalar(
            select(InboundEventRecord.id)
            .where(
                InboundEventRecord.phone.in_(target_phones),
                InboundEventRecord.received_at >= _utc_datetime(after),
            )
            .limit(1)
        )
        if event_id is not None:
            return True

        # Keep compatibility for old or manually inserted rows whose payload
        # could not be backfilled into the indexed phone column.
        unindexed_events = session.scalars(
            select(InboundEventRecord)
            .where(
                InboundEventRecord.phone.is_(None),
                InboundEventRecord.received_at >= _utc_datetime(after),
            )
        ).all()
        return any(
            normalize_phone(
                str((event.payload or {}).get("phone") or (event.payload or {}).get("participantPhone") or "")
            )
            in target_phones
            for event in unindexed_events
        )

    def claim_recovery_send(
        self,
        phone: str,
        expected_last_message_id: int,
        allowed_statuses: set[str] | frozenset[str] | tuple[str, ...],
        *,
        lease_seconds: int = 300,
    ) -> tuple[str | None, str]:
        """Reserve one recovery send and reject stale or queued customer turns."""
        with Session(self.engine) as session:
            self._lock_pending_batch_phone(session, normalize_phone(phone))
            conversation = self._find_conversation(session, phone)
            if conversation is None:
                return None, "missing"
            conversation = session.scalar(
                select(ConversationRecord)
                .where(ConversationRecord.id == int(conversation.id))
                .with_for_update()
            )
            if conversation is None:
                return None, "missing"
            if conversation.status not in allowed_statuses:
                return None, "status"

            last_message = session.scalar(
                select(MessageRecord)
                .where(MessageRecord.conversation_id == int(conversation.id))
                .order_by(MessageRecord.created_at.desc(), MessageRecord.id.desc())
                .limit(1)
            )
            if last_message is None or int(last_message.id) != int(expected_last_message_id):
                return None, "stale"
            if self._has_newer_inbound_event(session, phone, last_message.created_at):
                return None, "stale"

            now = utc_now()
            claim = session.scalar(
                select(RecoverySendClaimRecord)
                .where(RecoverySendClaimRecord.conversation_id == int(conversation.id))
                .with_for_update()
            )
            if claim is not None:
                if (
                    int(claim.expected_last_message_id) == int(expected_last_message_id)
                    and claim.status in {"sent", "suppressed"}
                ):
                    return None, "already_sent"
                if claim.status == "sending" and _utc_datetime(claim.expires_at) > now:
                    return None, "sending"
                claim.expected_last_message_id = int(expected_last_message_id)
                claim.token = uuid4().hex
                claim.status = "sending"
                claim.invalidated = False
                claim.expires_at = now + timedelta(seconds=max(30, int(lease_seconds)))
                claim.updated_at = now
            else:
                claim = RecoverySendClaimRecord(
                    conversation_id=int(conversation.id),
                    expected_last_message_id=int(expected_last_message_id),
                    token=uuid4().hex,
                    status="sending",
                    invalidated=False,
                    expires_at=now + timedelta(seconds=max(30, int(lease_seconds))),
                    created_at=now,
                    updated_at=now,
                )
                session.add(claim)
            session.commit()
            return str(claim.token), "claimed"

    def recovery_send_claim_is_current(
        self,
        token: str,
        allowed_statuses: set[str] | frozenset[str] | tuple[str, ...],
        *,
        advisory_lock_held: bool = False,
    ) -> bool:
        now = utc_now()
        with Session(self.engine) as session:
            claim = session.scalar(
                select(RecoverySendClaimRecord).where(RecoverySendClaimRecord.token == str(token))
            )
            if claim is None:
                return False
            conversation = session.get(ConversationRecord, int(claim.conversation_id))
            if conversation is not None:
                if not advisory_lock_held:
                    self._lock_pending_batch_phone(session, conversation.phone)
                conversation = session.scalar(
                    select(ConversationRecord)
                    .where(ConversationRecord.id == int(claim.conversation_id))
                    .with_for_update()
                )
                claim = session.scalar(
                    select(RecoverySendClaimRecord)
                    .where(RecoverySendClaimRecord.token == str(token))
                    .with_for_update()
                )
            last_message = (
                session.scalar(
                    select(MessageRecord)
                    .where(MessageRecord.conversation_id == int(conversation.id))
                    .order_by(MessageRecord.created_at.desc(), MessageRecord.id.desc())
                    .limit(1)
                )
                if conversation is not None
                else None
            )
            return bool(
                claim is not None
                and conversation is not None
                and claim.status == "sending"
                and not claim.invalidated
                and _utc_datetime(claim.expires_at) > now
                and conversation.status in allowed_statuses
                and last_message is not None
                and int(last_message.id) == int(claim.expected_last_message_id)
                and not self._has_newer_inbound_event(session, conversation.phone, last_message.created_at)
            )

    def finish_recovery_send_claim(
        self,
        token: str,
        *,
        sent: bool,
        suppressed: bool = False,
    ) -> None:
        with Session(self.engine) as session:
            claim = session.scalar(
                select(RecoverySendClaimRecord)
                .where(RecoverySendClaimRecord.token == str(token))
                .with_for_update()
            )
            if claim is None or claim.status != "sending":
                return
            claim.status = "sent" if sent else "suppressed" if suppressed else "failed"
            claim.expires_at = utc_now()
            claim.updated_at = utc_now()
            session.commit()

    def claim_human_handoff(self, phone: str, reason: str | None = None) -> bool:
        """Atomically claim the first handoff for an active bot conversation.

        The conditional update prevents concurrent/retried processing from
        sending the same attendant notification more than once. A later
        handoff is allowed after an attendant explicitly releases the
        conversation back to bot_active.
        """
        with Session(self.engine) as session:
            now = utc_now()
            conversation = self._find_conversation(session, phone)
            if conversation is None:
                return False
            result = session.execute(
                update(ConversationRecord)
                .where(
                    ConversationRecord.id == conversation.id,
                    ConversationRecord.status == "bot_active",
                )
                .values(
                    status="human_pending",
                    paused_reason=reason,
                    updated_at=now,
                )
            )
            session.commit()
            return int(result.rowcount or 0) == 1

    def release_all_human_conversations(self, reason: str | None = None) -> int:
        """Reactivate conversations waiting for or receiving human service.

        Intentionally leaves closed conversations untouched.
        """
        with Session(self.engine) as session:
            records = session.scalars(
                select(ConversationRecord).where(
                    ConversationRecord.status.in_(("human_pending", "human_active"))
                )
            ).all()
            now = utc_now()
            for record in records:
                record.status = "bot_active"
                record.paused_reason = reason
                record.updated_at = now
            session.commit()
            return len(records)

    def add_message(
        self,
        phone: str,
        direction: str,
        kind: str,
        text: str = "",
        provider_message_id: str | None = None,
        raw: dict | None = None,
    ) -> int:
        normalized_phone = normalize_phone(phone)
        with Session(self.engine) as session:
            conversation = self._find_conversation(session, normalized_phone)
            if conversation is None:
                conversation = ConversationRecord(phone=normalized_phone, status="bot_active")
                session.add(conversation)
                self._ensure_protocol(session, conversation)
            else:
                self._ensure_protocol(session, conversation)
            record = MessageRecord(
                conversation_id=conversation.id,
                direction=direction,
                kind=kind,
                text=text,
                provider_message_id=provider_message_id,
                raw=raw or {},
            )
            session.add(record)
            if direction == "inbound":
                session.execute(
                    delete(RecoverySkipRecord).where(
                        RecoverySkipRecord.conversation_id == conversation.id
                    )
                )
            conversation.updated_at = utc_now()
            session.commit()
            return int(record.id)

    def outbound_message_exists(
        self,
        phone: str,
        *,
        kind: str,
        text: str | None,
        reply_to: str,
        source_message_id: str | None = None,
        content_digest: str | None = None,
    ) -> bool:
        with Session(self.engine) as session:
            conversation = self._find_conversation(session, phone)
            if conversation is None:
                return False
            expected_source = str(source_message_id or reply_to)
            statement = select(MessageRecord.id).where(
                MessageRecord.conversation_id == int(conversation.id),
                MessageRecord.direction == "outbound",
                MessageRecord.kind == kind,
                or_(
                    MessageRecord.raw["source_message_id"].as_string() == expected_source,
                    MessageRecord.raw["reply_to"].as_string() == expected_source,
                ),
            )
            if text is not None:
                statement = statement.where(MessageRecord.text == text)
            if content_digest is not None:
                statement = statement.where(
                    MessageRecord.raw["content_digest"].as_string() == content_digest
                )
            if session.scalar(statement.limit(1)) is not None:
                return True

            # Before source IDs were stored on outbound rows, a successful
            # provider response could still be recognized by its provider ID
            # and the inbound message interval. This avoids replaying confirmed
            # legacy text sends while leaving ambiguous/failed attempts retryable.
            if kind != "text" or text is not None or content_digest is not None:
                return False
            inbound_id = session.scalar(
                select(MessageRecord.id)
                .where(
                    MessageRecord.conversation_id == int(conversation.id),
                    MessageRecord.direction == "inbound",
                    MessageRecord.provider_message_id == expected_source,
                )
                .order_by(MessageRecord.id)
                .limit(1)
            )
            if inbound_id is None:
                return False
            prior_inbound_id = session.scalar(
                select(MessageRecord.id)
                .where(
                    MessageRecord.conversation_id == int(conversation.id),
                    MessageRecord.direction == "inbound",
                    MessageRecord.id < int(inbound_id),
                    or_(
                        MessageRecord.provider_message_id.is_(None),
                        MessageRecord.provider_message_id != expected_source,
                    ),
                )
                .limit(1)
            )
            if prior_inbound_id is not None:
                # Older records do not identify which turn a response belongs
                # to. Do not infer a match once the conversation has multiple
                # turns; a false positive would suppress a current reply.
                return False
            next_inbound_id = session.scalar(
                select(MessageRecord.id)
                .where(
                    MessageRecord.conversation_id == int(conversation.id),
                    MessageRecord.direction == "inbound",
                    MessageRecord.id > int(inbound_id),
                    or_(
                        MessageRecord.provider_message_id.is_(None),
                        MessageRecord.provider_message_id != expected_source,
                    ),
                )
                .order_by(MessageRecord.id)
                .limit(1)
            )
            legacy_send = select(MessageRecord.id).where(
                MessageRecord.conversation_id == int(conversation.id),
                MessageRecord.direction == "outbound",
                MessageRecord.kind == "text",
                MessageRecord.id > int(inbound_id),
                MessageRecord.provider_message_id.is_not(None),
            )
            if next_inbound_id is not None:
                legacy_send = legacy_send.where(MessageRecord.id < int(next_inbound_id))
            return session.scalar(legacy_send.limit(1)) is not None

    def update_message_text(self, message_id: int, text: str) -> None:
        """Update derived text for an already stored media message.

        Media descriptions are produced after the inbound event is persisted.
        Keeping the description in the message text makes it available to a
        later turn without storing the original media or exposing it in raw
        webhook data.
        """
        with Session(self.engine) as session:
            record = session.get(MessageRecord, int(message_id))
            if record is None:
                return
            record.text = text or ""
            conversation = session.get(ConversationRecord, record.conversation_id)
            if conversation is not None:
                conversation.updated_at = utc_now()
            session.commit()

    def recent_messages(
        self,
        phone: str,
        limit: int = 20,
        *,
        newer_than: datetime | None = None,
    ) -> list[dict[str, str]]:
        with Session(self.engine) as session:
            conversation = self._find_conversation(session, phone)
            if conversation is None:
                return []
            records = session.scalars(
                select(MessageRecord)
                .where(MessageRecord.conversation_id == conversation.id)
                .order_by(MessageRecord.created_at.desc(), MessageRecord.id.desc())
                .limit(limit)
            ).all()
            if newer_than is not None:
                cutoff = _utc_datetime(newer_than)
                records = [
                    item for item in records if _utc_datetime(item.created_at) >= cutoff
                ]
            return [
                {"role": "user" if item.direction == "inbound" else "assistant", "content": item.text}
                for item in reversed(records)
                if item.text
            ]

    def register_inbound_event(
        self,
        external_id: str,
        payload: dict,
        *,
        phone: str | None = None,
    ) -> tuple[int, bool]:
        normalized_phone = normalize_phone(
            phone or str(payload.get("phone") or payload.get("participantPhone") or "")
        )
        with Session(self.engine, expire_on_commit=False) as session:
            existing = session.scalar(select(InboundEventRecord).where(InboundEventRecord.external_id == external_id))
            if existing is not None:
                if not existing.phone and normalized_phone:
                    existing.phone = normalized_phone
                    session.commit()
                return int(existing.id), False
            record = InboundEventRecord(
                external_id=external_id,
                phone=normalized_phone or None,
                payload=payload,
            )
            session.add(record)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                existing = session.scalar(select(InboundEventRecord).where(InboundEventRecord.external_id == external_id))
                if existing is None:
                    raise
                if not existing.phone and normalized_phone:
                    existing.phone = normalized_phone
                    session.commit()
                return int(existing.id), False
            return int(record.id), True

    def unlinked_inbound_events(self, *, limit: int = 100) -> list[tuple[int, dict[str, Any]]]:
        """Return durable webhook receipts that still need a queue link."""
        has_job = select(JobRecord.id).where(JobRecord.event_id == InboundEventRecord.id).exists()
        has_batch = select(BatchEventRecord.id).where(BatchEventRecord.event_id == InboundEventRecord.id).exists()
        with Session(self.engine) as session:
            events = session.scalars(
                select(InboundEventRecord)
                .where(~has_job, ~has_batch)
                .order_by(InboundEventRecord.received_at, InboundEventRecord.id)
                .limit(max(1, int(limit)))
            ).all()
            return [(int(event.id), dict(event.payload or {})) for event in events]

    @staticmethod
    def _lock_pending_batch_phone(session: Session, phone: str) -> None:
        bind = session.get_bind()
        if bind.dialect.name == "postgresql":
            lock_key = f"message-batch:{phone}"
            session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
                {"lock_key": lock_key},
            )

    def acquire_recovery_send_lock(self, phone: str):
        """Hold the per-phone advisory lock across one external recovery send."""
        if self.engine.dialect.name != "postgresql":
            return None
        connection = self.engine.connect()
        lock_key = f"message-batch:{normalize_phone(phone)}"
        acquired = False
        try:
            connection.execute(
                text("SELECT pg_advisory_lock(hashtextextended(:lock_key, 0))"),
                {"lock_key": lock_key},
            )
            acquired = True
            connection.commit()
            return connection
        except Exception:
            if acquired:
                try:
                    connection.execute(
                        text("SELECT pg_advisory_unlock(hashtextextended(:lock_key, 0))"),
                        {"lock_key": lock_key},
                    )
                    connection.commit()
                except Exception:
                    connection.invalidate()
            connection.close()
            raise

    @staticmethod
    def release_recovery_send_lock(phone: str, connection) -> None:
        if connection is None:
            return
        lock_key = f"message-batch:{normalize_phone(phone)}"
        try:
            connection.execute(
                text("SELECT pg_advisory_unlock(hashtextextended(:lock_key, 0))"),
                {"lock_key": lock_key},
            )
            connection.commit()
        except Exception:
            connection.invalidate()
            raise
        finally:
            connection.close()

    def _enqueue_job_in_session(
        self,
        session: Session,
        event_id: int,
        phone: str | None,
        debounce_seconds: int,
    ) -> int:
        if phone is not None or debounce_seconds > 0:
            batch_phone = phone or f"event:{event_id}"
            self._lock_pending_batch_phone(session, batch_phone)

        existing_link = session.scalar(
            select(BatchEventRecord).where(BatchEventRecord.event_id == event_id)
        )
        if existing_link is not None:
            return int(existing_link.batch_id)

        existing_job = session.scalar(
            select(JobRecord).where(JobRecord.event_id == event_id).order_by(JobRecord.id).limit(1)
        )
        if existing_job is not None:
            return int(existing_job.id)

        if phone is None and debounce_seconds <= 0:
            job = JobRecord(event_id=event_id, status="pending", available_at=utc_now())
            session.add(job)
            session.flush()
            return int(job.id)

        due_at = utc_now() + timedelta(seconds=max(0, int(debounce_seconds)))
        batch_phone = phone or f"event:{event_id}"
        batch = session.scalar(
            select(MessageBatchRecord)
            .where(MessageBatchRecord.phone == batch_phone, MessageBatchRecord.status == "pending")
            .order_by(MessageBatchRecord.id.desc())
            .limit(1)
            .with_for_update()
        )
        if batch is None:
            batch = MessageBatchRecord(
                phone=batch_phone,
                status="pending",
                available_at=due_at,
                updated_at=utc_now(),
            )
            session.add(batch)
            session.flush()
        else:
            batch.available_at = due_at
            batch.updated_at = utc_now()
        session.add(BatchEventRecord(batch_id=batch.id, event_id=event_id))
        session.flush()
        return int(batch.id)

    @staticmethod
    def _invalidate_recovery_send_claim_in_session(session: Session, phone: str) -> None:
        conversation = Repository._find_conversation(session, phone)
        if conversation is None:
            return
        claim = session.scalar(
            select(RecoverySendClaimRecord)
            .where(RecoverySendClaimRecord.conversation_id == int(conversation.id))
            .with_for_update()
        )
        if claim is not None and claim.status == "sending":
            claim.invalidated = True
            claim.updated_at = utc_now()

    def register_inbound_event_and_enqueue(
        self,
        external_id: str,
        payload: dict,
        *,
        phone: str,
        debounce_seconds: int,
    ) -> tuple[int, bool, int]:
        """Persist a callback and its queue link in the same transaction.

        Replayed callbacks also repair an older orphan event that has no job or
        batch link, while existing queued/processed events remain idempotent.
        """
        for attempt in range(3):
            with Session(self.engine, expire_on_commit=False) as session:
                try:
                    self._lock_pending_batch_phone(session, phone)
                    record = session.scalar(
                        select(InboundEventRecord).where(InboundEventRecord.external_id == external_id)
                    )
                    created = record is None
                    if record is None:
                        record = InboundEventRecord(
                            external_id=external_id,
                            phone=normalize_phone(phone) or None,
                            payload=payload,
                        )
                        session.add(record)
                        session.flush()
                    elif not record.phone:
                        record.phone = normalize_phone(phone) or None
                    event_id = int(record.id)
                    job_id = self._enqueue_job_in_session(
                        session,
                        event_id,
                        phone,
                        debounce_seconds,
                    )
                    if created:
                        self._invalidate_recovery_send_claim_in_session(session, phone)
                    session.commit()
                    return event_id, created, job_id
                except IntegrityError:
                    session.rollback()
                    if attempt == 2:
                        raise
        raise RuntimeError("Could not register inbound event after concurrent retries")

    def enqueue_job(
        self,
        event_id: int,
        phone: str | None = None,
        debounce_seconds: int = 0,
    ) -> int:
        """Queue a durable per-phone debounce batch.

        Calls without a phone and without a delay retain the legacy one-event
        behavior used by older integrations and tests.
        """
        for attempt in range(3):
            with Session(self.engine, expire_on_commit=False) as session:
                try:
                    job_id = self._enqueue_job_in_session(
                        session,
                        event_id,
                        phone,
                        debounce_seconds,
                    )
                    session.commit()
                    return job_id
                except IntegrityError:
                    session.rollback()
                    if attempt == 2:
                        raise
        raise RuntimeError("Could not enqueue inbound event after concurrent retries")

    def _claim_legacy_job(self, session: Session, now: datetime) -> tuple[int, dict, int] | None:
        job = session.scalar(
            select(JobRecord)
            .where(JobRecord.status == "pending", JobRecord.available_at <= now)
            .order_by(JobRecord.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if job is None:
            return None
        job.status = "processing"
        job.attempts += 1
        job.available_at = now + timedelta(seconds=JOB_LEASE_SECONDS)
        attempts = int(job.attempts)
        event = session.get(InboundEventRecord, job.event_id)
        session.commit()
        return int(job.id), dict(event.payload) if event is not None else {}, attempts

    @staticmethod
    def _reclaim_expired_jobs(session: Session, now: datetime) -> None:
        expired_jobs = session.scalars(
            select(JobRecord)
            .where(JobRecord.status == "processing", JobRecord.available_at <= now)
            .order_by(JobRecord.id)
            .with_for_update(skip_locked=True)
        ).all()
        for job in expired_jobs:
            job.error = "Worker lease expired before completion"
            if job.attempts < 3:
                job.status = "pending"
                job.available_at = now
            else:
                job.status = "failed"
                job.finished_at = now

        expired_batches = session.scalars(
            select(MessageBatchRecord)
            .where(MessageBatchRecord.status == "processing", MessageBatchRecord.available_at <= now)
            .order_by(MessageBatchRecord.id)
            .with_for_update(skip_locked=True)
        ).all()
        for batch in expired_batches:
            batch.error = "Worker lease expired before completion"
            batch.updated_at = now
            if batch.attempts < 3:
                batch.status = "pending"
                batch.available_at = now
            else:
                batch.status = "failed"
                batch.finished_at = now

    def claim_next_job(
        self,
        *,
        include_attempt: bool = False,
    ) -> tuple[int, dict] | tuple[int, dict, int] | None:
        with Session(self.engine, expire_on_commit=False) as session:
            now = utc_now()
            self._reclaim_expired_jobs(session, now)
            session.commit()
            legacy = self._claim_legacy_job(session, now)
            if legacy is not None:
                return legacy if include_attempt else legacy[:2]
            batch = session.scalar(
                select(MessageBatchRecord)
                .where(MessageBatchRecord.status == "pending", MessageBatchRecord.available_at <= now)
                .order_by(MessageBatchRecord.available_at, MessageBatchRecord.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if batch is None:
                return None
            batch.status = "processing"
            batch.attempts += 1
            attempts = int(batch.attempts)
            batch.updated_at = now
            batch.available_at = now + timedelta(seconds=JOB_LEASE_SECONDS)
            links = session.scalars(
                select(BatchEventRecord)
                .where(BatchEventRecord.batch_id == batch.id)
                .order_by(BatchEventRecord.event_id)
            ).all()
            payloads = [dict(link.event.payload) for link in links if link.event is not None]
            session.commit()
            if len(payloads) == 1:
                payload: dict = payloads[0]
            else:
                payload = {"_batch_payloads": payloads}
            claimed = (-int(batch.id), payload, attempts)
            return claimed if include_attempt else claimed[:2]

    def renew_job_lease(self, job_id: int, expected_attempts: int | None = None) -> bool:
        now = utc_now()
        with Session(self.engine) as session:
            if job_id < 0:
                conditions = [
                    MessageBatchRecord.id == -job_id,
                    MessageBatchRecord.status == "processing",
                ]
                if expected_attempts is not None:
                    conditions.append(MessageBatchRecord.attempts == int(expected_attempts))
                result = session.execute(
                    update(MessageBatchRecord)
                    .where(*conditions)
                    .values(available_at=now + timedelta(seconds=JOB_LEASE_SECONDS), updated_at=now)
                )
            else:
                conditions = [JobRecord.id == job_id, JobRecord.status == "processing"]
                if expected_attempts is not None:
                    conditions.append(JobRecord.attempts == int(expected_attempts))
                result = session.execute(
                    update(JobRecord)
                    .where(*conditions)
                    .values(available_at=now + timedelta(seconds=JOB_LEASE_SECONDS))
                )
            session.commit()
            return int(result.rowcount or 0) == 1

    def finish_job(self, job_id: int, expected_attempts: int | None = None) -> None:
        with Session(self.engine) as session:
            if job_id < 0:
                batch = session.get(MessageBatchRecord, -job_id)
                if batch and (
                    expected_attempts is None
                    or (batch.status == "processing" and batch.attempts == int(expected_attempts))
                ):
                    batch.status = "done"
                    batch.finished_at = utc_now()
                    batch.updated_at = utc_now()
                    batch.error = None
                    session.commit()
                return
            job = session.get(JobRecord, job_id)
            if job and (
                expected_attempts is None
                or (job.status == "processing" and job.attempts == int(expected_attempts))
            ):
                job.status = "done"
                job.finished_at = utc_now()
                job.error = None
                session.commit()

    def fail_job(
        self,
        job_id: int,
        error: str,
        retry: bool = True,
        expected_attempts: int | None = None,
    ) -> None:
        with Session(self.engine) as session:
            if job_id < 0:
                batch = session.get(MessageBatchRecord, -job_id)
                if batch and (
                    expected_attempts is None
                    or (batch.status == "processing" and batch.attempts == int(expected_attempts))
                ):
                    batch.status = "pending" if retry and batch.attempts < 3 else "failed"
                    batch.error = error[:4000]
                    batch.available_at = utc_now() + timedelta(seconds=min(30, max(1, 2**batch.attempts)))
                    batch.updated_at = utc_now()
                    if batch.status == "failed":
                        batch.finished_at = utc_now()
                    session.commit()
                return
            job = session.get(JobRecord, job_id)
            if job and (
                expected_attempts is None
                or (job.status == "processing" and job.attempts == int(expected_attempts))
            ):
                job.status = "pending" if retry and job.attempts < 3 else "failed"
                job.error = error[:4000]
                job.available_at = utc_now()
                if job.status == "failed":
                    job.finished_at = utc_now()
                session.commit()

    def audit(self, event_type: str, subject: str | None, detail: dict | None = None) -> None:
        with Session(self.engine) as session:
            session.add(AuditEventRecord(event_type=event_type, subject=subject, detail=detail or {}))
            session.commit()

    def audit_error_summary(self, hours: int = 24) -> dict[str, Any]:
        since = utc_now() - timedelta(hours=max(1, int(hours)))
        with Session(self.engine) as session:
            rows = session.execute(
                select(AuditEventRecord.event_type, func.count(AuditEventRecord.id))
                .where(
                    AuditEventRecord.created_at >= since,
                    AuditEventRecord.event_type.ilike("%error%"),
                )
                .group_by(AuditEventRecord.event_type)
                .order_by(func.count(AuditEventRecord.id).desc(), AuditEventRecord.event_type)
            ).all()
            latest = session.scalar(
                select(AuditEventRecord)
                .where(
                    AuditEventRecord.created_at >= since,
                    AuditEventRecord.event_type.ilike("%error%"),
                )
                .order_by(AuditEventRecord.created_at.desc(), AuditEventRecord.id.desc())
                .limit(1)
            )
        return {
            "window_hours": max(1, int(hours)),
            "count": sum(int(total) for _event_type, total in rows),
            "by_type": {str(event_type): int(total) for event_type, total in rows},
            "last_event": (
                {
                    "event_type": latest.event_type,
                    "created_at": latest.created_at,
                }
                if latest is not None
                else None
            ),
        }

    def list_audit_events(
        self,
        *,
        limit: int = 50,
        event_type: str | None = None,
    ) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 200))
        with Session(self.engine) as session:
            statement = select(AuditEventRecord).order_by(
                AuditEventRecord.created_at.desc(), AuditEventRecord.id.desc()
            )
            if event_type:
                statement = statement.where(AuditEventRecord.event_type == event_type)
            records = session.scalars(statement.limit(safe_limit)).all()
            return [
                {
                    "id": int(record.id),
                    "event_type": record.event_type,
                    "subject": record.subject,
                    "detail": dict(record.detail or {}),
                    "created_at": record.created_at,
                }
                for record in records
            ]

    def cleanup(self, older_than: datetime) -> dict[str, int]:
        with Session(self.engine) as session:
            old_conversations = session.scalars(
                select(ConversationRecord).where(ConversationRecord.updated_at < older_than)
            ).all()
            old_events = session.scalars(
                select(InboundEventRecord).where(InboundEventRecord.received_at < older_than)
            ).all()
            old_audits = session.scalars(select(AuditEventRecord).where(AuditEventRecord.created_at < older_than)).all()
            old_batches = session.scalars(
                select(MessageBatchRecord).where(MessageBatchRecord.updated_at < older_than)
            ).all()
            old_conversation_ids = [int(record.id) for record in old_conversations]
            if old_conversation_ids:
                session.execute(
                    delete(RecoverySkipRecord).where(
                        RecoverySkipRecord.conversation_id.in_(old_conversation_ids)
                    )
                )
                session.execute(
                    delete(RecoverySendClaimRecord).where(
                        RecoverySendClaimRecord.conversation_id.in_(old_conversation_ids)
                    )
                )
            for record in old_conversations:
                session.delete(record)
            for record in old_events:
                session.delete(record)
            for record in old_audits:
                session.delete(record)
            for record in old_batches:
                session.delete(record)
            session.commit()
            return {
                "conversations": len(old_conversations),
                "events": len(old_events),
                "audits": len(old_audits),
                "batches": len(old_batches),
            }
