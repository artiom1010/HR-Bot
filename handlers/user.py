import asyncio

from aiogram import Router, F
from aiogram.filters import Command,CommandStart
from aiogram.types import Message

from sqlalchemy.orm import Session
from _database import engine, User
from handlers.db_helpers import get_or_create_user, get_first_unanswered, save_answer, clear_user_answers, clear_user_drafts
from handlers.order import create_order_and_run_ai
from handlers.bot_app import bot

QUESTION_FLOW = [
    ("resume_language", "📄 На каком языке ты хочешь получить итоговое резюме?"),
    ("resume_goal",     "💼 C какой целью ты создаёшь резюме?"),
    ("target_position", "🧭 На какую должность ты хочешь податься?"),
    ("job_description", "🔍 Пришли краткое описание вакансии (если есть)."),
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

@router.message(CommandStart())
async def start_handler(message: Message):
    tg_id = message.from_user.id
    username = message.from_user.username or ""
    full_name = message.from_user.full_name or ""

    # синхронно через to_thread, чтобы не блокировать
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
        prompt = dict(QUESTION_FLOW)[next_key]
        await message.answer(prompt)
    else:
        await message.answer("🎉 Все вопросы уже собраны.")
        # если всё заполнено — сразу идём в создание заказа
        await create_order_and_run_ai(message, user_db_id)


# @router.message(F.text)
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
        prompt = dict(QUESTION_FLOW)[next_key]
        await message.answer(prompt)
    else:
        await message.answer("🎉 Спасибо! Всё заполнено.")
        await create_order_and_run_ai(message, user_db_id)



# async def send_first_question(chat_id: int, user_db_id: int):
#     """Очищает всё и отправляет первый вопрос анкеты."""
#     # question_flow определён в этом модуле
#     first_key = QUESTION_FLOW[0][0]
#     prompt = dict(QUESTION_FLOW)[first_key]
#     await bot.send_message(chat_id, prompt)


async def send_first_question(chat_id: int, user_db_id: int):
    first_key = QUESTION_FLOW[0][0]
    await bot.send_message(chat_id, dict(QUESTION_FLOW)[first_key])

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

