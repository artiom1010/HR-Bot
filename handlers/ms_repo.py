from typing import Set
from sqlalchemy.orm import Session
from sqlalchemy import select, delete
from _database import MultiSelect

from handlers.options_repo import get_label_for
from _utils import CHOICES


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


# def build_answer_text_from_selection(s, user_id: int, qkey: str) -> str:
#     values = list(get_selected_set(s, user_id, qkey) or [])

#     base_key = qkey.split("::", 1)[1] if ("::" in qkey) else qkey

#     labels = []
#     for v in values:
#         lab = get_label_for(s, base_key, v, user_id)
#         if not lab:
#             lab = dict(CHOICES.get(base_key, [])).get(v, v)
#         labels.append(lab)

#     return ", ".join(labels)

from i18n import t
def build_answer_text_from_selection(s, user_id: int, qkey: str, lang: str = "ru") -> str:
    values = list(get_selected_set(s, user_id, qkey) or [])

    base_key = qkey.split("::", 1)[1] if ("::" in qkey) else qkey

    labels = []
    for v in values:
        lab = get_label_for(s, base_key, v, user_id)
        if not lab:
            lab = dict(CHOICES.get(base_key, [])).get(v, v)
        
        # ✅ ПЕРЕВОДИМ LABEL!
        labels.append(t(lab, lang))

    return ", ".join(labels)