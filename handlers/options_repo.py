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
from i18n import t
def get_options(question_key: str, user_id: int | None, lang: str | None, position: str | None):
    """
    Возвращает пары (value, label) в приоритете:
    1) персональные AI-опции пользователя под конкретную position (если задана),
    2) глобальная статика из БД (source='static'),
    3) ФОЛЛБЕК: CHOICES из utils.py (если в БД пусто).
    """
    pos_norm = normalize_position(position)
    lang = lang or "ru"

    with Session(engine) as s:
        out: list[tuple[str, str]] = []
        seen: set[str] = set()

        # 1) персональные AI-опции (под конкретную роль, если передана)
        if user_id is not None:
            q = s.query(AnswerOption).filter(
                AnswerOption.question_key == question_key,
                AnswerOption.user_id == user_id,
                AnswerOption.source == "ai",
            )
            if position:
                q = q.filter(AnswerOption.position == pos_norm)
            for r in q.order_by(AnswerOption.created_at.desc()).all():
                if r.value in seen: 
                    continue
                seen.add(r.value)
                out.append((r.value, t(r.label, lang)))

        # 2) глобальная статика из БД
        for r in s.query(AnswerOption).filter(
            AnswerOption.question_key == question_key,
            AnswerOption.user_id.is_(None),
            AnswerOption.source == "static",
        ).all():
            if r.value in seen:
                continue
            seen.add(r.value)
            out.append((r.value, t(r.label, lang)))

    # 3) ФОЛЛБЕК: CHOICES (если из БД вообще ничего не набралось)
    if not out:
        # out = list(STATIC_CHOICES.get(question_key, []))
        choices = STATIC_CHOICES.get(question_key, [])
        out = [(val, t(label, lang)) for val, label in choices]
    return out




def replace_user_ai_options(
    user_id: int,
    question_key: str,
    items: List[dict],
    *,
    lang: Optional[str] = None,
    position: Optional[str] = None,
) -> int:
    """
    Полностью заменяет персональные AI-опции пользователя для данного вопроса.
    - Удаляет старые записи source='ai' для этого user_id/question_key (+lang/position, если заданы),
    - Вставляет новые из items: [{"value": "...", "label": "..."}],
    - Возвращает количество вставленных строк.

    Использовать после успешной генерации 7–8 пунктов у ИИ.
    """
    pos_norm = normalize_position(position)

    with Session(engine) as s:
        # удалить старые AI-опции в этом "контексте"
        qdel = s.query(AnswerOption).filter(
            AnswerOption.user_id == user_id,
            AnswerOption.question_key == question_key,
            AnswerOption.source == "ai",
        ).delete(synchronize_session=False)

        # вставить новые
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
    # 1) персональные
    row = s.query(AnswerOption).filter(
        AnswerOption.question_key == question_key,
        AnswerOption.value == value,
        AnswerOption.user_id == user_id
    ).first()
    if row:
        return row.label
    # 2) глобальные
    row = s.query(AnswerOption).filter(
        AnswerOption.question_key == question_key,
        AnswerOption.value == value,
        AnswerOption.user_id.is_(None)
    ).first()
    if row:
        return row.label
    # 3) фоллбек CHOICES
    return dict(CHOICES.get(question_key, [])).get(value, value)