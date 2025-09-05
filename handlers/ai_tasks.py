import re
import json
import httpx
import asyncio
import logging
import random
from typing import List, Dict, Optional

from _conf import DO_AGENT_URL, DO_API_TOKEN
from handlers.options_repo import (
    get_user_context,
    replace_user_ai_options,
    normalize_position,
)

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





# =========================
# ГЕНЕРАЦИЯ ПРОМПТОВ
# =========================

def _build_tasks_prompt(position: str, lang_code: str) -> str:
    return f"""
ЗАДАЧА:
Сгенерируй список ключевых рабочих задач (skills/tasks) для роли "{position}".
Результат будет показан пользователю как кнопки в Telegram, на языке "{lang_code}".
Нужно отдать РОВНО 7 или 8 коротких, конкретных задач уровня «чем человек занимается каждый день».

ТРЕБОВАНИЯ:
- Строгий JSON-ответ без пояснений и без обрамляющих подсказок.
- Массив объектов с полями:
  {{ "value": "<slug-en-kebab-case>", "label": "<короткий текст на {lang_code}>" }}
- "label": до 30–45 символов, без кавычек и эмодзи.
- "value": латиница, kebab-case, уникальная (напр. "risk-management").
- Без повторов и почти-синонимов.
- Фокус на прикладные действия, не абстрактные качества.

ФОРМАТ ОТВЕТА:
[
  {{"value":"<slug-1>","label":"<кнопка 1>"}},
  {{"value":"<slug-2>","label":"<кнопка 2>"}},
  ...
]
""".strip()

def _build_values_prompt(position: str, lang_code: str) -> str:
    return f"""
Сгенерируй список рабочих ЦЕННОСТЕЙ/ПОДХОДОВ (work values / ways of working)
для роли "{position}". Язык кнопок: "{lang_code}".

Требования:
- Верни СТРОГО JSON-массив из 7 или 8 объектов, без пояснений и без ```.
- Каждый объект: {{"value":"<slug-en-kebab>", "label":"<короткая фраза на {lang_code}>"}}
- "label": 30–45 символов, без кавычек и эмодзи; конкретные формулировки:
  «Ориентация на результат», «Честность и прозрачность», «Командная работа»,
  «Клиентоцентричность», «Данные вместо мнений», «Непрерывное улучшение» и т.п.
- "value": латиница, kebab-case, уникальные.
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
        raw = await ask_llm(prompt)
        items = _parse_items(raw)
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
        # raw    = await ask_llm(prompt)        # запрос к твоему агенту
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








def _lang_code_from_label(label: Optional[str]) -> str:
    """Из того, что хранится в answers (у тебя там часто label),
    вытаскиваем ru/en/ro. По умолчанию ru."""
    if not label:
        return "ru"
    l = label.lower()
    if "ro" in l or "rom" in l:
        return "ro"
    if "en" in l or "eng" in l:
        return "en"
    return "ru"

def _slugify(s: str) -> str:
    s = re.sub(r"\s+", "-", s.strip().lower())
    s = re.sub(r"[^a-z0-9\-]", "", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s[:48] or "item"

def _trim_label(label: str, maxlen: int = 40) -> str:
    lab = " ".join(label.strip().split())
    return (lab[:maxlen]).strip()

def _build_values_prompt(position: str, lang_code: str) -> str:
    return f"""
Сгенерируй список рабочих ЦЕННОСТЕЙ/ПОДХОДОВ (work values & ways of working)
для роли "{position}". Язык кнопок: "{lang_code}".

Требования:
- Верни СТРОГО JSON-массив из 7 или 8 объектов, без пояснений и бэктиков.
- Каждый объект: {{"value":"<slug-latin-kebab>", "label":"<короткая фраза на {lang_code}>"}}
- "label": 30–45 символов, без кавычек и эмодзи; конкретные формулировки (напр. «Ориентация на результат», «Честность и прозрачность», «Ответственность за результат», «Клиентоцентричность», «Данные вместо мнений», «Непрерывное улучшение», «Командная работа», «Проактивная коммуникация»).
- "value": латиница, kebab-case, уникальные.

Формат ответа (пример структуры, НЕ списком):
[
  {{"value":"result-orientation", "label":"Ориентация на результат"}},
  {{"value":"transparency", "label":"Честность и прозрачность"}},
  ...
]
""".strip()

async def _call_llm_list(prompt: str, *, timeout_s: float = 12.0) -> Optional[List[dict]]:
    headers = {"Authorization": f"Bearer {DO_API_TOKEN}", "Content-Type": "application/json"}
    payload = {"messages": [{"role": "user", "content": prompt}], "stream": False}
    timeout = httpx.Timeout(connect=8.0, read=timeout_s, write=8.0, pool=10.0)

    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post(f"{DO_AGENT_URL}/api/v1/chat/completions", headers=headers, json=payload)
                r.raise_for_status()
                raw = r.json()["choices"][0]["message"]["content"].strip()
            if raw.startswith("```"):
                raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
                raw = re.sub(r"\n?```$", "", raw).strip()

            data = json.loads(raw)
            if isinstance(data, list):
                return data
            if isinstance(data, dict) and isinstance(data.get("items"), list):
                return data["items"]
            return None
        except Exception as e:
            log.warning("values LLM try %s failed: %r", attempt+1, e)
            await asyncio.sleep(0.7 * (attempt + 1))
    return None

def _normalize_items(raw: List[dict]) -> List[dict]:
    out: List[dict] = []
    seen: set[str] = set()
    for it in raw:
        lab = _trim_label(str(it.get("label", "")))
        if not lab:
            continue
        val = (it.get("value") or _slugify(lab))
        val = _slugify(val)
        if val in seen:
            k, base = 2, val
            while val in seen:
                val = f"{base}-{k}"
                k += 1
        seen.add(val)
        out.append({"value": val, "label": lab})
        if len(out) == 8:
            break

    return out if len(out) >= 7 else []

# ── публичная корутина ───────────────────────────────────────────────────────

# простой lock-пул на пользователя: не допускаем одновременных генераций
_user_locks: dict[int, asyncio.Lock] = {}
def _lock_for(user_id: int) -> asyncio.Lock:
    if user_id not in _user_locks:
        _user_locks[user_id] = asyncio.Lock()
    return _user_locks[user_id]

async def generate_values_options(user_id: int) -> None:
    """
    Генерирует 7–8 пунктов для мультивыбора 'summary_values' и сохраняет
    их как персональные (source='ai') в answer_options.
    На ошибках — только лог; UI покажет статику/предыдущее состояние.
    """
    async with _lock_for(user_id):
        try:
            ctx = get_user_context(user_id)
            position_raw: str = (ctx.get("position") or "").strip()
            if not position_raw:
                log.info("values: skip, no position (user_id=%s)", user_id)
                return

            lang_code = _lang_code_from_label(ctx.get("lang"))
            pos_norm  = normalize_position(position_raw) or position_raw  # на всякий

            prompt = _build_values_prompt(pos_norm, lang_code)

            attempts = 3
            raw_list: Optional[list] = None
            for i in range(attempts):
                try:
                    raw_list = await _call_llm_list(prompt, timeout_s=60.0)
                    break
                except Exception as e:
                    sleep_s = (1.0 * (2 ** i)) + random.uniform(0.0, 0.4)
                    log.warning("values LLM try %s/%s failed: %s; sleep %.1fs",
                                i+1, attempts, e, sleep_s)
                    await asyncio.sleep(sleep_s)

            if not raw_list:
                log.warning("values: LLM returned nothing (user_id=%s, pos=%r)", user_id, pos_norm)
                return

            try:
                items = _normalize_items(raw_list)
            except Exception as e:
                log.warning("values: normalize error (user_id=%s): %s", user_id, e)
                return

            if not items or not (7 <= len(items) <= 8):
                log.warning("values: empty/invalid after normalize (user_id=%s, pos=%r)", user_id, pos_norm)
                return

            inserted = await asyncio.to_thread(
                replace_user_ai_options,
                user_id,
                "summary_values",
                items,
                lang=lang_code,
                position=pos_norm,
            )
            log.info("user_id=%s: values inserted=%s lang=%s pos=%s",
                     user_id, inserted, lang_code, pos_norm)

        except Exception as e:
            log.exception("values generation failed (user_id=%s): %r", user_id, e)

async def chain_generate_after_position(user_id: int):
    try:
        await generate_tasks_options(user_id)
        await asyncio.sleep(3)
        await generate_values_options(user_id)
    except Exception as e:
        log.exception("chain_generate_after_position: %s", e)