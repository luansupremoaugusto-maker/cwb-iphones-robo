from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    ForeignKey,
    Integer,
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
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import DeclarativeBase, Session, relationship
from sqlalchemy.pool import StaticPool

from app.config import normalize_phone, phone_variants


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

    id = Column(Integer, primary_key=True)
    external_id = Column(String(255), unique=True, nullable=False, index=True)
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
        Base.metadata.create_all(self.engine)
        inspector = sqlalchemy_inspect(self.engine)
        columns = {column["name"] for column in inspector.get_columns("conversations")}
        if "protocol" not in columns:
            try:
                with self.engine.begin() as connection:
                    connection.execute(text("ALTER TABLE conversations ADD COLUMN protocol VARCHAR(32)"))
            except OperationalError:
                # Two app processes can initialize the same database during a
                # deployment. Re-read before surfacing a genuinely unrelated
                # schema error.
                columns = {
                    column["name"]
                    for column in sqlalchemy_inspect(self.engine).get_columns("conversations")
                }
                if "protocol" not in columns:
                    raise

        with Session(self.engine) as session:
            records = session.scalars(select(ConversationRecord).order_by(ConversationRecord.id)).all()
            for record in records:
                if not record.protocol:
                    record.protocol = conversation_protocol(record.id)
            session.commit()

        # create_all does not add indexes to an already existing table.
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_conversations_protocol "
                    "ON conversations (protocol)"
                )
            )

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

    def recent_messages(self, phone: str, limit: int = 20) -> list[dict[str, str]]:
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
            return [
                {"role": "user" if item.direction == "inbound" else "assistant", "content": item.text}
                for item in reversed(records)
                if item.text
            ]

    def register_inbound_event(self, external_id: str, payload: dict) -> tuple[int, bool]:
        with Session(self.engine, expire_on_commit=False) as session:
            existing = session.scalar(select(InboundEventRecord).where(InboundEventRecord.external_id == external_id))
            if existing is not None:
                return int(existing.id), False
            record = InboundEventRecord(external_id=external_id, payload=payload)
            session.add(record)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                existing = session.scalar(select(InboundEventRecord).where(InboundEventRecord.external_id == external_id))
                if existing is None:
                    raise
                return int(existing.id), False
            return int(record.id), True

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
        if phone is None and debounce_seconds <= 0:
            with Session(self.engine, expire_on_commit=False) as session:
                job = JobRecord(event_id=event_id, status="pending", available_at=utc_now())
                session.add(job)
                session.commit()
                return int(job.id)

        due_at = utc_now() + timedelta(seconds=max(0, int(debounce_seconds)))
        batch_phone = phone or f"event:{event_id}"
        with Session(self.engine, expire_on_commit=False) as session:
            existing_link = session.scalar(select(BatchEventRecord).where(BatchEventRecord.event_id == event_id))
            if existing_link is not None:
                return int(existing_link.batch_id)
            batch = session.scalar(
                select(MessageBatchRecord)
                .where(MessageBatchRecord.phone == batch_phone, MessageBatchRecord.status == "pending")
                .order_by(MessageBatchRecord.id.desc())
                .limit(1)
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
            session.commit()
            return int(batch.id)

    def _claim_legacy_job(self, session: Session, now: datetime) -> tuple[int, dict] | None:
        job = session.scalar(
            select(JobRecord)
            .where(JobRecord.status == "pending", JobRecord.available_at <= now)
            .order_by(JobRecord.id)
            .limit(1)
        )
        if job is None:
            return None
        job.status = "processing"
        job.attempts += 1
        event = session.get(InboundEventRecord, job.event_id)
        session.commit()
        return int(job.id), dict(event.payload) if event is not None else {}

    def claim_next_job(self) -> tuple[int, dict] | None:
        with Session(self.engine, expire_on_commit=False) as session:
            now = utc_now()
            legacy = self._claim_legacy_job(session, now)
            if legacy is not None:
                return legacy
            batch = session.scalar(
                select(MessageBatchRecord)
                .where(MessageBatchRecord.status == "pending", MessageBatchRecord.available_at <= now)
                .order_by(MessageBatchRecord.available_at, MessageBatchRecord.id)
                .limit(1)
            )
            if batch is None:
                return None
            batch.status = "processing"
            batch.attempts += 1
            batch.updated_at = now
            links = session.scalars(
                select(BatchEventRecord)
                .where(BatchEventRecord.batch_id == batch.id)
                .order_by(BatchEventRecord.id)
            ).all()
            payloads = [dict(link.event.payload) for link in links if link.event is not None]
            session.commit()
            if len(payloads) == 1:
                payload: dict = payloads[0]
            else:
                payload = {"_batch_payloads": payloads}
            return -int(batch.id), payload

    def finish_job(self, job_id: int) -> None:
        with Session(self.engine) as session:
            if job_id < 0:
                batch = session.get(MessageBatchRecord, -job_id)
                if batch:
                    batch.status = "done"
                    batch.finished_at = utc_now()
                    batch.updated_at = utc_now()
                    batch.error = None
                    session.commit()
                return
            job = session.get(JobRecord, job_id)
            if job:
                job.status = "done"
                job.finished_at = utc_now()
                job.error = None
                session.commit()

    def fail_job(self, job_id: int, error: str, retry: bool = True) -> None:
        with Session(self.engine) as session:
            if job_id < 0:
                batch = session.get(MessageBatchRecord, -job_id)
                if batch:
                    batch.status = "pending" if retry and batch.attempts < 3 else "failed"
                    batch.error = error[:4000]
                    batch.available_at = utc_now() + timedelta(seconds=min(30, max(1, 2**batch.attempts)))
                    batch.updated_at = utc_now()
                    if batch.status == "failed":
                        batch.finished_at = utc_now()
                    session.commit()
                return
            job = session.get(JobRecord, job_id)
            if job:
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
