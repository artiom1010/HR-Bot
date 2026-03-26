# handlers/ai_options.py
import os
import re
import json
import asyncio
import logging
import httpx
from typing import List, Dict, Optional
from hashlib import sha1
import hashlib


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


def _clean_label(label: str, max_len: int = 35) -> str:
    lab = (label or "").replace("\n", " ").strip()
    lab = lab.strip(' "\'')
    return lab



# def _ensure_unique_7_8(items: List[Dict[str, str]], max_len: int = 35) -> List[Dict[str, str]]:
#     out: List[Dict[str, str]] = []
#     seen = set()
#     for it in items:
#         v = _slugify(it.get("value") or it.get("label", ""))
#         l = _clean_label(it.get("label", ""), max_len=max_len)
#         if not l or len(l) > max_len or v in seen:
#             continue
#         seen.add(v)
#         out.append({"value": v, "label": l})
#         if len(out) >= 8:
#             break
#     if len(out) < 7:
#         raise ValueError("Нужно 7–8 уникальных пунктов")
#     return out[:8]


def _parse_items(raw_text: str, *, max_len: int = 35) -> List[Dict[str, str]]:
    """Принимаем строку с JSON-массивом и возвращаем нормализованный список словарей."""
    s = (raw_text or "").strip()
    try:
        data = json.loads(s)
    except json.JSONDecodeError:
        m = re.search(r"\[.*\]", s, flags=re.S)
        if not m:
            raise ValueError("Ожидался JSON-массив")
        data = json.loads(m.group(0))

    if not isinstance(data, list):
        raise ValueError("Ожидался JSON-массив")
    cleaned: List[Dict[str, str]] = []
    for it in data:
        if not isinstance(it, dict):
            continue
        # val = _slugify(it.get("value") or it.get("label", ""))
        raw_val = (it.get("value") or it.get("label", "")).strip()
        val = _slugify(raw_val)
        lab = _clean_label(it.get("label", ""), max_len=max_len)
        if not lab or len(lab) > max_len:
            continue
        cleaned.append({"value": val, "label": lab})
    # return _ensure_unique_7_8(cleaned, max_len=max_len)
    return cleaned



def _parse_examples_to_items(raw_text: str) -> list[dict[str, str]]:
    try:
        arr = _extract_json_array(raw_text)
    except Exception:
        arr = _coerce_bullets_to_array(raw_text)

    cleaned = []
    for s in arr:
        if not isinstance(s, str):
            continue
        lab = _clean_label(s, max_len=200)
        if not lab:
            continue
        val = _slugify(lab)[:40] or "item"
        cleaned.append({"value": val, "label": lab})
        if len(cleaned) == 3:
            break
    if not cleaned:
        raise ValueError("Empty achievements list")
    return cleaned


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
LABEL_MAX_CHARS = 30
ITEMS_COUNT = 8

def _build_tasks_prompt(position: str, lang_code: str) -> str:
    return f"""
Сгенерируй РАБОЧИЕ ЗАДАЧИ (tasks) для роли "{position}".
Язык меток (label): "{lang_code}".

ТРЕБОВАНИЯ К ВЫВОДУ:
- Верни СТРОГО JSON-массив из {ITEMS_COUNT} объектов без пояснений и без бэктиков.
- Каждый объект: {{"value":"<slug-en-kebab>", "label":"<фраза на {lang_code}>"}}

ТРЕБОВАНИЯ К "label":
- Каждая фраза должна быть ЗАКОНЧЕННОЙ и сохранять смысл;
- Формат: глагол + полное существительное;
- Примеры ✅ ПРАВИЛЬНО: "Вести код-ревью", "Готовить отчёты", "Обучать стажёров";
- Примеры ❌ НЕПРАВИЛЬНО: "Вести инструментальные", "Анализировать данные для";
- Максимум {LABEL_MAX_CHARS} символов (жёсткий лимит);
- Без кавычек, эмодзи, буллетов.

ТРЕБОВАНИЯ К "value":
- Латиница, kebab-case, уникальные, 4 слова;
- Пример: "code-review", "prepare-reports", "train-interns".

ЗАПРЕЩЕНО:
- Однословные фразы без контекста ("Оптимизация", "Тестирование");
- Обрезанные фразы ("Вести инструментальные", "Анализировать данные для");
- Превышение {LABEL_MAX_CHARS} символов.

ФОРМАТ ОТВЕТА (только JSON, без текста):
[
  {{"value":"code-review","label":"Task in {lang_code}"}},
  {{"value":"prepare-reports","label":"Task in {lang_code}"}}
]
""".strip()


def _build_values_prompt(position: str, lang_code: str) -> str:
    return f"""
Сгенерируй НАВЫКИ/СКИЛЛЫ (skills) для роли "{position}".
Язык меток (label): "{lang_code}".
Источник: файл "Skills CV bot - Лист1.pdf" в Базе знаний.

ТРЕБОВАНИЯ К ВЫВОДУ:
- Верни JSON-массив из {ITEMS_COUNT} объектов.
- Если в базе нет подходящей профессии — сгенерируй сам, НЕ пиши об этом.
- Без пояснений, без текста до/после, без бэктиков ```.

ТРЕБОВАНИЯ К "label":
- Максимум {LABEL_MAX_CHARS} символов (жёсткий лимит).
- Формат: навык или "Навык1 / Навык2" (если 1 слово — объедини).
- Примеры ✅: "Работа с CRM", "Python / SQL", "Анализ данных".
- Примеры ❌: "CRM" (слишком коротко), "Навыки работы с системами" (слишком длинно).
- Отдавай пункты на языке: "{lang_code}". Если {lang_code} неизвестен — используй английский.

ТРЕБОВАНИЯ К "value":
- Латиница, kebab-case, 4 слова, уникальные.
- Пример: "crm-management", "python-sql", "data-analysis".

ЗАПРЕЩЕНО:
- Однословные метки без контекста.
- Превышение {LABEL_MAX_CHARS} символов.
- Эмодзи, кавычки, буллеты.
- Пояснения что "в базе нет".

ФОРМАТ ОТВЕТА (только JSON, без текста):
[
  {{"value":"crm-management","label":"пункт из Skills CV bot - Лист1.pdf"}},
  {{"value":"data-analysis","label":"пункт из Skills CV bot - Лист1.pdf"}}
]
""".strip()



def _build_exp_tasks_prompt(role_title: str, lang_code: str) -> str:
    return f"""
Сгенерируй РАБОЧИЕ ЗАДАЧИ (tasks) для должности "{role_title}".
Язык меток (label): "{lang_code}".

ТРЕБОВАНИЯ К ВЫВОДУ:
- Верни JSON-массив из {ITEMS_COUNT} объектов.
- Без пояснений, без текста до/после, без бэктиков ```.

ТРЕБОВАНИЯ К "label":
- Максимум {LABEL_MAX_CHARS} символов (жёсткий лимит).
- Язык метки СТРОГО совпадает с "{lang_code}". НЕ используй другие языки.
- Формат: глагол + существительное.
- Фраза должна быть ЗАКОНЧЕННОЙ и сохранять смысл.
- Примеры структуры ✅ (язык — только иллюстрация формата, используй "{lang_code}"): "Process leads", "Check quality", "Negotiate deals".
- Примеры ❌: однословные метки, фразы длиннее {LABEL_MAX_CHARS} символов.

ТРЕБОВАНИЯ К "value":
- Латиница, kebab-case, 4 слова, уникальные.
- Пример: "process-leads", "check-quality", "negotiate-deals".

ЗАПРЕЩЕНО:
- Обрезанные фразы без смысла.
- Однословные метки.
- Эмодзи, кавычки, буллеты.
- Превышение {LABEL_MAX_CHARS} символов.

ФОРМАТ ОТВЕТА (только JSON, без текста):
[
  {{"value":"crm-leads-process","label":"Task in {lang_code}"}},
  {{"value":"sales-reports","label":"Task in {lang_code}"}}
]
""".strip()


def _build_exp_achievements_from_tasks_prompt(role_title: str, tasks_text: str, lang_code: str) -> str:
    """Этот промпт генерирует ТЕКСТ достижений, НЕ кнопки."""
    return f"""
Сгенерируй ДОСТИЖЕНИЯ для должности "{role_title}".
Язык: "{lang_code}".
Задачи из резюме: {tasks_text}

ТРЕБОВАНИЯ К ВЫВОДУ:
- Верни JSON-массив из 3 СТРОК (не объектов!).
- Без пояснений, без текста до/после, без бэктиков ```.

ТРЕБОВАНИЯ К СТРОКАМ:
- 60–120 символов каждая.
- Формула: действие + метрика (%, $, шт.) + период + за счёт чего + результат.
- Примеры ✅: 
  "Сократил время обработки на 40% за 3 мес. за счёт автоматизации — экономия 20ч/нед"
  "Увеличил конверсию на 25% за квартал через A/B тесты — рост выручки на 15%"
- Примеры ❌:
  "Улучшил процессы" (нет метрик)
  "Работал над оптимизацией" (нет результата)

ЗАПРЕЩЕНО:
- Фразы без метрик и периода.
- Эмодзи, кавычки, Markdown.
- Менее 60 символов.

ФОРМАТ ОТВЕТА (только JSON, без текста):
[
  "Достижение 1 с метрикой и периодом",
  "Достижение 2 с метрикой и периодом",
  "Достижение 3 с метрикой и периодом"
]
""".strip()

def _build_skills_tools_prompt(position: str, lang_code: str) -> str:
    return f"""
Сгенерируй ИНСТРУМЕНТЫ И ПРОГРАММЫ (tools) для позиции "{position}".
Язык меток (label): "{lang_code}".

ТРЕБОВАНИЯ К ВЫВОДУ:
- Верни JSON-массив из {ITEMS_COUNT} объектов.
- Минимум 4 пункта с конкретными продуктами.
- Максимум 1 общий пункт типа "MS Office, Google".
- Без пояснений, без текста до/после, без бэктиков ```.

ТРЕБОВАНИЯ К "label":
- Максимум {LABEL_MAX_CHARS} символов (жёсткий лимит).
- Формат: "Продукт1, Продукт2" (названия на английском).
- Примеры ✅: "Figma, Sketch", "Jira, Trello", "Excel, Sheets".
- Примеры ❌: "Системы управления проектами", "Инструменты дизайна".

ТРЕБОВАНИЯ К "value":
- Латиница, kebab-case, 4 слова, уникальные.
- Пример: "figma-sketch", "jira-trello", "excel-sheets".

ЗАПРЕЩЕНО:
- Абстрактные категории без продуктов.
- Превышение {LABEL_MAX_CHARS} символов.
- Эмодзи, кавычки, буллеты.

ФОРМАТ ОТВЕТА (только JSON, без текста):
[
  {{"value":"figma-sketch","label":"Figma, Sketch"}},
  {{"value":"jira-trello","label":"Jira, Trello"}}
]
""".strip()




_MAX_VALUE_LEN_FOR_CB = 28
def _short_value_for_cb(raw_value: str, role: str) -> str:
    """
    Возвращает короткий, уникальный и безопасный value для кнопок:
    r<6-симв.хэш>-<slug> длиной не более 28.
    """
    base = _slugify(raw_value)
    pref = "r" + hashlib.sha1((role or "").encode("utf-8")).hexdigest()[:6]
    # сколько осталось под сам слаг с учётом "rxxxxxx-"
    room = max(1, _MAX_VALUE_LEN_FOR_CB - (len(pref) + 1))
    base = base[:room]
    return f"{pref}-{base}"

def _extract_json_array(text: str) -> list:
    """Снимаем ```json, ищем первый валидный [ ... ]."""
    s = (text or "").strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    # 1) прямой JSON
    try:
        v = json.loads(s)
        if isinstance(v, list):
            return v
    except Exception:
        pass
    # 2) первый блок в квадратных скобках
    m = re.search(r"$begin:math:display$[\\s\\S]*?$end:math:display$", s)
    if m:
        return json.loads(m.group(0))
    raise ValueError("JSON array not found")

def _coerce_bullets_to_array(text: str) -> list[str]:
    lines = [ln.rstrip() for ln in (text or "").splitlines()]
    picked: list[str] = []
    for ln in lines:
        m = re.match(r"^(?:[-–—*•]|\d+[.)])\s+(.*)$", ln)
        if not m:
            continue
        body = m.group(1).strip()

        if body in {"[", "]"}:
            continue
        if body.startswith("[") and body.endswith("]") and len(body) <= 3:
            continue
        body = body.strip().strip('",').strip()

        if body:
            picked.append(body[:200])
        if len(picked) == 3:
            break

    if not picked:
        for ln in lines:
            ln = ln.strip().strip('",').strip()
            if ln in {"[", "]"} or not ln:
                continue
            picked.append(ln[:200])
            if len(picked) == 3:
                break
    return picked[:3]



# =========================
# ЗАПРОС К ТВОЕМУ DO-АГЕНТУ
# =========================
async def ask_llm(prompt: str, *, read_timeout: float = 120.0, retries: int = 1, tag: str = "") -> str:
    """
    Шлём промпт в DO-агент и возвращаем ЧИСТЫЙ текст JSON-массива.
    - read_timeout: таймаут на чтение (сек)
    - retries: количество повторов при сетевых/парсинг-ошибках
    """
    # ⬇️ логируем то, что реально отправляем
    log.info("============== PROMPT TO LLM%s:==============\n%s", f" [{tag}]" if tag else "", prompt)

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
            
            # ⬇️ логируем то, что получаем
            log.info("++++++++++++ PROMPT FROM LLM%s:++++++++++++\n%s", f" [{tag}]" if tag else "", cleaned)
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
async def generate_tasks_options(user_id: int) -> list[str] | None:
    labels = None
    try:
        ctx = get_user_context(user_id)
        position_raw = ctx.get("position")
        lang_answer  = ctx.get("lang")
        if not position_raw:
            log.info("user_id=%s: position пустая — генерацию пропускаем", user_id)
            return None

        lang_code = _map_lang_code(lang_answer)
        pos_norm  = normalize_position(position_raw) or position_raw.strip()

        prompt = _build_tasks_prompt(pos_norm, lang_code)
        raw    = await ask_llm(prompt)
        items  = _parse_items(raw)

        for it in items:
            it["value"] = _short_value_for_cb(it.get("value", ""), pos_norm)

        inserted = replace_user_ai_options(
            user_id=user_id, question_key="summary_tasks",
            lang=lang_code, position=pos_norm, items=items,
        )
        log.info("user_id=%s: tasks inserted=%s lang=%s pos=%s",
                 user_id, inserted, lang_code, pos_norm)

        labels = [it["label"] for it in items if it.get("label")]
    except Exception as e:
        log.exception("user_id=%s: generate_tasks_options failed: %s", user_id, e)
    return labels


@alert_on_exception(ttl=1)
async def generate_values_options(user_id: int) -> list[str] | None:
    labels = None
    try:
        ctx = get_user_context(user_id)
        position_raw = ctx.get("position")
        lang_answer  = ctx.get("lang")
        if not position_raw:
            log.info("user_id=%s: position пустая — values пропускаем", user_id)
            return None

        lang_code = _map_lang_code(lang_answer)
        pos_norm  = normalize_position(position_raw) or position_raw.strip()

        prompt = _build_values_prompt(pos_norm, lang_code)
        raw    = await ask_llm(prompt, read_timeout=75.0, retries=1)
        items  = _parse_items(raw)

        for it in items:
            it["value"] = _short_value_for_cb(it.get("value", ""), pos_norm)

        inserted = replace_user_ai_options(
            user_id=user_id, question_key="summary_values",
            lang=lang_code, position=pos_norm, items=items,
        )
        log.info("user_id=%s: values inserted=%s lang=%s pos=%s",
                 user_id, inserted, lang_code, pos_norm)

        labels = [it["label"] for it in items if it.get("label")]
    except Exception as e:
        log.exception("user_id=%s: generate_values_options failed: %s", user_id, e)
    return labels


@alert_on_exception(ttl=1)
async def generate_skills_tools_options(user_id: int) -> list[str] | None:
    """ИИ-подсказки для skills_tools на основе target_position."""
    labels = None
    try:
        ctx = get_user_context(user_id)
        position_raw = ctx.get("position") or ctx.get("target_position")
        if not position_raw:
            return None
        lang_code = _map_lang_code(ctx.get("lang"))
        pos_norm  = normalize_position(position_raw) or position_raw.strip()

        prompt = _build_skills_tools_prompt(pos_norm, lang_code)
        raw    = await ask_llm(prompt, read_timeout=120.0, retries=1)
        items  = _parse_items(raw, max_len=70)

        for it in items:
            it["value"] = _short_value_for_cb(it.get("value", ""), pos_norm)

        inserted = replace_user_ai_options(
            user_id=user_id,
            question_key="skills_tools",
            lang=lang_code,
            position=pos_norm,
            items=items,
        )
        log.info("user_id=%s: skills_tools inserted=%s lang=%s pos=%s",
                 user_id, inserted, lang_code, pos_norm)

        labels = [it["label"] for it in items if it.get("label")]
    except Exception as e:
        log.exception("user_id=%s: generate_skills_tools_options failed: %s", user_id, e)
    return labels


@alert_on_exception(ttl=1)
async def generate_experience_tasks_options(user_id: int, role_title: str, *, notify: bool = False) -> list[str] | None:
    try:
        ctx = get_user_context(user_id)
        lang_code = _map_lang_code(ctx.get("lang"))
        role = (role_title or "").strip()
        if not role:
            return None

        prompt = _build_exp_tasks_prompt(role, lang_code)
        raw    = await ask_llm(prompt, read_timeout=120.0, retries=1)
        items  = _parse_items(raw)  # 7–8 нормализованных пунктов

        for it in items:
            it["value"] = _short_value_for_cb(it.get("value", ""), role)

        inserted = replace_user_ai_options(
            user_id=user_id,
            question_key="experience_tasks",
            lang=lang_code,
            position=role,
            items=items,
        )
        log.info("user_id=%s: exp_tasks inserted=%s role=%s lang=%s",
                 user_id, inserted, role, lang_code)

        labels = [it.get("label","") for it in items if it.get("label")]
        return labels
    
    except Exception as e:
        log.exception("user_id=%s: generate_experience_tasks_options failed: %s", user_id, e)
        return None



@alert_on_exception(ttl=1)
async def generate_experience_achievements_examples(
    user_id: int,
    role_title: str,
    tasks_text: str | None = None,
    *,
    notify: bool = True
) -> list[str] | None:

    """
    Генерирует 3 примера достижений для указанной роли и сохраняет их как options
    под ключом question_key="experience_achievements" (по аналогии с experience_tasks).
    Возвращает список текстов примеров или None.
    """
    try:
        ctx = get_user_context(user_id)
        lang_code = _map_lang_code(ctx.get("lang"))
        role = (role_title or "").strip()
        if not role:
            return None
        
        tasks_text = tasks_text or ""   

        prompt = _build_exp_achievements_from_tasks_prompt(role, tasks_text, lang_code)
        raw    = await ask_llm(prompt, read_timeout=120.0, retries=1)
        items  = _parse_examples_to_items(raw)

        for it in items:
            it["value"] = _short_value_for_cb(it.get("value", ""), role)

        inserted = replace_user_ai_options(
            user_id=user_id,
            question_key="experience_achievements",
            lang=lang_code,
            position=role,
            items=items,
        )
        log.info("user_id=%s: exp_achievements inserted=%s role=%s lang=%s",
                 user_id, inserted, role, lang_code)

        labels = [it.get("label","") for it in items if it.get("label")]
        return labels
    
    except Exception as e:
        log.exception("user_id=%s: generate_experience_achievements_examples failed: %s", user_id, e)
        return None


# =========================
# СБОРКА ГЕНЕРАЦИИ И АЛЕРТА
# =========================
@alert_on_exception(ttl=1)
async def chain_generate_after_position(user_id: int):
    try:
        task_labels, value_labels, tools_labels = await asyncio.gather(
            generate_tasks_options(user_id),
            generate_values_options(user_id),
            generate_skills_tools_options(user_id),
            return_exceptions=False,
        )

        username, full_name, _ = get_user_identity(user_id)
        ctx = get_user_context(user_id)
        lang_code = _map_lang_code(ctx.get("lang"))
        pos_norm = normalize_position(ctx.get("position") or "") or (ctx.get("position") or "")

        parts = [
            f"Username: @{username or '-'}",
            f"Full name: {full_name or '-'}",
            "",
            f"Position: {pos_norm} - {lang_code}",
        ]
        
        if task_labels:
            parts += ["", "Tasks:", "- " + "\n- ".join(task_labels)]
        else:
            parts += ["", "Tasks: ❌ generation failed after 2 attempts"]
            
        if value_labels:
            parts += ["", "Values:", "- " + "\n- ".join(value_labels)]
        else:
            parts += ["", "Values: ❌ generation failed after 2 attempts"]
            
        if tools_labels:
            parts += ["", "Tools:", "- " + "\n- ".join(tools_labels)]
        else:
            parts += ["", "Tools: ❌ generation failed after 2 attempts"]

        txt = "\n".join(parts)
        ok = await alert.send(txt, level="INFO")
        if not ok:
            log.warning("alert: combined send failed (user_id=%s)", user_id)

    except Exception as e:
        log.exception("chain_generate_after_position: %s", e)

