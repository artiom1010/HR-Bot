import datetime as dt
from enum import Enum, unique

from sqlalchemy import (
    create_engine,
    Column, Integer, BigInteger, String, DateTime,
    ForeignKey, Text, Enum as PgEnum, UniqueConstraint
)
from sqlalchemy.orm import declarative_base, relationship


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



class AnswerOption(Base):
    __tablename__ = "answer_options"

    id           = Column(Integer, primary_key=True)
    question_key = Column(String(32),  nullable=False, index=True)  # напр. 'resume_language', 'strengths'
    value        = Column(String(64),  nullable=False, index=True)  # slug для callback_data
    label        = Column(String(255), nullable=False)              # текст на кнопке

    user_id   = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)  # NULL = глобальная
    lang      = Column(String(8),   nullable=True)          # 'ru' / 'en' / 'ro' (если нужно)
    position  = Column(String(128), nullable=True)          # нормализованная должность (для персональных)
    source    = Column(String(16),  nullable=False, default="static")  # static / ai / manual

    created_at = Column(DateTime, default=dt.datetime.utcnow, nullable=False)

    __table_args__ = (
    UniqueConstraint('user_id', 'question_key', 'value', 'source',
                         name='uix_answer_options_user_qkey_value_source'),
    )

# ───────────────── ЧЕРНОВИКИ (подвопросы) ─────────────────
class Draft(Base):
    __tablename__ = "drafts"

    id         = Column(Integer, primary_key=True)
    user_id    = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    parent_key = Column(String(32), index=True, nullable=False)   # напр. 'summary'
    step_key   = Column(String(32), nullable=False)               # напр. 'summary_industry'
    value      = Column(Text, nullable=False)
    created_at = Column(DateTime, default=dt.datetime.utcnow)

    user = relationship("User")

# ───────────────── МУЛЬТИВЫБОР ─────────────────
class MultiSelect(Base):
    __tablename__ = "multi_select"

    id           = Column(Integer, primary_key=True)
    user_id      = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    question_key = Column(String(32),  nullable=False, index=True)  # напр. 'skills'
    value        = Column(String(64),  nullable=False)              # slug из options (<=64 байт)
    created_at   = Column(DateTime, default=dt.datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "question_key", "value", name="uq_ms_user_key_value"),
    )


class StepLog(Base):
    """Лог прохождения шагов — для статистики воронки"""
    __tablename__ = "step_logs"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    step_key = Column(String(64), nullable=False, index=True)
    created_at = Column(DateTime, default=dt.datetime.utcnow, index=True)

    __table_args__ = (
        UniqueConstraint("user_id", "step_key", name="uq_step_log_user_step"),
    )



# ── 3. Создать таблицы ──────────────────────────────────────────────
Base.metadata.create_all(engine)
print("SQLite-схема создана.")