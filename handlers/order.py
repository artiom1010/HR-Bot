import asyncio
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, FSInputFile
from _utils import logger
from pathlib import Path

from sqlalchemy.orm import Session
from _database import engine
from handlers.db_helpers import create_order, get_user_lang
from pay_test import new_invoice, register_payment
from _genai import process_order
from _key import get_payment_keyboard

from resume_generator import build_resume
from alerts import alert

from i18n import t
def get_offer_text(lang: str) -> str:
    return t(
        "📄 Отлично, я собрал всю нужную информацию!\n"
        "Сейчас оформлю профессиональное продающее резюме и подготовлю его в формате PDF и Word.\n\n"
        "💳 Стоимость — 10€ / 200 MDL\n\n"
        "Что ты получишь:\n"
        "✅ Стильное и структурированное резюме\n"
        "✅ Адаптация под вакансию\n"
        "✅ Шаблон письма для отклика\n"
        "✅ Гайд по самопрезентации",
        lang
    )

router = Router()

def get_lang_by_user_db_id(user_db_id: int) -> str:
    """Получает язык пользователя по user.id из БД"""
    with Session(engine) as s:
        return get_user_lang(s, user_db_id)

async def create_order_and_run_ai(message: Message, user_db_id: int) -> None:
    lang = await asyncio.to_thread(get_lang_by_user_db_id, user_db_id)

    invoice = new_invoice()
    print("===> invoice:", invoice)
    # 1. создаём заказ
    def _create():
        with Session(engine) as s:
            order = create_order(s, user_db_id, invoice, amount_bani=100)
            return order.id
    order_id = await asyncio.to_thread(_create)

    # 2. запускаем генерацию
    success = await process_order(order_id)
    if not success:
        await message.answer(t("💡 Не удалось сгенерировать резюме.\nПожалуйста, обратитесь в поддержку, отправив команду /support", lang))
        return
    
    try: 
        pay_url = register_payment(order_id)
    except Exception as e:
        logger.exception("register_payment failed: %s", e)
        await message.answer(t("⚠️ Ошибка при создании счёта, попробуй позже.", lang))
        return

    # 3. Предлагаем оплату
    await message.answer(
        get_offer_text(lang),
        reply_markup=get_payment_keyboard(pay_url, lang)
    )


# -- СКАЧИВАНИЕ РЕЗЮМЕ --
@router.callback_query(F.data.startswith("download_pdf:"))
async def download_pdf_callback(callback: CallbackQuery):
    order_id = int(callback.data.split(":")[1])

    def _get_lang():
        with Session(engine) as s:
            from _database import Order
            order = s.get(Order, order_id)
            if not order:
                return "ru"
            return get_user_lang(s, order.user_id)
    lang = await asyncio.to_thread(_get_lang)

    paths = build_resume(order_id, make_pdf=True, make_docx=True)
    if not paths:
        await callback.message.answer(t("Не удалось подготовить резюме. Попробуйте позже.", lang))
        await callback.answer()
        return

    pdf_path  = paths.get("pdf")

    sent = False
    if pdf_path and Path(pdf_path).is_file():
        await callback.message.answer_document(
            document=FSInputFile(pdf_path),
            caption=t("Ваше резюме (PDF)", lang)
        )
        await alert.send_document(
            file_path=pdf_path,
            caption=f"📄 Новое резюме\n👤 @{callback.from_user.username or callback.from_user.id}"
        )
        sent = True

    if not sent:
        await callback.message.answer(t("Файл не найден на диске.", lang))
    await callback.answer()


@router.callback_query(F.data.startswith("download_docx:"))
async def download_docx_callback(callback: CallbackQuery):
    order_id = int(callback.data.split(":")[1])

    def _get_lang():
        with Session(engine) as s:
            from _database import Order
            order = s.get(Order, order_id)
            if not order:
                return "ru"
            return get_user_lang(s, order.user_id)
    lang = await asyncio.to_thread(_get_lang)

    paths = build_resume(order_id, make_pdf=True, make_docx=True)
    if not paths:
        await callback.message.answer(t("Не удалось подготовить резюме. Попробуйте позже.", lang))
        await callback.answer()
        return

    docx_path = paths.get("docx")

    if docx_path and Path(docx_path).is_file():
        await callback.message.answer_document(
            document=FSInputFile(docx_path),
            caption=t("Ваше резюме (Word)", lang)
        )

        sent = True

    if not sent:
        await callback.message.answer(t("Файлы не найдены на диске.", lang))

    await callback.answer()