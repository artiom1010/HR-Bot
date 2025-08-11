import re
import asyncio, json, datetime as dt
import httpx
from sqlalchemy.orm import Session
from sqlalchemy import select

from _database import engine, Answer, Order, ProcessedData
from _conf import DO_AGENT_URL, DO_API_TOKEN
from _utils import logger

PROMPT_TEMPLATE = """Ты опытный IT-рекрутер с 20-летней практикой.

Тебе даны сырые ответы соискателя. Перепиши их так, чтобы
выглядело как топ-резюме для вакансий; где не хватает информации –
красиво дополни.

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

Требования к полю  experiences_json:
• Это строка, в которой лежит JSON-массив.
• Каждый элемент массива – объект вида:
  {{
    "job_title_experience": "",
    "employment_period": "",
    "company_name": "",
    "job_responsibilities": ["", …]   // список буллитов
  }}
Пример:
[
  {{
    "job_title_experience": "PRODUCT MANAGER",
    "employment_period": "2024 – Present",
    "company_name": "UNIFUN, Moldova",
    "job_responsibilities": [
      "Общение с клиентами",
      "Разработал систему отчётов на Python"
    ]
  }}
]

Исходные ответы пользователя:
{answers_block}
"""


REQUIRED_KEYS = [
    "cv_naming",
    "career_summary",
    "experiences_json",          # ← новый
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


# async def call_do_genai(prompt: str) -> dict | None:
#     """Выполняет запрос к DO-агенту и возвращает JSON-ответ."""
#     logger.info("PROMPT TO DO:\n%s", prompt)      # ← строка №

#     headers = {
#         "Content-Type": "application/json",
#         "Authorization": f"Bearer {DO_API_TOKEN}"
#     }
#     payload = {"messages": [{"role": "user", "content": prompt}], "stream": False}

#     async with httpx.AsyncClient(timeout=60) as client:
#         try:
#             resp = await client.post(
#                 f"{DO_AGENT_URL}/api/v1/chat/completions",
#                 headers=headers,
#                 json=payload
#             )
#             resp.raise_for_status()

#             raw_content = resp.json()["choices"][0]["message"]["content"]
#             logger.info("RAW REPLY FROM DO:\n%r", raw_content)

#             # ── пытаемся распарсить как JSON ──
#             try:
#                 data = json.loads(raw_content)
#             except json.JSONDecodeError:
#                 cleaned = re.sub(r"^```[a-zA-Z]*\n?", "", raw_content.strip())
#                 cleaned = re.sub(r"\n?```$", "", cleaned).strip()
#                 try:
#                     data = json.loads(cleaned)
#                 except json.JSONDecodeError:
#                     logger.error("GenAI вернул не-JSON")
#                     return None

#             # ── валидация ───────────────────────────────
#             for k in REQUIRED_KEYS:
#                 data.setdefault(k, "")

#             exp = data.get("experiences_json")
#             if isinstance(exp, list):
#                 data["experiences_json"] = json.dumps(exp, ensure_ascii=False)
#             elif isinstance(exp, str):
#                 data["experiences_json"] = exp.strip()
#             else:
#                 data["experiences_json"] = "[]"

#             # 2) теперь (!) приводим остальные значения к строке
#             for k in REQUIRED_KEYS:
#                 if k != "experiences_json":          # не трогаем уже-гото­вый JSON
#                     data[k] = str(data.get(k, ""))
            
#             logger.debug("Fixed experiences_json: %s", data["experiences_json"][:120])

#             # удаляем лишние ключи, если пришли
#             data = {k: data.get(k, "") for k in REQUIRED_KEYS}

#             return data   
#         except (httpx.HTTPError, KeyError) as e:
#             logger.error(f"GenAI error: {e}")
#             return None


import asyncio
import httpx

async def call_do_genai(prompt: str) -> dict | None:
    logger.info("PROMPT TO DO:\n%s", prompt)

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {DO_API_TOKEN}",
    }
    payload = {"messages": [{"role": "user", "content": prompt}], "stream": False}

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

            # парсим JSON (с учётом ```…```)
            try:
                data = json.loads(raw_content)
            except json.JSONDecodeError:
                cleaned = re.sub(r"^```[a-zA-Z]*\n?", "", raw_content.strip())
                cleaned = re.sub(r"\n?```$", "", cleaned).strip()
                data = json.loads(cleaned)  # если снова упадёт — уйдёт в except ниже

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

        except json.JSONDecodeError as e:
            logger.error("GenAI вернул не‑JSON (попытка %s): %s", attempt+1, e)
            # без ретрая обычно смысла нет — но дадим 1 повтор
            await asyncio.sleep(0.8 * (attempt + 1))

        except httpx.HTTPStatusError as e:
            # важное: логируем код и тело
            logger.error("DO HTTP %s: %s", e.response.status_code, e.response.text[:500])
            return None  # статусная ошибка — повторять обычно бесполезно

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