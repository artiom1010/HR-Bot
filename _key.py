from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Да, поехали!", callback_data="starts")]
        ]
    )

def get_payment_keyboard(pay_url: str) -> InlineKeyboardMarkup:
    """
    • 1-я кнопка – сразу открывает форму PayNet
    • 2-я – остаётся прежней «Посмотреть пример»
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Перейти к оплате", url=pay_url)],
            [InlineKeyboardButton(text="📎 Посмотреть пример", callback_data="show_example")]
        ]
    )


def get_download_keyboard(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📥 Скачать резюме", callback_data=f"download:{order_id}")]
        ]
    )


def get_dev_payment_keyboard(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Оплатить (dev)", callback_data=f"pay_now:{order_id}")]
        ]
    )


CHOICES = {
    "main_language": [
        ("main_ru", "🇷🇺 Русский"), 
        ("main_en", "🇬🇧 English"), 
        ("main_ro", "🇷🇴 Română")
    ],
    "resume_language": [
        ("ru", "RU — на русском"), 
        ("en", "ENG — на английском"), 
        ("ro", "RO — на румынском")
    ],
    "resume_goal": [
        ("find_job_profession", "Найти новую работу в своей профессии"),
        ("resume_manager_position", "Оформить резюме на руководящую позицию"),
        ("find_first_job", "Найти первую работу"),
        ("career_change", "Перейти в новую сферу (смена профессии)"),
        ("resume_freelance", "Сделать резюме для фриланс/проектной работы"),
        ("update_resume", "Обновить резюме")
    ]
}


def encode_cb(key: str, value: str) -> str:
    return f"ans:{key}:{value}"

def for_question(key: str):
    opts = CHOICES.get(key)
    if not opts:
        return None
    kb = InlineKeyboardBuilder()
    for value, label in opts:
        kb.button(text=label, callback_data=encode_cb(key, value))
    kb.adjust(1)
    return kb.as_markup()


def text_or_skip_keyboard(key: str):
    kb = InlineKeyboardBuilder()
    kb.button(text="📎 Отправить вакансию", callback_data=f"flow:ask:{key}")
    kb.button(text="⏭ Пропустить",       callback_data=f"flow:skip:{key}")
    kb.adjust(2)
    return kb.as_markup()