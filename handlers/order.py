import asyncio
from aiogram import Router
from aiogram.types import Message
from _utils import logger

from sqlalchemy.orm import Session
from _database import engine
from handlers.db_helpers import create_order, clear_user_answers
from pay_test import new_invoice, register_payment
from _genai import process_order
from _key import get_payment_keyboard


OFFER_TEXT = (
    "📄 Отлично, я собрал всю нужную информацию!\n"
    "Сейчас оформлю профессиональное продающее резюме и подготовлю его в формате PDF и Word.\n\n"
    "💳 Стоимость — 10€ / 200 MDL\n\n"
    "Что ты получишь:\n"
    "✅ Стильное и структурированное резюме\n"
    "✅ Адаптация под вакансию\n"
    "✅ Шаблон письма для отклика\n"
    "✅ Гайд по самопрезентации"
)

router = Router()

async def create_order_and_run_ai(message: Message, user_db_id: int) -> None:
    invoice = new_invoice()
    # 1. создаём заказ
    def _create():
        with Session(engine) as s:
            order = create_order(s, user_db_id, invoice, amount_bani=20000)
            return order.id
    order_id = await asyncio.to_thread(_create)

    # 2. запускаем генерацию
    success = await process_order(order_id)
    if not success:
        await message.answer("⚠️ Не удалось сгенерировать резюме, попробуй позже.")
        return
    
    try: 
        pay_url = register_payment(order_id)
    except Exception as e:
        logger.exception("register_payment failed: %s", e)
        await message.answer("⚠️ Ошибка при создании счёта, попробуй позже.")
        return

    # 3. Предлагаем оплату
    await message.answer(
        OFFER_TEXT,
        reply_markup=get_payment_keyboard(pay_url)
    )