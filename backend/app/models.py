import uuid
from datetime import date, datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default="user")  # user | admin
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | active
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    chats: Mapped[list["Chat"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Chat(Base):
    __tablename__ = "chats"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(255), default="Новый чат")
    share_token: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    # Режим чата: "normal" либо режим активного инструмента (см. app.tools)
    mode: Mapped[str] = mapped_column(String(32), default="normal")
    # Состояние активного инструмента (например, собираемый бриф задачи)
    tool_state: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    user: Mapped[User] = relationship(back_populates="chats")
    messages: Mapped[list["Message"]] = relationship(
        back_populates="chat", cascade="all, delete-orphan", order_by="Message.id"
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    chat_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(16))  # user | assistant
    content: Mapped[str] = mapped_column(Text)
    # Кнопки-действия под сообщением ассистента: [{"id", "label", "data"}].
    # Одноразовые: обнуляются после клика.
    actions: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    chat: Mapped[Chat] = relationship(back_populates="messages")
    citations: Mapped[list["Citation"]] = relationship(
        back_populates="message", cascade="all, delete-orphan", order_by="Citation.ordinal"
    )


class Citation(Base):
    """Цитата [n] в ответе ассистента.

    Метаданные источника денормализованы, чтобы старые чаты показывали
    «Источник удален» с названием, даже когда файл/исследование уже стерты.
    """

    __tablename__ = "citations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), index=True)
    ordinal: Mapped[int] = mapped_column()
    study_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    file_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    study_title: Mapped[str] = mapped_column(String(512), default="")
    conducted_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    file_name: Mapped[str] = mapped_column(String(512), default="")
    file_kind: Mapped[str] = mapped_column(String(16), default="")
    locator: Mapped[dict] = mapped_column(JSONB, default=dict)
    snippet: Mapped[str] = mapped_column(Text, default="")

    message: Mapped[Message] = relationship(back_populates="citations")


class Study(Base):
    __tablename__ = "studies"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(512))
    conducted_at: Mapped[date] = mapped_column(Date)
    comment: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    files: Mapped[list["File"]] = relationship(
        back_populates="study", cascade="all, delete-orphan", order_by="File.created_at"
    )


class File(Base):
    __tablename__ = "files"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    study_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("studies.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # report_pdf | raw_csv | audio | video
    original_name: Mapped[str] = mapped_column(String(512))
    comment: Mapped[str] = mapped_column(Text, default="")
    storage_path: Mapped[str] = mapped_column(String(1024))
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    mime: Mapped[str] = mapped_column(String(128), default="application/octet-stream")
    status: Mapped[str] = mapped_column(String(16), default="uploaded")  # uploaded | processing | ready | error
    error_message: Mapped[str] = mapped_column(Text, default="")
    meta: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    study: Mapped[Study] = relationship(back_populates="files")
    transcript: Mapped["Transcript | None"] = relationship(
        back_populates="file", cascade="all, delete-orphan", uselist=False
    )
    chunks: Mapped[list["Chunk"]] = relationship(back_populates="file", cascade="all, delete-orphan")


class Transcript(Base):
    __tablename__ = "transcripts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    file_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("files.id", ondelete="CASCADE"), unique=True)
    language: Mapped[str] = mapped_column(String(16), default="")
    segments: Mapped[list] = mapped_column(JSONB, default=list)  # [{text, t_start, t_end}]

    file: Mapped[File] = relationship(back_populates="transcript")


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    study_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    file_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("files.id", ondelete="CASCADE"), index=True)
    content: Mapped[str] = mapped_column(Text)
    # Контекст-префикс для эмбеддинга (название исследования + комментарий файла)
    context: Mapped[str] = mapped_column(Text, default="")
    locator: Mapped[dict] = mapped_column(JSONB, default=dict)
    language: Mapped[str] = mapped_column(String(16), default="")
    embedding = mapped_column(Vector(), nullable=True)
    embedding_model: Mapped[str] = mapped_column(String(128), default="", index=True)

    file: Mapped[File] = relationship(back_populates="chunks")
