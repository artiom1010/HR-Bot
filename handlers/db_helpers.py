from sqlalchemy.orm import Session
from sqlalchemy import select, func
from _database import User, Answer, Order, PaynetStatus, ProcessedData, Draft


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

def save_answer(session: Session, user_id: int, question_key: str, answer_text: str) -> None:
    answer = Answer(user_id=user_id, question_key=question_key, answer=answer_text)
    session.add(answer)
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

def mark_order_paid(session: Session, order_id: int) -> None:
    order = session.get(Order, order_id)
    if order:
        order.paynet_status = PaynetStatus.paid
        session.commit()


def clear_user_answers(session: Session, user_id: int) -> int:
    """Удаляет все ответы пользователя. Возвращает сколько удалили."""
    deleted = session.query(Answer).filter(Answer.user_id == user_id)\
                    .delete(synchronize_session=False)
    session.commit()
    return deleted

def clear_user_drafts(session: Session, user_id: int) -> None:
    """
    Опционально: чистим незавершённые заказы и связанные processed_data,
    чтобы точно не было «хвостов».
    """
    draft_orders = session.query(Order).filter(
        Order.user_id == user_id,
        Order.paynet_status.in_([PaynetStatus.created, PaynetStatus.pending])
    ).all()
    for o in draft_orders:
        session.query(ProcessedData).filter(ProcessedData.order_id == o.id)\
               .delete(synchronize_session=False)
        session.delete(o)
    session.commit()




# ==============
# DRAFTS HELPER
# ==============
def drafts_count(s, user_id: int, parent_key: str) -> int:
    return s.execute(
        select(func.count()).select_from(Draft)
        .where(Draft.user_id == user_id, Draft.parent_key == parent_key)
    ).scalar_one()

def add_draft(s, user_id: int, parent_key: str, step_key: str, value: str):
    s.add(Draft(user_id=user_id, parent_key=parent_key, step_key=step_key, value=value))

def get_drafts_values_ordered(s, user_id: int, parent_key: str) -> list[str]:
    rows = s.execute(
        select(Draft).where(Draft.user_id == user_id, Draft.parent_key == parent_key)
        .order_by(Draft.id.asc())
    ).scalars().all()
    return [r.value.strip() for r in rows if r.value and r.value.strip()]

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






# ==============
# PERSONAL WAIT
# ==============
# Флаг ожидания личного ввода для подшага (parent='summary')
def set_personal_wait(s, user_id: int, step_key: str):
    # используем drafts как KV-хранилище: parent_key='summary', step_key='<subkey>__await'
    replace_draft(s, user_id, parent_key="summary", step_key=f"{step_key}__await", value="1")


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


def pop_personal_wait(s, user_id: int) -> str | None:
    # вернёт step_key ('summary_tasks' или 'summary_values'), если ждём текст
    rows = get_drafts_by_parent(s, user_id, "summary")
    for r in rows:
        if r.step_key.endswith("__await"):
            base = r.step_key[:-8]  # отрезать "__await"
            # удалить флаг
            delete_draft_by_key(s, user_id, "summary", r.step_key)
            return base
    return None