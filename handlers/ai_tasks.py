# handlers/ai_tasks.py
import re
import json
import asyncio
import logging
import httpx
from typing import List, Dict, Optional

from sqlalchemy.orm import Session

from _database import engine, User
from _conf import DO_AGENT_URL, DO_API_TOKEN
from handlers.options_repo import (
    get_user_context,
    replace_user_ai_options,
    normalize_position,
)
from alerts import alert, alert_on_exception

log = logging.getLogger(__name__)

# =========================
# УТИЛИТЫ
# =========================

def _slugify(text: str) -> str:
    t = (text or "").strip().lower()
    t = re.sub(r"[^\w\s-]", "", t)
    t = re.sub(r"\s+", "-", t)
    t = re.sub(r"-+", "-", t)
    return t[:40] or "item"


def _clean_label(label: str, max_len: int = 32) -> str:
    lab = (label or "").replace("\n", " ").strip()
    lab = lab.strip(' "\'')
    return lab[:max_len].strip()


def _ensure_unique_7_8(items: List[Dict[str, str]]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    seen = set()
    for it in items:
        v = _slugify(it.get("value") or it.get("label", ""))
        l = _clean_label(it.get("label", ""))
        if not l or v in seen:
            continue
        seen.add(v)
        out.append({"value": v, "label": l})
        if len(out) >= 8:
            break
    if len(out) < 7:
        raise ValueError("Нужно 7–8 уникальных пунктов")
    return out[:8]


def _parse_items(raw_text: str) -> List[Dict[str, str]]:
    """Принимаем строку с JSON-массивом и возвращаем нормализованный список словарей."""
    data = json.loads(raw_text)
    if not isinstance(data, list):
        raise ValueError("Ожидался JSON-массив")
    cleaned: List[Dict[str, str]] = []
    for it in data:
        if not isinstance(it, dict):
            continue
        val = _slugify(it.get("value") or it.get("label", ""))
        lab = _clean_label(it.get("label", ""))
        if not lab:
            continue
        cleaned.append({"value": val, "label": lab})
    return _ensure_unique_7_8(cleaned)


def _map_lang_code(lang_answer: Optional[str]) -> str:
    """Грубо приводим «RU — на русском»/«ENG — ...» к ru/en/ro."""
    if not lang_answer:
        return "ru"
    la = lang_answer.lower()
    if "eng" in la or "english" in la or " en" in la or la.endswith("(eng)") or la.endswith("en"):
        return "en"
    if "ro" in la or "română" in la or " ro" in la or la.endswith("(ro)") or la.endswith("ro"):
        return "ro"
    return "ru"


def get_user_identity(user_id: int) -> tuple[str, str, int | None]:
    with Session(engine) as s:
        u = s.get(User, user_id)
        if not u:
            return "", "", None
        return (u.username or "", u.full_name or "", getattr(u, "tg_id", None))


# =========================
# ПРОМПТЫ
# =========================

def _build_tasks_prompt(position: str, lang_code: str) -> str:
    return f"""
Сгенерируй РАБОЧИЕ ЗАДАЧИ (tasks) для роли "{position}".
Язык меток (label): "{lang_code}".

ТРЕБОВАНИЯ К ВЫВОДУ
- Верни СТРОГО JSON-массив из 8 объектов без пояснений и без бэктиков.
- Каждый объект: {{"value":"<slug-en-kebab>", "label":"<короткая фраза на {lang_code}>"}}
- "label":
  • 30–65 символов, простая речь, без кавычек и эмодзи;
  • обязательно «действие + объект/контекст», а не одно слово;
  • начинай с глагола или отглагольной формы (напр. «Проводить код-ревью по чек-листу», «Поддерживать CI/CD пайплайн», «Обслуживать гостей по стандарту»).
- "value":
  • латиница, kebab-case, уникальные;
  • не отправляй по одному слову, должно быть полноценное детальное словосочетание;
  • 3–5 слов, отражающих суть действия (напр. "code-review-checklist", "maintain-ci-cd", "guest-service-standard").

ПРОВЕРКИ КАЧЕСТВА
- Запрещены однословные или слишком общие метки («Тестирование», «Оптимизация») без уточнения.
- Где уместно, добавляй лёгкий контекст («по чек-листу», «перед PR», «по стандарту смены», «в POS-системе», «по SLA»).
- Если {lang_code} неизвестен — используй русский.

ФОРМАТ ОТВЕТА (пример структуры, НЕ печатать сам пример):
[
  {{"value":"code-review-checklist","label":"Проводить код-ревью по чек-листу"}},
  {{"value":"write-unit-tests-before-pr","label":"Писать юнит-тесты перед PR"}}
]
""".strip()


def _build_values_prompt(position: str, lang_code: str) -> str:
    return f"""
Сгенерируй рабочие ЦЕННОСТИ/ПОДХОДЫ (work values & ways of working) для роли "{position}".
Язык меток (label): "{lang_code}".

ТРЕБОВАНИЯ К ВЫВОДУ
- Верни СТРОГО JSON-массив из 7 или 8 объектов без пояснений и бэктиков.
- Каждый объект: {{"value":"<slug-latin-kebab>", "label":"<короткая фраза на {lang_code}>"}}
- "label": 30–65 символов, простая речь, без кавычек и эмодзи.
- "value": латиница, kebab-case, уникальные, без префиксов.
- Не более 2 универсальных пунктов (вроде «Командная работа», «Прозрачность»).
  Минимум 5 пунктов ДОЛЖНЫ описывать поведение, характерное именно для роли "{position}".

ПРОСТОЕ ПРАВИЛО РЕЛЕВАНТНОСТИ (выполни мысленно, НЕ выводи):
1) Из названия роли "{position}" выведи 3–6 ключевых ежедневных задач, инструментов и контекста.
   Примеры направления мысли (НЕ печатать):
   • сервисные роли: гостевой сервис, скорость и порядок, чистота/санитария, касса/POS, обработка жалоб;
   • начинающий разработчик: читаемый код, мелкие коммиты, ревью/PR-дисциплина, базовые тесты, запрос фидбэка, ведение задач;
   • офисные/менеджерские: приоритизация задач, коммуникации со стейкхолдерами, контроль сроков/рисков, фиксация решений.
2) Преобразуй эти задачи в поведенческие ценности/подходы: конкретные формулировки
   («POS-операции без ошибок», «Малые фокусные коммиты ежедневно», «Чистота по чек-листу смены», «Риск-лист обновляется своевременно»).
3) Удали дубли и обобщённые синонимы. Сформируй 7–8 уникальных пунктов.

ПРОВЕРКИ КАЧЕСТВА
- Каждый "label" должен явно чувствоваться «про эту работу», а не про любую.
- Избегай пустых слов («эффективность», «ответственность») без конкретики.
- Сохраняй разнообразие: покрывай разные стороны ежедневной работы роли.

ФОРМАТ ОТВЕТА (пример структуры, НЕ печатать сам пример):
[
  {{"value":"small-focused-commits","label":"Малые фокусные коммиты ежедневно"}},
  {{"value":"pos-accuracy","label":"POS-операции без ошибок"}},
  ...
]
""".strip()



# =========================
# ЗАПРОС К ТВОЕМУ DO-АГЕНТУ
# =========================
async def ask_llm(prompt: str, *, read_timeout: float = 60.0, retries: int = 1) -> str:
    """
    Шлём промпт в DO-агент и возвращаем ЧИСТЫЙ текст JSON-массива.
    - read_timeout: таймаут на чтение (сек)
    - retries: количество повторов при сетевых/парсинг-ошибках
    """
    if not DO_AGENT_URL:
        raise RuntimeError("DO_AGENT_URL не задан в conf.py")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {DO_API_TOKEN}",
    }
    payload = {"messages": [{"role": "user", "content": prompt}], "stream": False}

    # ⬇️ делаем настраиваемый timeout и поднимаем read
    timeout = httpx.Timeout(connect=10.0, read=read_timeout, write=20.0, pool=10.0)

    last_err: Optional[Exception] = None
    for attempt in range(max(1, retries)):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    f"{DO_AGENT_URL}/api/v1/chat/completions",
                    headers=headers,
                    json=payload,
                )
            if resp.status_code != 200:
                body = resp.text[:1000]
                log.error("ask_llm HTTP %s. Body: %s", resp.status_code, body)
                resp.raise_for_status()

            j = resp.json()

            content = j["choices"][0]["message"]["content"]
            if isinstance(content, list):
                text_parts = []
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        text_parts.append(part.get("text", ""))
                    elif isinstance(part, str):
                        text_parts.append(part)
                content = "".join(text_parts).strip()

            raw = str(content).strip()
            # снимаем ```json ... ``` если есть
            cleaned = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
            cleaned = re.sub(r"\n?```$", "", cleaned).strip()
            return cleaned

        except httpx.HTTPStatusError as e:
            raise
        except (httpx.TimeoutException, httpx.RequestError, KeyError, ValueError) as e:
            last_err = e
            log.warning("ask_llm: попытка %s не удалась (%s): %s",
                        attempt + 1, type(e).__name__, str(e) or repr(e))
            await asyncio.sleep(0.8 * (attempt + 1))

    raise RuntimeError(f"ask_llm: ошибки сети/парсинга: {last_err!r}")


# =========================
# ОСНОВНЫЕ ФОНОВЫЕ ЗАДАЧИ
# =========================

@alert_on_exception(ttl=1)
async def generate_tasks_options(user_id: int) -> None:
    """
    Поток:
    1) читаем у пользователя role/lang из answers,
    2) собираем промпт, шлём в DO,
    3) парсим и валидируем (ровно 7–8 пунктов),
    4) сохраняем в answer_options как source='ai' для question_key='strengths'.
    На ошибках — лог и тихий выход (покажется статика).
    """
    await asyncio.sleep(0.3)
    try:
        ctx = get_user_context(user_id)
        position_raw = ctx.get("position")
        lang_answer  = ctx.get("lang")
        if not position_raw:
            log.info("user_id=%s: position пустая — генерацию пропускаем", user_id)
            return

        lang_code = _map_lang_code(lang_answer)
        pos_norm  = normalize_position(position_raw) or position_raw.strip()

        prompt = _build_tasks_prompt(pos_norm, lang_code)
        raw = await ask_llm(prompt)          # ← запрос в DO
        items = _parse_items(raw)            # ← строгая валидация 7–8
        inserted = replace_user_ai_options(
            user_id=user_id,
            question_key="summary_tasks",
            lang=lang_code,
            position=pos_norm,
            items=items,
        )
        log.info("user_id=%s: tasks inserted=%s lang=%s pos=%s",
                        user_id, inserted, lang_code, pos_norm)

    except Exception as e:
        log.exception("user_id=%s: generate_tasks_options failed: %s", user_id, e)

    return [it.get("label","") for it in items if it.get("label")]


@alert_on_exception(ttl=1)
async def generate_values_options(user_id: int) -> None:
    """
    Генерирует 7–8 пунктов для мультивыбора 'summary_values'
    и сохраняет их как персональные (source='ai') в answer_options.
    """
    try:
        ctx = get_user_context(user_id)
        position_raw = ctx.get("position")
        lang_answer  = ctx.get("lang")
        if not position_raw:
            log.info("user_id=%s: position пустая — values пропускаем", user_id)
            return

        lang_code = _map_lang_code(lang_answer)
        pos_norm  = normalize_position(position_raw) or position_raw.strip()

        prompt = _build_values_prompt(pos_norm, lang_code)
        raw    = await ask_llm(prompt, read_timeout=75.0, retries=1)
        items  = _parse_items(raw)            # нормализация + 7–8 уникальных

        inserted = replace_user_ai_options(
            user_id=user_id,
            question_key="summary_values",
            lang=lang_code,
            position=pos_norm,
            items=items,
        )
        log.info("user_id=%s: values inserted=%s lang=%s pos=%s",
                 user_id, inserted, lang_code, pos_norm)

    except Exception as e:
        log.exception("user_id=%s: generate_values_options failed: %s", user_id, e)

    return [it.get("label","") for it in items if it.get("label")]


@alert_on_exception(ttl=1)
async def chain_generate_after_position(user_id: int):
    try:
        task_labels  = await generate_tasks_options(user_id)
        await asyncio.sleep(2.0)  # небольшой «антифлуд» и визуально приятнее
        value_labels = await generate_values_options(user_id)

        username, full_name, _ = get_user_identity(user_id)
        ctx = get_user_context(user_id)
        lang_code = _map_lang_code(ctx.get("lang"))
        pos_norm  = normalize_position(ctx.get("position") or "") or (ctx.get("position") or "")

        parts = [
            f"Username: @{username or '-'}",
            f"Full name: {full_name or '-'}",
            "",
            f"Position: {pos_norm} - {lang_code}",
        ]
        if task_labels:
            parts += ["", "Tasks:", "- " + "\n- ".join(task_labels)]
        if value_labels:
            parts += ["", "Values:", "- " + "\n- ".join(value_labels)]

        txt = "\n".join(parts)
        ok = await alert.send(txt, level="INFO")
        if not ok:
            log.warning("alert: combined send failed (user_id=%s)", user_id)

    except Exception as e:
        log.exception("chain_generate_after_position: %s", e)