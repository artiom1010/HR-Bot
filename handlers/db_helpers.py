from sqlalchemy.orm import Session
from sqlalchemy import select, func
from _database import (
    User, Answer, Order, PaynetStatus,
    ProcessedData, Draft, AnswerOption, StepLog
    )


# ========================
# USERS & ORDERS
# ========================
def get_or_create_user(session: Session, telegram_id: int, username: str, full_name: str) -> User:
    user = session.query(User).filter(User.telegram_id == telegram_id).first()
    if not user:
        user = User(telegram_id=telegram_id, username=username, full_name=full_name)
        session.add(user)
        session.commit()
    return user


def get_first_unanswered(session: Session, user_id: int, question_flow: list[tuple[str, str]]) -> str | None:
    answered_keys = {
        a.question_key for a in
        session.query(Answer.question_key)
               .filter(Answer.user_id == user_id).all()
    }
    for key, _ in question_flow:
        if key not in answered_keys:
            return key
    return None


# def save_answer(session: Session, user_id: int, question_key: str, answer_text: str) -> None:
#     answer = Answer(user_id=user_id, question_key=question_key, answer=answer_text)
#     session.add(answer)
#     log_step(session, user_id, question_key, answer_text)
#     session.commit()
def save_answer(session: Session, user_id: int, question_key: str, answer_text: str) -> None:
    # Проверяем, есть ли уже ответ на этот вопрос
    existing = session.query(Answer).filter_by(
        user_id=user_id, 
        question_key=question_key
    ).first()
    
    if existing:
        existing.answer = answer_text  # Обновляем
    else:
        answer = Answer(user_id=user_id, question_key=question_key, answer=answer_text)
        session.add(answer)  # Создаём новый
    
    log_step(session, user_id, question_key, answer_text)
    session.commit()


def create_order(session: Session, user_id: int, invoice: str, amount_bani: int) -> Order:
    order = Order(
        user_id=user_id,
        invoice=invoice,
        amount_bani=amount_bani,
        paynet_status=PaynetStatus.created
    )
    session.add(order)
    session.commit()
    return order

from datetime import datetime
def mark_order_paid(session: Session, order_id: int) -> None:
    order = session.get(Order, order_id)
    if order:
        order.paynet_status = PaynetStatus.paid
        order.paid_at = datetime.utcnow()
        session.commit()


def clear_user_answers(session: Session, user_id: int) -> int:
    """Удаляет все ответы пользователя. Возвращает сколько удалили."""
    deleted = session.query(Answer).filter(Answer.user_id == user_id)\
                    .delete(synchronize_session=False)
    session.commit()
    return deleted


def clear_user_drafts(session: Session, user_id: int) -> None:
    """Полная очистка пользовательских черновиков (Draft) + опционально хвостов заказов."""
    session.query(Draft).filter(Draft.user_id == user_id)\
        .delete(synchronize_session=False)

    draft_orders = session.query(Order).filter(
        Order.user_id == user_id,
        Order.paynet_status.in_([PaynetStatus.created, PaynetStatus.created])
    ).all()
    for o in draft_orders:
        session.query(ProcessedData).filter(ProcessedData.order_id == o.id)\
               .delete(synchronize_session=False)
        session.delete(o)
    session.commit()

def clear_user_answer_options(session: Session, user_id: int) -> int:
    deleted = session.query(AnswerOption).filter(AnswerOption.user_id == user_id)\
        .delete(synchronize_session=False)
    session.commit()
    return deleted



# ==============
# DRAFTS HELPER
# ==============
def drafts_count(s, user_id: int, parent_key: str) -> int:
    q = select(func.count()).select_from(Draft).where(
        Draft.user_id == user_id,
        Draft.parent_key == parent_key
    )
    if parent_key == "summary":
        q = q.where(~Draft.step_key.like("%__await"))
    return s.execute(q).scalar_one()


def add_draft(s, user_id: int, parent_key: str, step_key: str, value: str):
    s.add(Draft(user_id=user_id, parent_key=parent_key, step_key=step_key, value=value))
    log_step(s, user_id, step_key, value)


def get_drafts_values_ordered(s, user_id: int, parent_key: str) -> list[str]:
    rows = s.execute(
        select(Draft)
        .where(Draft.user_id == user_id, Draft.parent_key == parent_key)
        .order_by(Draft.id.asc())
    ).scalars().all()
    out: list[str] = []
    for r in rows:
        if r.step_key.endswith("__await"):
            continue
        if r.value and r.value.strip():
            out.append(r.value.strip())
    return out

def drafts_completed_count(s, user_id: int, parent_key: str) -> int:
    return s.execute(
        select(func.count()).select_from(Draft).where(
            Draft.user_id == user_id,
            Draft.parent_key == parent_key,
            ~Draft.step_key.like("%__await")
        )
    ).scalar_one()


def clear_parent_drafts(s, user_id: int, parent_key: str):
    s.query(Draft).filter(Draft.user_id == user_id, Draft.parent_key == parent_key)\
      .delete(synchronize_session=False)
    

def replace_draft(s, user_id: int, parent_key: str, step_key: str, value: str):
    s.query(Draft).filter(
        Draft.user_id == user_id,
        Draft.parent_key == parent_key,
        Draft.step_key == step_key
    ).delete(synchronize_session=False)
    s.add(Draft(user_id=user_id, parent_key=parent_key, step_key=step_key, value=value))
    log_step(s, user_id, step_key, value)
    s.commit()


def delete_draft_by_key(s, user_id: int, parent_key: str, step_key: str):
    s.query(Draft).filter(
        Draft.user_id == user_id,
        Draft.parent_key == parent_key,
        Draft.step_key == step_key
    ).delete(synchronize_session=False)


def get_drafts_by_parent(s, user_id: int, parent_key: str):
    """Вернёт объекты Draft для данного parent_key (в порядке вставки)."""
    return s.execute(
        select(Draft).where(Draft.user_id == user_id, Draft.parent_key == parent_key)
        .order_by(Draft.id.asc())
    ).scalars().all()



# ==============
# PERSONAL WAIT
# ==============
def set_personal_wait(s, user_id: int, step_key: str):
    replace_draft(s, user_id, parent_key="summary", step_key=f"{step_key}__await", value="1")


def pop_personal_wait(s: Session, user_id: int) -> str | None:
    step = s.execute(
        select(Draft.step_key)
        .where(
            Draft.user_id == user_id,
            Draft.parent_key == "summary",
            Draft.step_key.like("%__await"),
        )
        .order_by(Draft.id.asc())
    ).scalars().first()

    if not step:
        return None

    base = step[:-8]
    delete_draft_by_key(s, user_id, "summary", step)
    return base



# ==============
# FREE TEXT WAIT
# ==============
def set_free_wait(s: Session, user_id: int, qkey: str):
    """Ставим флаг: ждём свободный текст для конкретного ключа (не только summary_*)."""
    replace_draft(s, user_id, parent_key="__free__", step_key=qkey, value="1")


def pop_free_wait(s: Session, user_id: int) -> str | None:
    """Снимаем и возвращаем ключ, для которого ждали свободный ввод."""
    rows = get_drafts_by_parent(s, user_id, "__free__")
    for r in rows:
        step = r.step_key
        delete_draft_by_key(s, user_id, "__free__", step)
        return step
    return None


def get_draft_value(s: Session, user_id: int, parent_key: str, step_key: str) -> str | None:
    row = s.execute(
        select(Draft.value)
        .where(Draft.user_id == user_id,
               Draft.parent_key == parent_key,
               Draft.step_key == step_key)
        .order_by(Draft.id.desc())
    ).scalars().first()
    return row



# ==============
# USER LANGUAGE
# ==============
LANG_MAP = {
    "🇷🇺 Русский": "ru",
    "🇬🇧 English": "en",
    "🇷🇴 Română": "ro",
}

def get_user_lang(s: Session, user_id: int) -> str:
    """Возвращает код языка пользователя (ru/en/ro). По умолчанию ru."""
    answer = s.query(Answer).filter_by(
        user_id=user_id,
        question_key="main_language"
    ).first()

    if not answer:
        return "ru"
    return LANG_MAP.get(answer.answer, "ru")


def get_resume_lang(s: Session, user_id: int) -> str:
    """Возвращает язык резюме пользователя (ru/en/ro). По умолчанию ru."""
    answer = s.query(Answer).filter_by(
        user_id=user_id,
        question_key="resume_language"
    ).first()
    if not answer or not answer.answer:
        return "ru"
    code = answer.answer.split()[0].lower()
    return code if code in ("ru", "en", "ro") else "ru"



def log_step(s: Session, user_id: int, step_key: str, value: str = None):
    """Записывает что пользователь прошёл шаг (один раз на пользователя)"""
    
    # Пропускаем если значение пустое
    if value is not None and not value.strip():
        return
    
    # Пропускаем служебные ключи
    if step_key.startswith("ms::"):
        return
    if step_key.startswith("addown::"):
        return
    if step_key.endswith("__tmp_name"):
        return
    if step_key.endswith("__await"):
        return
    
    # Пропускаем маркеры завершения этапов
    SKIP_KEYS = {
        "summary", 
        "experience", 
        "education", 
        "courses", 
        "skills", 
        "languages",
    }
    
    if step_key in SKIP_KEYS:
        return
    
    # Пропускаем experience_1, experience_2, education_1, education_2 и т.д.
    if step_key.startswith("experience_") and step_key[-1].isdigit():
        return
    if step_key.startswith("education_") and step_key[-1].isdigit():
        return
    
    # Убираем префиксы типа "exp1::", "edu1::"
    if "::" in step_key:
        step_key = step_key.split("::")[-1]
    
    existing = s.query(StepLog).filter_by(user_id=user_id, step_key=step_key).first()
    if not existing:
        s.add(StepLog(user_id=user_id, step_key=step_key))
