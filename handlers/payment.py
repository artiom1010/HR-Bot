import asyncio
from aiogram import Router
from aiogram.types import CallbackQuery, FSInputFile
from pathlib import Path

from sqlalchemy.orm import Session
from _database import engine, Order, PaynetStatus
from handlers.db_helpers import mark_order_paid
from handlers.bot_app import bot


# from _pdf_generator import generate_resume
from _pdf_generator_lo import generate_resume

router = Router()

async def send_resume(chat_id: int, order_id: int) -> None:
    with Session(engine) as s:
        order: Order | None = s.get(Order, order_id)
        if not order:
            await bot.send_message(chat_id, "❌ Заказ не найден.")
            return
        if order.user.telegram_id != chat_id:
            await bot.send_message(chat_id, "❌ Это не ваш заказ.")
            return
        if order.paynet_status is not PaynetStatus.paid:
            await bot.send_message(chat_id,
                                   "💳 Оплата ещё не подтверждена.\n"
                                   "Проверьте позже, пожалуйста.")
            return

    pdf_path = generate_resume(order_id, to_pdf=True)
    if not pdf_path:
        await bot.send_message(chat_id, "Не удалось подготовить файл резюме. Попробуйте позже.")
    
    ext = Path(pdf_path).suffix.lower()
    if ext == ".pdf":
#         await bot.send_document(
#             chat_id,
#             caption="""
# 💼 Ваше индивидуальное резюме готово!
# 📂 Формат: PDF — готово к отправке HR прямо сейчас.
# \n✨ Используйте этот документ как свой ключ к новым карьерным возможностям.""",
#             document=FSInputFile(pdf_path)
#         )
        await bot.send_message(
            chat_id,
            "💼 Ваше индивидуальное резюме готово!\n"
            "📂 Формат: PDF — готово к отправке HR прямо сейчас.\n\n"
            "✨ Используйте этот документ как свой ключ к новым карьерным возможностям."
        )
    else:
        await bot.send_message(
            chat_id,
            "💼 Ваше индивидуальное резюме готово!\n"
            "📂 Формат: Word — готово к отправке HR прямо сейчас.\n\n"
            "✨ Используйте этот документ как свой ключ к новым карьерным возможностям."
        )
    await bot.send_document(
        chat_id,
        FSInputFile(pdf_path),
        caption=None
    )
        

@router.callback_query(lambda c: c.data and c.data.startswith("download:"))
async def download_callback(callback: CallbackQuery):
    order_id = int(callback.data.split(":", 1)[1])
    await send_resume(callback.from_user.id, order_id)
    await callback.answer()

@router.callback_query(lambda c: c.data == "show_example")
async def show_example_callback(callback: CallbackQuery):
    await callback.message.answer("📎 Вот пример того, как будет выглядеть твоё резюме:")
    await callback.message.answer_document(FSInputFile("files/resume_example.pdf"))
    await callback.answer()