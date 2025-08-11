# import uuid, asyncio
# from sqlalchemy.orm import Session

# from aiogram import Router, F
# from aiogram.filters import CommandStart
# from aiogram.types import Message, CallbackQuery, FSInputFile

# from _database import engine, User, Answer, Order, PaynetStatus
# from _key import get_payment_keyboard, get_download_keyboard, get_dev_payment_keyboard
# from _genai import process_order
# from _pdf_generator import generate_resume
# from pay_test import new_invoice
# from run import bot
# import logging

# # ───────────────────  Бот и роутер  ──────────────────

# router = Router()

# # ───────────────────  DICTIONARIES  ─────────────────
# QUESTION_FLOW = [
#     ("resume_language", "📄 На каком языке ты хотел бы получить итоговое резюме?"),
#     ("resume_goal",     "💼 С какой целью ты создаёшь резюме?"),
#     ("target_position", "🎯 На какую должность подаёшься?"),
#     ("job_description", "🔍 Пришли краткое описание вакансии (если есть)."),
#     ("summary",         "✨ Расскажи о себе в 2–3 предложениях."),
#     ("experience_1",    "💼 Опиши свой последний/текущий опыт работы."),
#     ("experience_2",    "💼 Опиши предыдущий опыт работы (если есть)."),
#     ("experience_3",    "💼 Опиши ещё один опыт работы (если есть)."),
#     ("achievements",    "🏆 Назови до трёх основных достижений."),
#     ("education",       "🎓 Где ты учился? (ВУЗ/курсы)"),
#     ("skills",          "🛠️ Укажи до шести ключевых навыков, каждый с новой строки."),
#     ("languages",       "🌍 Какие языки знаешь и на каком уровне?"),
#     ("full_name", 
#         "🖋 Отлично, я подвёл итоги твоего опыта и навыков.\n"
#         "Теперь осталось заполнить контактную информацию.\n\n"
#         "Укажи своё имя и фамилию:"
#     ),
#     ("contact_phone",   "📞 Твой номер телефона:"),
#     ("contact_email",   "✉️ Твой e-mail для связи:"),
#     ("contact_links",   "🔗 Ссылки (LinkedIn, GitHub и т.п.), через запятую:"),
# ]

# OFFER_TEXT = (
#     "📄 Отлично, я собрал всю нужную информацию!\n"
#     "Сейчас оформлю профессиональное продающее резюме и подготовлю его в формате PDF и Word.\n\n"
#     "💳 Стоимость — 10€ / 200 MDL\n\n"
#     "Что ты получишь:\n"
#     "✅ Стильное и структурированное резюме\n"
#     "✅ Адаптация под вакансию\n"
#     "✅ Шаблон письма для отклика\n"
#     "✅ Гайд по самопрезентации"
# )

# # ───────────────────  Хелперы  ───────────────────────
# def get_first_unanswered(session: Session, user_id: int) -> str | None:
#     """Вернёт key первого незаполненного вопроса или None, если всё пройдено."""
#     answered_keys = {
#         a.question_key for a in
#         session.query(Answer.question_key)
#                .filter(Answer.user_id == user_id).all()
#     }
#     for key, _ in QUESTION_FLOW:
#         if key not in answered_keys:
#             return key
#     return None

# async def send_next_question(chat_id: int, user_db_id: int):
#     """Отправляет следующий вопрос, если он есть."""
#     with Session(engine) as s:
#         next_key = get_first_unanswered(s, user_db_id)
#         if next_key:
#             prompt = dict(QUESTION_FLOW)[next_key]
#             await bot.send_message(chat_id, prompt)
#         else:
#             await bot.send_message(chat_id, "🎉 Спасибо! Все вопросы пройдены.")



# # ───────────────────  /start  ────────────────────────
# @router.message(CommandStart())
# async def start_handler(message: Message):
#     tg_id = message.from_user.id
#     username = message.from_user.username or ""
#     full_name = message.from_user.full_name or ""

#     with Session(engine) as s:
#         user = (
#             s.query(User)
#              .filter(User.telegram_id == tg_id)
#              .first()
#         )
#         if not user:
#             user = User(telegram_id=tg_id, username=username, full_name=full_name)
#             s.add(user)
#             s.commit()

#         user_db_id = user.id

#     await message.answer("👋 Привет! Давай начнём анкету.")
#     await send_next_question(message.chat.id, user_db_id)




# # ──────────────────────  /resume  ──────────────────────
# async def create_order_and_run_ai(message: Message, user_db_id: int) -> None:
#     invoice = new_invoice()

#      # --- 1. создаём order --------------------------------------------------
#     def _create_order() -> int:
#         with Session(engine) as s:
#             order = Order(
#                 user_id       = user_db_id,
#                 invoice       = invoice,
#                 # amount_bani   = 20000,
#                 paynet_status = PaynetStatus.created
#             )
#             s.add(order)
#             s.commit()
#             return order.id
#     order_id = await asyncio.to_thread(_create_order)

#     # --- 2. вызываем ChatGPT ----------------------------------------------
#     if not await process_order(order_id):
#         await message.answer("⚠️ Не удалось сгенерировать резюме, попробуй позже.")
#         return
    
#     print(f"[DEV] Резюме сгенерировано. Введите 'paid' для order_id {order_id}, чтобы подтвердить оплату.")
#     await message.answer(
#         OFFER_TEXT, 
#         reply_markup=get_dev_payment_keyboard(order_id)
#         )

#     # try:
#     #     pay_url = await asyncio.to_thread(register_payment, order_id)  # ← теперь строка-URL
#     # except Exception as e:
#     #     logging.exception("register_payment failed: %s", e)
#     #     await message.answer("⚠️ Ошибка при создании счёта, попробуй позже.")
#     #     return

#     # await message.answer(
#     #     OFFER_TEXT,
#     #     reply_markup=get_payment_keyboard(pay_url)
#     # )



# # ───────────────────  Принимаем ответы  ───────────────
# @router.message(F.text)  # любой текст = ответ
# async def handle_answer(message: Message):
#     tg_id = message.from_user.id
#     text  = message.text.strip()

#     with Session(engine) as s:
#         user = (
#             s.query(User)
#              .filter(User.telegram_id == tg_id)
#              .first()
#         )
#         if not user:
#             await message.answer("Напиши /start сначала, пожалуйста.")
#             return

#         # какой вопрос сейчас без ответа?
#         key = get_first_unanswered(s, user.id)
#         if not key:
#             await message.answer("Все ответы уже собраны. 🚀")
#             return

#         # сохраняем
#         answer = Answer(
#             user_id      = user.id,
#             question_key = key,
#             answer       = text
#         )
#         s.add(answer)
#         s.commit()

#         # спрашиваем следующий
#         # await send_next_question(message.chat.id, user.id)
        
#         next_key = get_first_unanswered(s, user.id)
#         if next_key:
#             prompt = dict(QUESTION_FLOW)[next_key]  
#             await message.answer(prompt)
#         else:
#             # все ответы собраны → предложение оплаты
#             # await message.answer(OFFER_TEXT, reply_markup=get_payment_keyboard())
#             await create_order_and_run_ai(message, user.id)






# # ────────────────────  БЛОК ОПЛАТЫ TEST  ───────────────────────

# async def wait_console_paid(order_id: int) -> bool:
#     """Блокирует stdin в отдельном потоке, ждёт слово 'paid'."""
#     loop = asyncio.get_running_loop()

#     def _ask():
#         return input(f"[DEV] Введите 'paid' для ордера {order_id}: ").strip().lower()

#     answer = await loop.run_in_executor(None, _ask)
#     return answer == "paid"

# @router.callback_query(F.data.startswith("pay_now:"))
# async def pay_now_callback(callback: CallbackQuery):
#     order_id = int(callback.data.split(":")[1])
#     await callback.message.answer("⏳ Ожидаю подтверждение оплаты ...")
#     await callback.answer()
#     # ждём ключевое слово 'paid' в терминале
#     paid = await wait_console_paid(order_id)
#     if not paid:
#         await callback.message.answer("❌ Оплата не подтверждена.")
#         return
#     # 1. обновляем статус
#     from sqlalchemy.orm import Session
#     with Session(engine) as s:
#         order = s.get(Order, order_id)
#         order.paynet_status = PaynetStatus.paid
#         s.commit()
#     # 2. отправляем сообщение + кнопку скачать
#     await callback.message.answer(
#         "💰 Оплата получена! Нажми кнопку ниже, чтобы скачать резюме.",
#         reply_markup=get_download_keyboard(order_id)
#     )

# # @router.callback_query(F.data.startswith("download:"))
# # async def download_callback(callback: CallbackQuery):
# #     order_id = int(callback.data.split(":")[1])
# #     docx_path = generate_resume(order_id)  # если файл уже есть, просто вернёт путь
# #     await callback.message.answer_document(
# #         caption="📄 Ваше резюме (Word)",
# #         document=FSInputFile(docx_path)
# #     )
# #     await callback.answer()






# # ────────────────────  БЛОК ОПЛАТЫ PROD  ───────────────────────







# # _handl.py  (добавьте где-нибудь выше callback-ов, после imports)

# async def send_resume(chat_id: int, order_id: int) -> None:
#     """Отправляет файл, если заказ действительно оплачен."""
#     with Session(engine) as s:
#         order: Order | None = s.get(Order, order_id)

#         # ① нет такого заказа
#         if not order:
#             await bot.send_message(chat_id, "❌ Заказ не найден.")
#             return

#         # ② чужой заказ
#         if order.user.telegram_id != chat_id:
#             await bot.send_message(chat_id, "❌ Это не ваш заказ.")
#             return

#         # ③ ещё не оплачен
#         if order.paynet_status is not PaynetStatus.paid:
#             await bot.send_message(chat_id,
#                                    "💳 Оплата ещё не подтверждена.\n"
#                                    "Проверьте позже, пожалуйста.")
#             return

#     # ④ всё ок → генерим (или берём готовый) и шлём
#     docx_path = generate_resume(order_id)
#     await bot.send_document(chat_id,
#                             FSInputFile(docx_path),
#                             caption="📄 Ваше резюме")


# @router.callback_query(F.data.startswith("download:"))
# async def download_callback(cb: CallbackQuery):
#     order_id = int(cb.data.split(":")[1])
#     await send_resume(cb.from_user.id, order_id)
#     await cb.answer()




# # ────────── callback: «Посмотреть пример» ──────────
# @router.callback_query(F.data == "show_example")
# async def show_example_callback(callback: CallbackQuery):
#     await callback.message.answer("📎 Вот пример того, как будет выглядеть твоё резюме:")
#     await callback.message.answer_document(FSInputFile("files/resume_example.pdf"))
#     await callback.answer()


