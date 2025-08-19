import asyncio

from aiogram import Router, F
from aiogram.filters import Command,CommandStart
from aiogram.types import Message, CallbackQuery

from sqlalchemy.orm import Session
from _database import engine, User
from handlers.db_helpers import get_or_create_user, get_first_unanswered, save_answer, clear_user_answers, clear_user_drafts
from handlers.order import create_order_and_run_ai
from handlers.bot_app import bot
from _key import for_question, CHOICES

QUESTION_FLOW = [
    ("main_language", "🌐 На каком языке тебе будет удобнее общаться со мной?"),
    
    ("resume_language", "📄 На каком языке ты хотел бы получить итоговое резюме?"),
    
    ("resume_goal", """
💼 C какой целью ты создаёшь резюме?
Это поможет мне выбрать правильный стиль, структуру и акценты, чтобы твоё резюме сработало именно под твою задачу.
💬 Например, резюме для смены профессии будет построено так, чтобы выделить твои универсальные навыки и скрыть пробелы в опыте. А резюме для руководящей должности — подчеркнёт лидерские качества, стратегическое мышление и результаты в цифрах.
     """),
    
    ("target_position", """
🧭 На какую должность ты хочешь податься?
(Например: Product Manager, UI/UX Designer, Sales Lead)
    """),
    
    ("job_description", """
🔍 Скинь описание вакансии текстом, на которую хочешь откликнуться.

📌 Зачем это нужно:
Я проанализирую ключевые требования работодателя и помогу сформулировать опыт и достижения так, чтобы они максимально совпадали с ожиданиями.
Это повышает шансы пройти отбор и получить оффер.

Если пока нет — нажми "Пропустить".
    """),
    
    ("summary",         """
✨ Давай сформируем блок “Обо мне”

💡 Когда мы ответим на эти вопросы, я соберу их в сильное, цепляющее описание, например:
Руководитель проектов с 5-летним опытом в IT. Запускаю цифровые продукты под ключ, выстраиваю эффективные команды и процессы. Ориентирован на результат и рост бизнеса.
     
     """),
    
    ("experience_1",    "💼 Опиши свой последний/текущий опыт работы."),
    ("experience_2",    "💼 Опиши предыдущий опыт работы (если есть)."),
    ("experience_3",    "💼 Опиши ещё один опыт работы (если есть)."),
    ("achievements",    "🏆 Назови до трёх основных достижений."),
    ("education",       "🎓 Где ты учился? (ВУЗ/курсы)"),
    ("skills",          "🛠️ Укажи до шести ключевых навыков, каждый с новой строке."),
    ("languages",       "🌍 Какие языки знаешь и на каком уровне?"),
    ("full_name", 
        "🖋 Отлично, я подвёл итоги твоего опыта и навыков.\n"
        "Теперь осталось заполнить контактную информацию.\n\n"
        "Укажи своё имя и фамилию:"
    ),
    ("contact_phone",   "📞 Твой номер телефона:"),
    ("contact_email",   "✉️ Твой e-mail для связи:"),
    ("contact_links",   "🔗 Ссылки (LinkedIn, GitHub и т.п.), через запятую:"),
]

router = Router()


async def send_question(chat_id: int, qkey: str):
    prompt = dict(QUESTION_FLOW)[qkey]
    markup = for_question(qkey)  # вернёт None, если вариантов нет
    await bot.send_message(chat_id, prompt, reply_markup=markup)



@router.callback_query(F.data.startswith("ans:"))
async def handle_choice(callback: CallbackQuery):
    try:
        _, key, value = callback.data.split(":", 2)
    except ValueError:
        await callback.answer("Некорректные данные", show_alert=True)
        return

    tg_id = callback.from_user.id

    def _work():
        with Session(engine) as s:
            user = s.query(User).filter(User.telegram_id == tg_id).first()
            if not user:
                return None, None, "no_user", None
            label = dict(CHOICES.get(key, [])).get(value, value)
            save_answer(s, user.id, key, label)
            next_key = get_first_unanswered(s, user.id, QUESTION_FLOW)
            return user.id, next_key, None, label
    user_db_id, next_key, error, label = await asyncio.to_thread(_work)
    await callback.answer()

    if error == "no_user":
        await callback.message.answer("Напишите /start сначала, пожалуйста.")
        return
    await callback.message.answer(f"Установлен язык резюме: {label}")

    if next_key:
        await send_question(callback.message.chat.id, next_key)
    else:
        await callback.message.answer("Спасибо! Всё заполнено.")
        await create_order_and_run_ai(callback.message, user_db_id)


# ----> КОМАНДА /START ---->
@router.message(CommandStart())
async def start_handler(message: Message):
    tg_id = message.from_user.id
    username = message.from_user.username or ""
    full_name = message.from_user.full_name or ""

    def _work():
        with Session(engine) as s:
            user = get_or_create_user(s, tg_id, username, full_name)
            user_db_id = user.id
            # получаем первый вопрос
            next_key = get_first_unanswered(s, user_db_id, QUESTION_FLOW)
            return user_db_id, next_key
    user_db_id, next_key = await asyncio.to_thread(_work)

    await message.answer("""
👋 Привет!
 Я — твой персональный помощник по созданию продающего одностраничного резюме.
Что это значит?
 Это резюме, которое за 1 страницу убедительно показывает твои сильные стороны, опыт и достижения так, чтобы работодатель захотел пригласить тебя на интервью.
💡 Я соберу твою информацию, задам правильные вопросы и помогу сформулировать ответы так, чтобы они выглядели профессионально и привлекательно.
⏱ Всего 10–15 минут — и ты получишь готовое резюме в PDF и Word, адаптированное под вакансию.
""")
    if next_key:
        await send_question(message.chat.id, next_key)
    else:
        await message.answer("🎉 Все вопросы уже собраны.")
        # если всё заполнено — сразу идём в создание заказа
        await create_order_and_run_ai(message, user_db_id)


@router.message(F.text & ~F.text.startswith("/"))
async def handle_answer(message: Message):
    tg_id = message.from_user.id
    text = message.text.strip()

    def _work():
        with Session(engine) as s:
            user = s.query(User).filter(User.telegram_id == tg_id).first()
            if not user:
                return None, None, "no_user"
            user_db_id = user.id
            current_key = get_first_unanswered(s, user_db_id, QUESTION_FLOW)
            if not current_key:
                return user_db_id, None, "done"
            save_answer(s, user_db_id, current_key, text)
            next_key = get_first_unanswered(s, user_db_id, QUESTION_FLOW)
            return user_db_id, next_key, None

    user_db_id, next_key, error = await asyncio.to_thread(_work)

    if error == "no_user":
        await message.answer("Напиши /start сначала, пожалуйста.")
        return

    if error == "done":
        await message.answer("Все ответы уже собраны. 🚀")
        await create_order_and_run_ai(message, user_db_id)
        return

    if next_key:
        await send_question(message.chat.id, next_key)
    else:
        await message.answer("🎉 Спасибо! Всё заполнено.")
        await create_order_and_run_ai(message, user_db_id)



async def send_first_question(chat_id: int, user_db_id: int):
    first_key = QUESTION_FLOW[0][0]
    await send_question(chat_id, first_key)

@router.message(Command("new_interview"))
async def new_interview_handler(message: Message):
    tg_id = message.from_user.id

    def _reset():
        with Session(engine) as s:
            user = s.query(User).filter(User.telegram_id == tg_id).first()
            if not user:
                return None, 0
            deleted = clear_user_answers(s, user.id)   # ← реальная очистка
            clear_user_drafts(s, user.id)              # ← опционально
            return user.id, deleted

    user_db_id, deleted = await asyncio.to_thread(_reset)
    if not user_db_id:
        await message.answer("Напиши /start сначала, пожалуйста.")
        return

    # для самопроверки
    await message.answer(f"🆕 Начинаем новую анкету! (очищено ответов: {deleted})")
    await send_first_question(message.chat.id, user_db_id)

