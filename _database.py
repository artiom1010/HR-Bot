import datetime as dt
from enum import Enum, unique

from sqlalchemy import (
    create_engine,
    Column, Integer, BigInteger, String, DateTime,
    ForeignKey, Text, Enum as PgEnum, CheckConstraint
)
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.pool import StaticPool

engine = create_engine("sqlite:///./resume_bot.db", echo=False, future=True)

Base   = declarative_base()

# ───────────────── ПОЛЬЗОВАТЕЛИ ─────────────────
class User(Base):
    __tablename__ = "users"

    id          = Column(Integer, primary_key=True)
    telegram_id = Column(BigInteger, unique=True, nullable=False)
    username    = Column(String(64))
    full_name   = Column(String(128))
    created_at  = Column(DateTime, default=dt.datetime.utcnow)

    answers  = relationship("Answer",  back_populates="user", cascade="all, delete")
    orders   = relationship("Order",   back_populates="user", cascade="all, delete")

# ───────────────── ОТВЕТЫ ─────────────────
class Answer(Base):
    __tablename__ = "answers"

    id           = Column(Integer, primary_key=True)
    user_id      = Column(Integer, ForeignKey("users.id"), nullable=False)
    question_key = Column(String(32),  nullable=False)
    answer       = Column(Text)
    answered_at  = Column(DateTime, default=dt.datetime.utcnow)

    user = relationship("User", back_populates="answers")

# ───────────────── ДАННЫЕ ОТ ИИ ─────────────────
class ProcessedData(Base):
    """
    Данные, которые вернул ChatGPT после обработки анкеты.
    Привязаны к конкретному заказу (order_id).
    Поля названы 1-в-1 как ключи словаря, чтобы без маппинга ложить .json() → row.
    """
    __tablename__ = "processed_data"

    id        = Column(Integer, primary_key=True)
    order_id  = Column(Integer, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime, default=dt.datetime.utcnow)

    # ---------- фиксированные ключи ----------
    cv_naming            = Column(String(128))
    full_name            = Column(String(128))
    contact_phone        = Column(String(64))
    contact_email        = Column(String(128))
    contact_links        = Column(Text)      # строка / JSON со списком
    
    career_summary       = Column(Text)
    experiences_json = Column(Text, nullable=True)
    education_details    = Column(Text)
    languages            = Column(Text)

    hard_skills          = Column(Text)
    soft_skills          = Column(Text)

    # связь назад к заказу
    order = relationship("Order", back_populates="processed_data")


# ───────────────── ОПЛАТА ────────────────────────
@unique
class PaynetStatus(Enum):
    created  = "created"      # анкета собрана
    pending  = "pending"      # счёт выписан, ждём
    paid     = "paid"
    cancelled= "cancelled"
    error    = "error"        # не удалось выписать

class Order(Base):
    __tablename__ = "orders"

    id              = Column(Integer, primary_key=True)
    user_id         = Column(Integer, ForeignKey("users.id"), nullable=False)

    # Paynet
    invoice         = Column(String(50),  unique=True, nullable=False)
    payment_id      = Column(BigInteger, index=True)
    paynet_status   = Column(PgEnum(PaynetStatus), default=PaynetStatus.created, nullable=False)

    amount_bani      = Column(Integer, nullable=False)
    created_at      = Column(DateTime, default=dt.datetime.utcnow)
    paid_at         = Column(DateTime)

    user            = relationship("User", back_populates="orders")
    processed_data  = relationship("ProcessedData", back_populates="order",
                                   cascade="all, delete-orphan", uselist=False)

class PaymentLog(Base):
    __tablename__ = "payment_logs"

    id               = Column(Integer, primary_key=True)
    order_id         = Column(Integer, ForeignKey("orders.id"), nullable=False)
    x_correlation_id = Column(String(64))
    json_body        = Column(Text)          # полный ответ Paynet
    created_at       = Column(DateTime, default=dt.datetime.utcnow)

    order = relationship("Order", backref="payment_logs")

# ── 3. Создать таблицы ──────────────────────────────────────────────
Base.metadata.create_all(engine)
print("SQLite-схема создана.")