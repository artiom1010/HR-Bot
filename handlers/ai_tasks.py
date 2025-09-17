# handlers/ai_tasks.py
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


def _clean_label(label: str, max_len: int = 50) -> str:
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

def _build_tasks_prompt(position: str, lang_code: str) -> str:
    return f"""
Сгенерируй РАБОЧИЕ ЗАДАЧИ (tasks) для роли "{position}".
Язык меток (label): "{lang_code}".

ТРЕБОВАНИЯ К ВЫВОДУ
- Верни СТРОГО JSON-массив из 8 объектов без пояснений и без бэктиков.
- Каждый объект: {{"value":"<slug-en-kebab>", "label":"<короткая фраза на {lang_code}>"}}
- "label":
  • 25–45 символов, простая речь, без кавычек и эмодзи;
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
- "label": 25–45 символов, простая речь, без кавычек и эмодзи.
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



def _build_exp_tasks_prompt(role_title: str, lang_code: str) -> str:
    return f"""
Сгенерируй 7–8 типичных рабочих ЗАДАЧ для должности "{role_title}".
Язык меток (label): "{lang_code}".

Требования к выводу:
- Верни СТРОГО JSON-массив из 7–8 объектов, без пояснений и без бэктиков.
- Каждый объект: {{"value":"<slug-latin-kebab>", "label":"<короткая фраза на {lang_code}>"}}
- "label": 25–45 символов, простая речь, без кавычек и эмодзи, обязательно «действие + объект/контекст».
- "value": латиница, kebab-case, уникальные, 2–3 слова.

Проверки качества:
- Запрещены однословные/слишком общие метки ("Оптимизация", "Коммуникации") без уточнения.
- Где уместно — добавь контекст: «в CRM», «по чек-листу смены», «по SLA», «в POS-системе», «воронка продаж» и т.п.

Формат ответа (пример структуры, НЕ печатать сам пример):
[
  {{"value":"crm-leads-proc","label":"Обрабатывать лиды в CRM"}},
  {{"value":"weekly-sales-rep","label":"Готовить отчётность по продажам"}}
]
""".strip()


def _build_exp_achievements_prompt(role_title: str, lang_code: str) -> str:
    return f"""
Сгенерируй РЕЛЕВАНТНЫЕ для должности "{role_title}" примеры достижений в резюме.
Язык: "{lang_code}".

Формат: ВЕРНИ СТРОГО JSON-массив ИЗ 3 СТРОК без пояснений и без бэктиков.
Каждая строка — одно достижение по формуле:
действие + метрика + период + за счёт + эффект.

Требования к стилю:
- 90–150 символов на строку.
- Конкретные числа/проценты/сроки (если уместно).
- Без эмодзи, без кавычек, без Markdown.
- Фразы должны чувствоваться "про {role_title}" (лексика/контекст роли).

Важно:
- Возврати ТОЛЬКО JSON-массив строк без какого-либо текста до/после.
- Запрещены буллеты, нумерация, комментарии, пояснения, кавычки-снаружи массива и лишние запятые.
- ЕСЛИ не можешь — всё равно верни массив из ТРЁХ корректных строк.

Примеры структуры (НЕ печатать их, это не часть ответа):
[
  "Снизил расходы на 22% за 9 мес. за счёт новых условий — экономия ~$5000/мес.",
  "Ускорил выдачу на 30% за 6 мес. автоматизацией — просрочки −45%.",
  "Довёл NPS до 4.8/5 за квартал обучением — повторные покупки +15%."
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


@alert_on_exception(ttl=1)
# async def generate_experience_tasks_options(user_id: int, role_title: str) -> list[str] | None:
async def generate_experience_tasks_options(user_id: int, role_title: str, *, notify: bool = False) -> list[str] | None:
    try:
        ctx = get_user_context(user_id)
        lang_code = _map_lang_code(ctx.get("lang"))
        role = (role_title or "").strip()
        if not role:
            return None

        prompt = _build_exp_tasks_prompt(role, lang_code)
        raw    = await ask_llm(prompt, read_timeout=60.0, retries=1)
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

        # === ДОБАВЛЕНО: алерт как в саммари ===
        if notify and labels:
            try:
                username, full_name, _ = get_user_identity(user_id)
                txt = "\n".join([
                    f"Username: @{username or '-'}",
                    f"Full name: {full_name or '-'}",
                    "",
                    f"Experience role: {role} - {lang_code}",
                    "",
                    "Exp tasks:",
                    "- " + "\n- ".join(labels)
                ])
                await alert.send(txt, level="INFO")
            except Exception as e:
                log.warning("alert exp_tasks failed: %s", e)
        # =====================================

        return labels
    except Exception as e:
        log.exception("user_id=%s: generate_experience_tasks_options failed: %s", user_id, e)
        return None


# handlers/ai_tasks.py — ДОБАВИТЬ рядом с утилитами
def _extract_json_array(text: str) -> list:
    """Снимаем ```json, ищем первый валидный [ ... ]."""
    import re, json
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
    import re
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

@alert_on_exception(ttl=1)
# async def generate_experience_achievements_examples(user_id: int, role_title: str) -> list[str] | None:
async def generate_experience_achievements_examples(user_id: int, role_title: str, *, notify: bool = False) -> list[str] | None:

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

        prompt = _build_exp_achievements_prompt(role, lang_code)
        raw    = await ask_llm(prompt, read_timeout=60.0, retries=1)

        items  = _parse_examples_to_items(raw)

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
        # лёгкий инфо-алёрт (как в других местах)
        if notify and labels:
            try:
                username, full_name, _ = get_user_identity(user_id)
                txt = "\n".join([
                    f"Username: @{username or '-'}",
                    f"Full name: {full_name or '-'}",
                    "",
                    f"Experience role: {role} - {lang_code}",
                    "",
                    "Achievements (examples):",
                    "- " + "\n- ".join(labels)
                ])
                await alert.send(txt, level="INFO")
            except Exception as e:
                log.warning("alert exp_achievements failed: %s", e)

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
        task_labels  = await generate_tasks_options(user_id)
        await asyncio.sleep(2.0)
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


@alert_on_exception(ttl=1)
async def generate_experience_bundle(user_id: int, role_title: str):
    """
    Параллельно генерим exp tasks + achievements для выбранной роли,
    сохраняем options и отправляем ОДИН общий алерт.
    """
    role = (role_title or "").strip()
    if not role:
        return

    # параллельная генерация без собственных алертов
    tasks_labels, ach_labels = await asyncio.gather(
        generate_experience_tasks_options(user_id, role, notify=False),
        generate_experience_achievements_examples(user_id, role, notify=False),
    )

    # если совсем пусто — молчим
    if not (tasks_labels or ach_labels):
        return

    # собираем одно сообщение
    username, full_name, _ = get_user_identity(user_id)
    ctx = get_user_context(user_id)
    lang_code = _map_lang_code(ctx.get("lang"))

    parts = [
        f"Username: @{username or '-'}",
        f"Full name: {full_name or '-'}",
        "",
        f"Experience role: {role} - {lang_code}",
    ]
    if tasks_labels:
        parts += ["", "Experience tasks:", "- " + "\n- ".join(tasks_labels)]
    if ach_labels:
        parts += ["", "Achievements:", "- " + "\n- ".join(ach_labels)]

    await alert.send("\n".join(parts), level="INFO")