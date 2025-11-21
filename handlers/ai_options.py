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


def _clean_label(label: str, max_len: int = 56) -> str:
    """
    Очищаем и аккуратно обрезаем label:
    - убираем переносы/кавычки;
    - если строка длиннее max_len, режем по границе слова, а не посередине.
    """
    lab = (label or "").replace("\n", " ").strip()
    lab = lab.strip(' "\'')
    if len(lab) <= max_len:
        return lab
    # грубо обрезаем и откатываемся к последнему пробелу
    cut = lab[:max_len]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]

    return cut.strip()



def _ensure_unique_7_8(items: List[Dict[str, str]], max_len: int = 56) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    seen = set()
    for it in items:
        v = _slugify(it.get("value") or it.get("label", ""))
        l = _clean_label(it.get("label", ""), max_len=max_len)
        if not l or v in seen:
            continue
        seen.add(v)
        out.append({"value": v, "label": l})
        if len(out) >= 8:
            break
    if len(out) < 7:
        raise ValueError("Нужно 7–8 уникальных пунктов")
    return out[:8]



def _parse_items(raw_text: str, *, max_len: int = 56) -> List[Dict[str, str]]:
    """Принимаем строку с JSON-массивом и возвращаем нормализованный список словарей."""
    data = json.loads(raw_text)
    if not isinstance(data, list):
        raise ValueError("Ожидался JSON-массив")
    cleaned: List[Dict[str, str]] = []
    for it in data:
        if not isinstance(it, dict):
            continue
        val = _slugify(it.get("value") or it.get("label", ""))
        lab = _clean_label(it.get("label", ""), max_len=max_len)
        if not lab:
            continue
        cleaned.append({"value": val, "label": lab})
    return _ensure_unique_7_8(cleaned, max_len=max_len)



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

# ==== summery_tasks & summary_values ====
def _build_tasks_prompt(position: str, lang_code: str) -> str:
    return f"""
Сгенерируй РАБОЧИЕ ЗАДАЧИ (tasks) для роли "{position}".
Язык меток (label): "{lang_code}".
Если введённая роль неоднозначная (например: архитектор, дизайнер, инженер), не уточнённая пользователем — не додумывай, а воспринимай буквально, например не архитектор приложений, а архитектор просто! 

ТРЕБОВАНИЯ К ВЫВОДУ
- Верни СТРОГО JSON-массив из 8 объектов без пояснений и без бэктиков. Если вариант не проходит по длине — перегенерируй его,
  пока все 8 меток не будут ≤ 56 символов.
- Каждый объект: {{"value":"<slug-en-kebab>", "label":"<короткая фраза на {lang_code}>"}}
- "label":
  • 25–45 символов, НО верхняя граница абсолютная — 56, простая речь, без кавычек и эмодзи;
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




# ==== main skills from file  ====
def _build_values_prompt(position: str) -> str:
    return f"""
ЗАДАЧА: Найти в Базе знаний точный список скиллов для профессии: "{position}".
ОТВЕТ:
- Пунктов всего должно быть 7-8.
- Не переводи пукнты на другие языки, отдавай ТОЛЬКО на том языке, как указано в базе знаний!
- "label": 35–65 символов, если больше – пропусти.
- Если в пункте всего 1 слово, можешь скомбинировать его с соседним пунктом через /, чтобы получилось 2-3 слова.
ЗАПРЕТ: не использовать внешние знания.

ФОРМАТ ОТВЕТА:
[
  {{"value":"<slug-latin-kebab>","label":"[пункт из Skills CV bot]"}},
  {{"value":"<slug-latin-kebab>","label":"[пункт из Skills CV bot]"}}
]
""".strip()





# ==== experience_tasks & experience_achievements ====
def _build_exp_tasks_prompt(role_title: str, lang_code: str) -> str:
    return f"""
Сгенерируй 7–8 рабочих ЗАДАЧ для должности "{role_title}".
Язык меток (label): "{lang_code}".
Если введённая должность неоднозначная (например: архитектор, дизайнер, инженер), не уточнённая пользователем — не додумывай, а воспринимай буквально, например не архитектор приложений, а архитектор просто! 

Требования к выводу:
- Верни СТРОГО JSON-массив из 7–8 объектов, без пояснений и без бэктиков. Если вариант не проходит по длине — перегенерируй его,
  пока все 8 меток не будут ≤ 56 символов.
- Каждый объект: {{"value":"<slug-latin-kebab>", "label":"<короткая фраза на {lang_code}>"}}
- "label": 25–45 символов, НО верхняя граница абсолютная — 56, простая речь, без кавычек и эмодзи, обязательно «действие + объект/контекст».
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



def _build_exp_achievements_from_tasks_prompt(role_title: str, tasks_text: str, lang_code: str) -> str:
    return f"""
У тебя есть специалист с должностью "{role_title}" и вот список его реальных задач (из резюме):

{tasks_text}

На основе ИМЕННО ЭТИХ задач сгенерируй РЕЛЕВАНТНЫЕ для резюме примеры достижений.
Язык: "{lang_code}".

ФОРМАТ:
- Верни СТРОГО JSON-массив ИЗ 3 СТРОК без пояснений и без бэктиков.
- Каждая строка — одно достижение по формуле:
  действие + метрика + период + за счёт + эффект + бизнес-результат.

ТРЕБОВАНИЯ К СТИЛЮ:
- 60–120 символов на строку (не делай короче 50).
- Обязательно:
  • конкретная метрика (%, деньги, количество, скорость, время);
  • период (за 3 месяца, за год, за квартал и т.п.);
  • за счёт чего (какие именно скрипты, какая автоматизация, какие модели);
  • эффект для бизнеса (рост выручки, сокращение ошибок, экономия времени, улучшение качества и т.п.).
- Без эмодзи, без кавычек, без Markdown.
- Фразы должны быть правдоподобны для этих задач и роли "{role_title}" и выглядеть как реальные достижения для HR.

ВАЖНО:
- Возврати ТОЛЬКО JSON-массив строк без текста до/после.
- Никаких буллетов, нумерации, пояснений или комментариев.
""".strip()









# ==== skills_hard & skills_tools ====
def _build_skills_hard_prompt(position: str, lang_code: str) -> str:
    return f"""
Сгенерируй профессиональные НАВЫКИ (hard skills) для резюме под позицию "{position}".
Язык меток (label): "{lang_code}".

ТРЕБОВАНИЯ:
- Верни СТРОГО JSON-массив 7–12 объектов без пояснений и без бэктиков.
- Каждый объект: {{"value":"<slug-latin-kebab>", "label":"<краткий навык на {lang_code}>"}}
- "label": 1–3 слова, без кавычек и эмодзи, конкретные навыки/методики/области.
- "value": латиница, kebab-case, уникальные.

ПРИМЕР СТРУКТУРЫ (НЕ печатать его):
[
  {{"value":"competency-interviews","label":"Интервью по компетенциям"}},
  {{"value":"workforce-planning","label":"Планирование штата"}}
]
""".strip()


def _build_skills_tools_prompt(position: str, lang_code: str) -> str:
    return f"""
Сгенерируй СПИСОК ИНСТРУМЕНТОВ/ПРОГРАММ (tools) для резюме под позицию "{position}".
Язык меток (label): "{lang_code}".

ОБЩИЕ ТРЕБОВАНИЯ:
- Верни СТРОГО JSON-массив 7–8 объектов без пояснений и без бэктиков.
- Каждый объект: {{"value":"<slug-latin-kebab>", "label":"<инструмент/пакет на {lang_code}>"}}.
- "value": латиница, kebab-case, уникальные.
- НЕ добавляй никакого текста до/после JSON-массива.

СТРУКТУРА label:
- В КАЖДОМ "label" СНАЧАЛА идут 1–2 конкретных продукта/сервиса/системы через " / ",
  а уже ПОТОМ — короткое описание класса системы после тире.
  Примеры формата (НЕ печатать их дословно, это только образцы):
  • "1С:Медицина / MedOffice — системы управления мед. практикой"
- Если это класс систем, НЕ пиши только класс вроде "Системы управления медицинской практикой".
  ОБЯЗАТЕЛЬНО дай конкретные продукты слева, а класс — справа после "—".
- Названия продуктов должны быть полностью читабельны в начале строки.
  Если строка получается длинной, СОКРАЩАЙ описательную часть после тире, но НЕ обрезай названия продуктов слева.

ТРЕБОВАНИЯ К СПИСКУ:
- Из 7–8 пунктов МИНИМУМ 4 ДОЛЖНЫ содержать разные конкретные продукты/сервисы
  (их названия полностью видны слева до тире).
- Остальные 3–4 пункта могут описывать классы систем, но всё равно начинаются с конкретных продуктов.
- Разрешён максимум ОДИН базовый общий пункт типа "MS Office / Google Workspace — офисные пакеты".
  Остальные пункты ДОЛЖНЫ содержать специализированные инструменты для роли "{position}".

ТРЕБОВАНИЯ К "label":
- 2–8 слов, без эмодзи и кавычек.
- Формат строго: "<продукт1> [ / продукт2] — <краткое описание класса>".
- Избегай пустых общих фраз ("Системы анализа данных", "Информационные системы")
  без конкретных продуктов слева.

ПРОВЕРКИ КАЧЕСТВА (выполни мысленно, НЕ выводи):
- Убедись, что в начале каждого label стоят реальные названия ПО/сервисов.
- Если видишь, что название продукта может быть обрезано, сократи описание после тире, а не имя продукта.
- Не повторяй один и тот же продукт в разных пунктах без необходимости.

ПРИМЕР СТРУКТУРЫ (НЕ печатать его, это только образец формата):
[
  {{"value":"medesk-medoffice-practice","label":"Medesk / MedOffice — управление мед. практикой"}},
  {{"value":"epic-cerner-emr","label":"Epic / Cerner — электронные медкарты"}}
]
""".strip()




# =========================
# ЗАПРОС К ТВОЕМУ DO-АГЕНТУ
# =========================
async def ask_llm(prompt: str, *, read_timeout: float = 60.0, retries: int = 1, tag: str = "") -> str:
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

        prompt = _build_values_prompt(pos_norm)
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
        raw    = await ask_llm(prompt, read_timeout=60.0, retries=1)
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
        if notify and labels:
            try:
                username, full_name, _ = get_user_identity(user_id)
                txt = "\n".join([
                    f"Username: @{username or '-'}",
                    f"Full name: {full_name or '-'}",
                    "",
                    f"Experience role: {role} - {lang_code}",
                    "",
                    "Tasks:",
                    tasks_text or "-",
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



@alert_on_exception(ttl=1)
async def generate_skills_hard_options(user_id: int) -> list[str] | None:
    """ИИ-подсказки для skills_hard на основе target_position."""
    labels = None
    try:
        ctx = get_user_context(user_id)
        position_raw = ctx.get("position") or ctx.get("target_position")  # на всякий
        if not position_raw:
            return None
        lang_code = _map_lang_code(ctx.get("lang"))
        pos_norm  = normalize_position(position_raw) or position_raw.strip()

        prompt = _build_skills_hard_prompt(pos_norm, lang_code)
        raw    = await ask_llm(prompt, read_timeout=60.0, retries=1)
        items  = _parse_items(raw)

        for it in items:
            it["value"] = _short_value_for_cb(it.get("value", ""), pos_norm)

        inserted = replace_user_ai_options(
            user_id=user_id,
            question_key="skills_hard",
            lang=lang_code,
            position=pos_norm,        # ← ключ: привязываем к позиции
            items=items,
        )
        log.info("user_id=%s: skills_hard inserted=%s lang=%s pos=%s",
                 user_id, inserted, lang_code, pos_norm)

        labels = [it["label"] for it in items if it.get("label")]
    except Exception as e:
        log.exception("user_id=%s: generate_skills_hard_options failed: %s", user_id, e)
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
        raw    = await ask_llm(prompt, read_timeout=60.0, retries=1)
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


# =========================
# СБОРКА ГЕНЕРАЦИИ И АЛЕРТА
# =========================
@alert_on_exception(ttl=1)
async def chain_generate_after_position(user_id: int):
    try:
        task_labels  = await generate_tasks_options(user_id)
        await asyncio.sleep(2.0)
        value_labels = await generate_values_options(user_id)
        await asyncio.sleep(2.0)
        hard_labels = await generate_skills_hard_options(user_id)
        await asyncio.sleep(2.0)
        tools_labels = await generate_skills_tools_options(user_id)

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
        if hard_labels:
            parts += ["", "Hard skills:", "- " + "\n- ".join(hard_labels)]
        if tools_labels:
            parts += ["", "Tools:", "- " + "\n- ".join(tools_labels)]

        txt = "\n".join(parts)
        ok = await alert.send(txt, level="INFO")
        if not ok:
            log.warning("alert: combined send failed (user_id=%s)", user_id)

    except Exception as e:
        log.exception("chain_generate_after_position: %s", e)


@alert_on_exception(ttl=1)
async def generate_experience_bundle(user_id: int, role_title: str):
    role = (role_title or "").strip()
    if not role:
        return
    
    tasks_labels = await generate_experience_tasks_options(user_id, role, notify=False)
    if not tasks_labels:
        return

    username, full_name, _ = get_user_identity(user_id)
    ctx = get_user_context(user_id)
    lang_code = _map_lang_code(ctx.get("lang"))

    parts = [
        f"Username: @{username or '-'}",
        f"Full name: {full_name or '-'}",
        "",
        f"Experience role: {role} - {lang_code}",
        "",
        "Experience tasks:",
        "- " + "\n- ".join(tasks_labels),
    ]

    await alert.send("\n".join(parts), level="INFO")
