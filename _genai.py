import re
import asyncio, json, datetime as dt
import httpx
from sqlalchemy.orm import Session
from sqlalchemy import select

from _database import engine, Answer, Order, ProcessedData
from _conf import DO_AGENT_URL, DO_API_TOKEN
from _utils import logger
from alerts import alert

PROMPT_TEMPLATE = """Ты опытный IT-рекрутер с 20-летней практикой.

ЗАПРЕЩЕНО копировать исходные данные как есть. Разделители "//" — это служебный формат базы данных; в итоговом резюме их быть НЕ должно. Всегда перефразируй и форматируй текст профессионально.

Твоя задача — переписать ответы в сильное резюме под вакансию, сохранив ВСЕ факты и метрики пользователя. 
Нельзя отбрасывать проценты/числа/сроки и пояснения после тире («— …») или обороты «за счёт …».

⚠️ ЯЗЫК И ГЕНДЕР
1) ВСЕГДА соблюдай язык из поля resume_language: весь текст на этом языке.
2) Определи грамматический род автора по данным (приоритет по порядку):
   a) если явно указан пол/местоимения — используй их;
   b) иначе попытайся вывести из full_name (имя/окончания/частые соответствия);
3) Для русского языка корректируй род в прошедшем времени, прилагательных, причастиях и возможных гендерных вариантах должностей («Тестировщица»/«Тестировщик»). Если в индустрии нейтральная форма общепринята (например, «разработчик»), допускается нейтральная мужская форма без сексистских клише.
4) Для английского языка: «he/him», «she/her» при явном поле; если род не ясен — используй нейтральное «they/them» и без «Mr./Ms.».

⚠️ АДАПТАЦИЯ ПОД ВАКАНСИЮ (job_description):
- Проанализируй ключевые требования из job_description: навыки, обязанности, ключевые слова.
- В career_summary и job_responsibilities делай акцент на опыте, который НАПРЯМУЮ соответствует требованиям вакансии.
- В hard_skills выноси первыми те навыки, которые упоминаются в вакансии.
- НЕ выдумывай опыт — только подсвечивай релевантное из реальных данных пользователя.

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

ТРЕБОВАНИЯ К career_summary:
• 3–5 предложений, 200–350 символов минимум.
• Структура: [Роль] + [годы опыта] + [ключевая экспертиза] + [главное достижение] + [ценность для работодателя].
• Пример ✅: "Результативный менеджер по продажам с 5-летним опытом в B2B-сегменте. Специализируюсь на развитии клиентской базы и выстраивании долгосрочных партнёрств. Увеличил объём продаж на 40% за последний год. Умею находить подход к сложным клиентам и закрывать крупные сделки."
• Пример ❌: "Менеджер с опытом работы в продажах." (слишком коротко и сухо)
• Тон: уверенный, профессиональный, конкретный. Без "ищу работу"/"хочу развиваться".

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

⚠️ КРИТИЧНО ДЛЯ job_responsibilities:
• ЗАПРЕЩЕНО копировать задачи как есть ("Сохранять чистоту", "Решать жалобы").
• ОБЯЗАТЕЛЬНО переписать каждую задачу в полноценное профессиональное предложение 8–15 слов.
• Добавь контекст, масштаб, инструменты где уместно.

ПРЕОБРАЗОВАНИЕ ЗАДАЧ (примеры):
• "Сохранять чистоту" → "Обеспечивал соблюдение стандартов чистоты и санитарных норм в зале на 120 посадочных мест"
• "Обслуживать клиентов" → "Обслуживал до 50 гостей в смену, обеспечивая высокий уровень сервиса и повторные визиты"
• "Координировать персонал" → "Координировал работу команды из 5 официантов в часы пиковой нагрузки"
• "Решать жалобы" → "Оперативно разрешал конфликтные ситуации с гостями, сохраняя лояльность клиентов"
• "Анализировать рынок" → "Проводил анализ конкурентов и рыночных трендов для корректировки стратегии продаж"

• Разбери отдельно «задачи» и «достижения». Достижения с метриками выноси отдельными буллитами.
• ОБЯЗАТЕЛЬНО переноси все числа и метрики из достижений без потерь.
• 5–7 буллитов на место работы.
• Если компания не указана или указано "-" — используй пустую строку для company_name.

ТРЕБОВАНИЯ К languages:
• Каждый язык на ОТДЕЛЬНОЙ строке через \\n.
• Формат: "Язык – Уровень"
• Пример ✅: "Русский – Native\\nАнглийский – Advanced\\nНемецкий – Intermediate"

ТРЕБОВАНИЯ К hard_skills:
• Перечисли навыки через ЗАПЯТУЮ (не через слеш).
• Если пользователь указал мало навыков — добавь 3–5 релевантных для позиции target_position.
• Пример ✅: "Jira, Trello, CRM-системы, Excel, холодные звонки, B2B-продажи"

ТРЕБОВАНИЯ К soft_skills:
• Перечисли через запятую, 4–6 навыков.
• Переформулируй в профессиональные термины.
• Пример: "ответственность" → "высокий уровень ответственности"

ОБРАЗОВАНИЕ И КУРСЫ:
• Образование: education_1, education_2, ... в формате "ВУЗ // Специальность // Годы".
• Курсы: ключ courses — свободный текст.

СФОРМИРУЙ ПОЛЕ "education_details":
• Формат: "<ВУЗ> — <Специальность> (<Годы>)"
• Курсы — отдельной строкой после пустой строки.
• Никакого HTML, только текст с \\n.

⚠️ КРИТИЧНО ДЛЯ JSON:
1) Внутри строковых значений используй \\n для переносов. НЕ реальные переносы.

2) ЭКРАНИРОВАНИЕ КАВЫЧЕК в experiences_json:
   ✅ Правильно: "experiences_json": "[{{\\"company_name\\": \\"Google\\"}}]"
   ❌ Неправильно: незакрытые кавычки

3) ПУСТЫЕ ЗНАЧЕНИЯ — только пустая строка "":
   ✅ "company_name": ""
   ❌ "company_name": "-"

4) НЕТ trailing commas: {{"a": 1}} ✅ | {{"a": 1,}} ❌

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



# ===========================
# JSON fixer
# ===========================
def fix_llm_json(raw: str) -> str:
    """
    Исправляет типичные ошибки JSON от LLM перед парсингом.
    """
    s = raw
    
    # 1. Убираем ``` обёртку
    s = s.strip()
    s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    s = s.strip()
    
    # 2. ГЛАВНЫЙ ФИКС: \"-" → \"-\"
    # Паттерн: \"X" где X — короткое значение БЕЗ скобок
    s = re.sub(
        r'\\"([^"\\{}\[\]]{1,5})"(,|\s*\\")',
        r'\"\1\"\2',
        s
    )
    
    # 3. Trailing commas: убираем запятую перед } или ]
    s = re.sub(r',(\s*[}\]])', r'\1', s)
    
    return s




# ===========================
# helpers
# ===========================
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


# ===========================
# GenAI call and processing
# ===========================
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
                cleaned = fix_llm_json(raw_content)
                data = json.loads(cleaned)
            except json.JSONDecodeError as e:
                logger.error("GenAI вернул не‑JSON (попытка %s): %s", attempt+1, e)
                logger.debug("Raw content (first 500): %s", raw_content[:500])
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

    error_preview = raw_content[:1500] if 'raw_content' in locals() else 'N/A'
    await alert.send(
        f"🔴 LLM JSON Parse Failed\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"Попыток: 3/3\n"
        f"Prompt length: {len(prompt)} chars\n\n"
        f"Raw ответ:\n{error_preview}",
        level="ERROR"
    )

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