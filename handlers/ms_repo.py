from typing import Set, List
from sqlalchemy.orm import Session
from sqlalchemy import select, delete
from _database import MultiSelect
from handlers.options_repo import get_label_for

def get_selected_set(s: Session, user_id: int, qkey: str) -> Set[str]:
    rows = s.execute(
        select(MultiSelect.value).where(
            MultiSelect.user_id == user_id,
            MultiSelect.question_key == qkey,
        )
    ).all()
    return {r[0] for r in rows}

def toggle_selection(s: Session, user_id: int, qkey: str, value: str) -> None:
    row = s.query(MultiSelect).filter_by(
        user_id=user_id, question_key=qkey, value=value
    ).first()
    if row:
        s.delete(row)
    else:
        s.add(MultiSelect(user_id=user_id, question_key=qkey, value=value))

def clear_selection(s: Session, user_id: int, qkey: str) -> None:
    s.execute(
        delete(MultiSelect).where(
            MultiSelect.user_id == user_id,
            MultiSelect.question_key == qkey,
        )
    )

def build_answer_text_from_selection(
    s: Session, user_id: int, qkey: str
) -> str:
    """Собираем красивый текст для сохранения в answers: по выбранным value подставляем label."""
    selected = list(get_selected_set(s, user_id, qkey))
    labels: List[str] = [get_label_for(s, qkey, v, user_id) for v in selected]
    labels = [lab for lab in labels if lab]  # safety
    return ", ".join(labels)