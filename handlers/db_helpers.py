from sqlalchemy.orm import Session
from _database import User, Answer, Order, PaynetStatus, ProcessedData

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