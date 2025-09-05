# handlers/options_repo.py
"""
Единая точка работы с вариантами кнопок (опциями).
- get_options: вернуть [(value, label)] для вопроса
- replace_user_ai_options: сохранить 7–8 персональных опций от ИИ для пользователя
- normalize_position / get_user_context: утилиты для контекста
"""
import re
from typing import List, Optional

from sqlalchemy.orm import Session
from sqlalchemy import or_

from _database import engine, Answer, AnswerOption
from _utils import CHOICES as STATIC_CHOICES 
from _utils import CHOICES





# ───────────────────────────
# УТИЛИТЫ
# ───────────────────────────

def normalize_position(text: Optional[str]) -> Optional[str]:
    """Приводим должность к стабильному виду (ключ для фильтров/кэша)."""
    if not text:
        return None
    t = text.strip().lower()
    # сжать пробелы
    t = re.sub(r"\s+", " ", t)
    return t or None


def get_user_context(user_id: int) -> dict:
    """
    Достаёт из answers минимальный контекст:
    - resume_language (то, что вы храните как answer; если это label — ок)
    - target_position (как ввёл пользователь)
    Вернёт: {"lang": <str|None>, "position": <str|None>}
    """
    with Session(engine) as s:
        ans = s.query(Answer).filter(
            Answer.user_id == user_id,
            Answer.question_key.in_(["resume_language", "target_position"])
        ).all()
        ctx = {"lang": None, "position": None}
        for a in ans:
            if a.question_key == "resume_language":
                ctx["lang"] = a.answer
            elif a.question_key == "target_position":
                ctx["position"] = a.answer
        return ctx





# ───────────────────────────
# ОСНОВНОЕ API
# ───────────────────────────

def get_options(question_key: str, user_id: int | None = None,
                lang: str | None = None, position: str | None = None) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    with Session(engine) as s:
        if user_id is not None:
            personal = (s.query(AnswerOption)
                        .filter(AnswerOption.user_id == user_id,
                                AnswerOption.question_key == question_key,
                                AnswerOption.source.in_(["ai", "manual"]))
                        .order_by(AnswerOption.id.asc())
                        .all())
            if personal:
                return [(r.value, r.label) for r in personal]

        qg = (s.query(AnswerOption)
              .filter(AnswerOption.user_id.is_(None),
                      AnswerOption.question_key == question_key,
                      or_(AnswerOption.source == "static", AnswerOption.source.is_(None))))
        if lang in ("ru", "en", "ro"):
            qg = qg.filter(AnswerOption.lang == lang)
        global_rows = qg.order_by(AnswerOption.id.asc()).all()
        if global_rows:
            return [(r.value, r.label) for r in global_rows]

    return STATIC_CHOICES.get(question_key, [])



def replace_user_ai_options(
    user_id: int,
    question_key: str,
    items: List[dict],
    *,
    lang: Optional[str] = None,
    position: Optional[str] = None,
) -> int:
    pos_norm = normalize_position(position)

    with Session(engine) as s:
        qdel = s.query(AnswerOption).filter(
            AnswerOption.user_id == user_id,
            AnswerOption.question_key == question_key,
            AnswerOption.source == "ai",
        ).delete(synchronize_session=False)

        inserted = 0
        for it in items:
            val = it.get("value")
            lab = it.get("label")
            if not val or not lab:
                continue
            row = AnswerOption(
                question_key=question_key,
                value=str(val),
                label=str(lab),
                user_id=user_id,
                lang=lang,
                position=pos_norm if position else None,
                source="ai",
            )
            s.add(row)
            inserted += 1

        s.commit()
        return inserted
    



def get_label_for(s: Session, question_key: str, value: str, user_id: int | None = None) -> str:
    row = s.query(AnswerOption).filter(
        AnswerOption.question_key == question_key,
        AnswerOption.value == value,
        AnswerOption.user_id == user_id
    ).first()
    if row:
        return row.label

    row = s.query(AnswerOption).filter(
        AnswerOption.question_key == question_key,
        AnswerOption.value == value,
        AnswerOption.user_id.is_(None)
    ).first()
    if row:
        return row.label
    
    return dict(CHOICES.get(question_key, [])).get(value, value)