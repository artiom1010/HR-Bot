import asyncio
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, FSInputFile
from _utils import logger
from pathlib import Path

from sqlalchemy.orm import Session
from _database import engine
from handlers.db_helpers import create_order, clear_user_answers
from pay_test import new_invoice, register_payment
from _genai import process_order
from _key import get_payment_keyboard

from resume_generator import build_resume


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
    print("===> invoice:", invoice)
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


# -- СКАЧИВАНИЕ РЕЗЮМЕ --
@router.callback_query(F.data.startswith("download_pdf:"))
async def download_pdf_callback(callback: CallbackQuery):
    order_id = int(callback.data.split(":")[1])

    paths = build_resume(order_id, make_pdf=True, make_docx=True)
    if not paths:
        await callback.message.answer("Не удалось подготовить резюме. Попробуйте позже.")
        await callback.answer()
        return

    pdf_path  = paths.get("pdf")

    sent = False
    if pdf_path and Path(pdf_path).is_file():
        await callback.message.answer_document(
            document=FSInputFile(pdf_path),
            caption="Ваше резюме (PDF)"
        )
        sent = True

    if not sent:
        await callback.message.answer("Файл не найден на диске.")
    await callback.answer()


@router.callback_query(F.data.startswith("download_docx:"))
async def download_docx_callback(callback: CallbackQuery):
    order_id = int(callback.data.split(":")[1])

    paths = build_resume(order_id, make_pdf=True, make_docx=True)
    if not paths:
        await callback.message.answer("Не удалось подготовить резюме. Попробуйте позже.")
        await callback.answer()
        return

    docx_path = paths.get("docx")

    if docx_path and Path(docx_path).is_file():
        await callback.message.answer_document(
            document=FSInputFile(docx_path),
            caption="Ваше резюме (Word)"
        )
        sent = True

    if not sent:
        await callback.message.answer("Файлы не найдены на диске.")

    await callback.answer()