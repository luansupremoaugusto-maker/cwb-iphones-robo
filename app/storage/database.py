from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import JSON, Column, DateTime, ForeignKey, Integer, String, Text, create_engine, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Session, relationship
from sqlalchemy.pool import StaticPool


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class ConversationRecord(Base):
    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True)
    phone = Column(String(32), unique=True, nullable=False, index=True)
    chat_name = Column(String(255), nullable=True)
    status = Column(String(32), nullable=False, default="bot_active", index=True)
    paused_reason = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)

    messages = relationship("MessageRecord", back_populates="conversation", cascade="all, delete-orphan")


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

    def healthcheck(self) -> bool:
        try:
            with Session(self.engine) as session:
                session.execute(select(1)).scalar_one()
            return True
        except Exception:
            return False

    def get_or_create_conversation(self, phone: str, chat_name: str | None = None) -> ConversationRecord:
        with Session(self.engine, expire_on_commit=False) as session:
            record = session.scalar(select(ConversationRecord).where(ConversationRecord.phone == phone))
            if record is None:
                record = ConversationRecord(phone=phone, chat_name=chat_name, status="bot_active")
                session.add(record)
            elif chat_name and not record.chat_name:
                record.chat_name = chat_name
            record.updated_at = utc_now()
            session.commit()
            return record

    def get_conversation(self, phone: str) -> ConversationRecord | None:
        with Session(self.engine, expire_on_commit=False) as session:
            return session.scalar(select(ConversationRecord).where(ConversationRecord.phone == phone))

    def set_conversation_status(self, phone: str, status: str, reason: str | None = None) -> None:
        with Session(self.engine) as session:
            record = session.scalar(select(ConversationRecord).where(ConversationRecord.phone == phone))
            if record is None:
                record = ConversationRecord(phone=phone, status=status, paused_reason=reason)
                session.add(record)
            else:
                record.status = status
                record.paused_reason = reason
                record.updated_at = utc_now()
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
            result = session.execute(
                update(ConversationRecord)
                .where(
                    ConversationRecord.phone == phone,
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
        with Session(self.engine) as session:
            conversation = session.scalar(select(ConversationRecord).where(ConversationRecord.phone == phone))
            if conversation is None:
                conversation = ConversationRecord(phone=phone, status="bot_active")
                session.add(conversation)
                session.flush()
            record = MessageRecord(
                conversation_id=conversation.id,
                direction=direction,
                kind=kind,
                text=text,
                provider_message_id=provider_message_id,
                raw=raw or {},
            )
            session.add(record)
            conversation.updated_at = utc_now()
            session.commit()
            return int(record.id)

    def recent_messages(self, phone: str, limit: int = 20) -> list[dict[str, str]]:
        with Session(self.engine) as session:
            conversation = session.scalar(select(ConversationRecord).where(ConversationRecord.phone == phone))
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
