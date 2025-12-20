import re
import asyncio, json, datetime as dt
import httpx
from sqlalchemy.orm import Session
from sqlalchemy import select

from _database import engine, Answer, Order, ProcessedData
from _conf import DO_AGENT_URL, DO_API_TOKEN
from _utils import logger

PROMPT_TEMPLATE = """Ты опытный IT-рекрутер с 20-летней практикой.

Твоя задача — переписать ответы в сильное резюме под вакансию, сохранив ВСЕ факты и метрики пользователя. 
Нельзя отбрасывать проценты/числа/сроки и пояснения после тире («— …») или обороты «за счёт …».

⚠️ ЯЗЫК И ГЕНДЕР
1) ВСЕГДА соблюдай язык из поля resume_language: весь текст на этом языке.
2) Определи грамматический род автора по данным (приоритет по порядку):
   a) если явно указан пол/местоимения — используй их;
   b) иначе попытайся вывести из full_name (имя/окончания/частые соответствия);
3) Для русского языка корректируй род в прошедшем времени, прилагательных, причастиях и возможных гендерных вариантах должностей («Тестировщица»/«Тестировщик»). Если в индустрии нейтральная форма общепринята (например, «разработчик»), допускается нейтральная мужская форма без сексистских клише.
4) Для английского языка: «he/him», «she/her» при явном поле; если род не ясен — используй нейтральное «they/them» и без «Mr./Ms.».

❗️Верни ТОЛЬКО JSON-объект с точными ключами:
{{
  "cv_naming": "",
  "career_summary": "",
  "experiences_json": "",
  "education_details": "",
  "languages": "",
  "hard_skills": "",
  "soft_skills": "",
  "full_name": "",
  "contact_phone": "",
  "contact_email": "",
  "contact_links": ""
}}

ТРЕБОВАНИЯ К ОПЫТУ (experiences_json):
• Это строка с JSON-массивом объектов:
  {{
    "job_title_experience": "",
    "employment_period": "",
    "company_name": "",
    "job_responsibilities": ["", …]
  }}
• Источник: поля experience_1, experience_2, … в формате
  "<Должность> // <Компания> // <Период> // <Задачи через запятую> // <Достижения через запятую>".
• Разбери отдельно «задачи» и «достижения». Буллиты собирай в стиле:
  Действие → Измеримый результат → Как было достигнуто (перенеси «за счёт …»/хвост после «— …»).
• ОБЯЗАТЕЛЬНО переноси все числа и метрики из достижений без потерь, включая вторые показатели после тире.
• 5–7 буллитов на место: если задач и достижений больше — объединяй, НО не теряй метрики и пояснения.
• Длина буллита: ориентир 8–22 слов, без местоимения «я», деловой тон.

ПРИМЕР ПРЕОБРАЗОВАНИЯ ДОСТИЖЕНИЯ:
Исходник: "Повысил точность прогнозов на 18% за 3 мес. за счёт новых моделей — прибыль +12%"
Буллит: "Повысил точность прогнозов на 18% за 3 месяца, внедрив новые модели; прибыль выросла на 12%."

ОБРАЗОВАНИЕ И КУРСЫ:
• Образование приходит в нескольких ключах: education_1, education_2, ... Каждая запись в формате
  "ВУЗ // Специальность // Годы".
• Ключ courses (если есть) содержит свободный текст — список курсов/сертификатов, построчно.

СФОРМИРУЙ ПОЛЕ "education_details":
• Для каждого education_N сделай отдельную строку в виде:
  "<ВУЗ> — <Специальность> (<Годы>)"
  Пример: "АСЕМ — IT (2015—2019)"
• Если есть courses, добавь пустую строку, затем ниже перечисли курсы, по одному пункту на строку без HTML/Markdown.
• Никакого HTML, только текст с переносами строк.

• Ответственности — деловые буллиты в нужном языке; избегай «я» и разговорных слов.
• Где возможно, добавляй измеримые результаты (%, $, количество, скорость, время).

⚠️ КРИТИЧНО ДЛЯ JSON:
Внутри всех строковых значений используй \\n (два символа) для переносов.
НЕ вставляй реальные переносы строк внутри значений — это сломает парсинг.

Исходные ответы пользователя:
{answers_block}
"""


REQUIRED_KEYS = [
    "cv_naming",
    "career_summary",
    "experiences_json",
    "education_details",
    "languages",
    "hard_skills",
    "soft_skills",
    "full_name",
    "contact_phone",
    "contact_email",
    "contact_links",
]

# ───────────────────── helpers ──────────────────────
def _build_answers_block(user_id: int) -> str:
    """Синхронный: вытягивает ответы из БД и склеивает строку."""
    with Session(engine) as s:
        rows = (
            s.execute(
                select(Answer.question_key, Answer.answer)
                .where(Answer.user_id == user_id)
                .order_by(Answer.id)
            ).all()
        )
    return "\n".join(f"{key}: {ans}" for key, ans in rows)


async def build_prompt(user_id: int) -> str:
    block = await asyncio.to_thread(_build_answers_block, user_id)
    return PROMPT_TEMPLATE.format(answers_block=block)




def _sanitize_json_string(s: str) -> str:
    """Убирает обёртку ``` и возвращает чистый JSON."""
    s = s.strip()
    # Убираем ``` обёртку (с или без "json")
    s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    s = s.strip()
    return s



async def call_do_genai(prompt: str) -> dict | None:
    logger.info("PROMPT TO DO:\n%s", prompt)

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {DO_API_TOKEN}",
    }
    payload = {
        "messages": [
            {"role": "system", "content": "You strictly preserve all numbers, percentages and causal phrases; do not drop details. Output valid JSON only."},
            {"role": "user", "content": prompt}
        ], 
        "stream": False
        }

    # таймауты раздельно: подлиннее на чтение
    timeout = httpx.Timeout(connect=10.0, read=120.0, write=20.0, pool=10.0)

    for attempt in range(3):  # до 3 попыток
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    f"{DO_AGENT_URL}/api/v1/chat/completions",
                    headers=headers,
                    json=payload,
                )
                resp.raise_for_status()

            raw_content = resp.json()["choices"][0]["message"]["content"]
            logger.info("RAW REPLY FROM DO:\n%r", raw_content)

            try:
                cleaned = _sanitize_json_string(raw_content)
                data = json.loads(cleaned)
            except json.JSONDecodeError as e:
                logger.error("GenAI вернул не‑JSON (попытка %s): %s", attempt+1, e)
                await asyncio.sleep(0.8 * (attempt + 1))
                continue

            # валидация/приведение
            for k in REQUIRED_KEYS:
                data.setdefault(k, "")

            exp = data.get("experiences_json")
            if isinstance(exp, list):
                data["experiences_json"] = json.dumps(exp, ensure_ascii=False)
            elif isinstance(exp, str):
                data["experiences_json"] = exp.strip()
            else:
                data["experiences_json"] = "[]"

            for k in REQUIRED_KEYS:
                if k != "experiences_json":
                    data[k] = str(data.get(k, ""))

            data = {k: data.get(k, "") for k in REQUIRED_KEYS}
            return data

        except httpx.HTTPStatusError as e:
            # важное: логируем код и тело
            logger.error("DO HTTP %s: %s", e.response.status_code, e.response.text[:500])
            return None

        except (httpx.TimeoutException, httpx.RequestError) as e:
            logger.warning("DO timeout/network (try %s/3): %s", attempt+1, e)
            await asyncio.sleep(1.0 * (attempt + 1))  # бэкофф и повтор

        except Exception as e:
            logger.exception("Unexpected GenAI error: %r", e)
            return None

    return None  # все попытки исчерпаны


async def process_order(order_id: int) -> bool:
    """Сгенерировать данные через DO и сохранить их в processed_data."""
    # 1. Получаем заказ
    order = await asyncio.to_thread(lambda: Session(engine).get(Order, order_id))
    if not order:
        logger.error(f"Order {order_id} not found")
        return False

    # 2. Формируем промпт
    prompt = await build_prompt(order.user_id)
    logger.info(f"Prompt length: {len(prompt)} chars")

    # 3. Запрашиваем DO
    data = await call_do_genai(prompt)
    if data is None:
        return False

    # 4. Сохраняем результат
    def _store():
        with Session(engine) as s:
            existing = s.query(ProcessedData).filter_by(order_id=order_id).first()
            if existing:
                for k, v in data.items():
                    setattr(existing, k, v)
                existing.created_at = dt.datetime.utcnow()
            else:
                s.add(ProcessedData(order_id=order_id, **data))

            # обновляем статус
            s.commit()

    await asyncio.to_thread(_store)
    logger.info(f" v2 - ProcessedData saved for order {order_id}")
    return True