import asyncio
from aiogram import Router
from aiogram.types import CallbackQuery, FSInputFile
from pathlib import Path

from sqlalchemy.orm import Session
from _database import engine, Order, PaynetStatus
from handlers.db_helpers import mark_order_paid
from handlers.bot_app import bot

from resume_generator import build_resume
from _pdf_generator_lo import generate_resume

router = Router()

from i18n import t
def get_lang_by_chat_id(chat_id: int) -> str:
    """Получает язык пользователя по telegram_id"""
    with Session(engine) as s:
        from _database import User
        from handlers.db_helpers import get_user_lang
        
        user = s.query(User).filter_by(telegram_id=chat_id).first()
        if not user:
            return "ru"
        return get_user_lang(s, user.id)


async def send_resume(chat_id: int, order_id: int) -> None:
    lang = await asyncio.to_thread(get_lang_by_chat_id, chat_id)

    with Session(engine) as s:
        order: Order | None = s.get(Order, order_id)
        if not order:
            await bot.send_message(chat_id, t("❌ Заказ не найден. Попробуйте /start", lang))
            return
        if order.user.telegram_id != chat_id:
            await bot.send_message(chat_id, t("❌ Это не ваш заказ. Попробуйте /start", lang))
            return
        if order.paynet_status is not PaynetStatus.paid:
            await bot.send_message(chat_id,
                                   t("💳 Оплата ещё не подтверждена.\nПроверьте позже, пожалуйста.", lang))
            return
    paths = build_resume(order_id, make_pdf=True, make_docx=True)

    if not paths:
        await bot.send_message(chat_id, t("Не удалось подготовить файл резюме. Попробуйте позже.", lang))
        return

    pdf_path  = paths.get("pdf")
    docx_path = paths.get("docx")

    await bot.send_message(
        chat_id,
        t("💼 Ваше резюме готово! Отправляю PDF и Word.", lang)
    )
    if pdf_path and Path(pdf_path).is_file():
        await bot.send_document(chat_id, FSInputFile(pdf_path), caption="PDF")
    if docx_path and Path(docx_path).is_file():
        await bot.send_document(chat_id, FSInputFile(docx_path), caption="Word (.docx)")

@router.callback_query(lambda c: c.data == "show_example")
async def show_example_callback(callback: CallbackQuery):
    lang = await asyncio.to_thread(get_lang_by_chat_id, callback.from_user.id)
    await callback.message.answer(t( "📎 Вот пример того, как будет выглядеть твоё резюме:", lang))
    await callback.message.answer_document(FSInputFile("files/resume_example.pdf"))
    await callback.answer()