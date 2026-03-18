from typing import List, Tuple, Set

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from _utils import LANG_LIST, LANG_LEVELS
from i18n import t

import logging
log = logging.getLogger(__name__)

def start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Да, поехали!", callback_data="starts")]
        ]
    )


def continue_kb(next_key: str, label: str = "🚀 Продолжить", lang: str = "ru"):
    kb = InlineKeyboardBuilder()
    kb.button(text=t(label, lang), callback_data=f"cont:{next_key}")
    return kb.as_markup()


def experience_intro_kb(lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t("🚀 Готов!", lang), callback_data="experience_continue")],
            [InlineKeyboardButton(text=t("⏭ Нет опыта", lang), callback_data="experience_noexp")]
        ]
    )



def get_payment_keyboard(pay_url: str, lang: str = "ru") -> InlineKeyboardMarkup:
    """
    • 1-я кнопка – сразу открывает форму PayNet
    • 2-я – остаётся прежней «Посмотреть пример»
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t("💳 Перейти к оплате", lang), url=pay_url)],
            [InlineKeyboardButton(text=t("📎 Посмотреть пример", lang), callback_data="show_example")]
        ]
    )


def get_download_keyboard(order_id: int, lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t("📥 Скачать резюме в формате PDF ", lang), callback_data=f"download_pdf:{order_id}")],
            [InlineKeyboardButton(text=t("📥 Скачать резюме в формате DOCX", lang), callback_data=f"download_docx:{order_id}")]
        ]
    )


def get_cover_letter_keyboard(order_id: int, lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text=t("📥 Скачать шаблон письма для отклика", lang),
                callback_data=f"download_cover:{order_id}"
            )]
        ]
    )

# ============= 
# DEV PAYMENT KEYBOARD
# ==============
def get_dev_payment_keyboard(order_id: int, lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t("💳 Оплатить (dev)", lang), callback_data=f"pay_now:{order_id}")]
        ]
    )
# ==============
# ==============
# ==============


# ============== EXPERIENCE KEYBOARDS

def exp_finish_kb(next_index: int, lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t("➕ Добавить ещё опыт", lang), callback_data=f"exp:add:{next_index}")],
            [InlineKeyboardButton(text=t("⏭ Перейти дальше", lang),  callback_data="exp:next")]
        ]
    )


# ============== EDUCATION KEYBOARDS

def edu_start_kb(lang: str = "ru") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=t("🎓 Добавить образование/курсы", lang), callback_data="edu:start:1")
    kb.button(text=t("⏭ Пропустить", lang),                 callback_data="edu:next")
    kb.adjust(1)
    return kb.as_markup()


def edu_finish_kb(next_index: int, lang: str = "ru") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=t("➕ Добавить ещё одно образование", lang), callback_data=f"edu:add:{next_index}")
    kb.button(text=t("⏭ Далее", lang),                          callback_data="edu:next")
    kb.adjust(1)
    return kb.as_markup()


# ============== LANG STEP KEYBOARDS
def lang_actions_kb(lang: str = "ru"):
    kb = InlineKeyboardBuilder()
    kb.button(text=t("➕ Добавить язык", lang), callback_data="lang:add")
    kb.button(text=t("✅ Готово", lang), callback_data="lang:done")
    kb.button(text=t("⏭ Пропустить", lang), callback_data="lang:skip")
    kb.adjust(1)
    return kb.as_markup()

def lang_choose_kb(lang: str = "ru"):
    kb = InlineKeyboardBuilder()
    for code, label in LANG_LIST:
        kb.button(text=t(label, lang), callback_data=f"lang:pick:{code}")
    kb.button(text=t("👤 Свой язык", lang), callback_data="lang:custom")
    kb.button(text=t("⬅︎ Назад", lang), callback_data="lang:back")
    kb.adjust(1)
    return kb.as_markup()

def lang_levels_kb(code: str, lang: str = "ru"):
    kb = InlineKeyboardBuilder()
    for lvl_code, lvl_label in LANG_LEVELS:
        kb.button(text=lvl_label, callback_data=f"lang:level:{code}:{lvl_code}")
    kb.button(text=t("⬅︎ Назад", lang), callback_data="lang:add")
    kb.adjust(1)
    return kb.as_markup()

def lang_levels_custom_kb(lang: str = "ru"):                                     # ← НОВОЕ
    """Клавиатура уровней для кастомного языка (код не нужен)."""
    kb = InlineKeyboardBuilder()
    for lvl_code, lvl_label in LANG_LEVELS:
        kb.button(text=lvl_label, callback_data=f"lang:level_custom:{lvl_code}")
    kb.button(text=t("⬅︎ Назад", lang), callback_data="lang:add")
    kb.adjust(1)
    return kb.as_markup()




def courses_kb(lang: str = "ru") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=t("➕ Добавить курс/сертификат", lang), callback_data="flow:ask:courses")
    kb.button(text=t("⏭ Пропустить", lang),               callback_data="flow:skip:courses")
    kb.adjust(1)
    return kb.as_markup()

# ==============
# FLOW KEYBOARDS
# ==============
def text_or_skip_keyboard(key: str, lang: str = "ru"):  # ← добавь lang
    kb = InlineKeyboardBuilder()
    kb.button(text=t("📤 Отправить вариант", lang), callback_data=f"flow:ask:{key}")
    kb.button(text=t("⏭ Пропустить", lang), callback_data=f"flow:skip:{key}")
    kb.adjust(1)
    return kb.as_markup()


def ms_callback(action: str, qkey: str, value: str | None = None) -> str:
    return f"ms:{action}:{qkey}" + (f":{value}" if value else "")


def build_multiselect_kb(qkey: str, pairs: List[Tuple[str, str]], selected: Set[str], lang: str = "ru"):
    kb = InlineKeyboardBuilder()
    for value, label in pairs:
        checked = "✅" if value in selected else "☐"
        cd = ms_callback("toggle", qkey, value)
        # log.info("CB[%s] len=%d data=%r", qkey, len(cd.encode("utf-8")), cd)
        kb.button(text=f"{checked} {label}", callback_data=ms_callback("toggle", qkey, value))
    kb.adjust(1)
    kb.button(text=t("💾 Готово", lang),    callback_data=ms_callback("done", qkey))
    kb.button(text=t("♻️ Сбросить", lang), callback_data=ms_callback("reset", qkey))
    kb.button(text=t("⏭ Пропустить", lang), callback_data=ms_callback("skip", qkey))
    kb.adjust(1)
    return kb.as_markup()

def add_own_kb(qkey: str, lang: str = "ru"):
    kb = InlineKeyboardBuilder()
    kb.button(text=t("✅ Да, добавить", lang), callback_data=f"ms:add_yes:{qkey}")
    kb.button(text=t("➡️ Нет, достаточно", lang), callback_data=f"ms:add_no:{qkey}")
    kb.adjust(1)
    return kb.as_markup()

# ▼ универсальная клавиатура для одиночных шагов
def single_select_kb(qkey: str, pairs: list[tuple[str, str]], *, allow_personal: bool = False, per_row: int = 1, lang: str = "ru") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for val, lab in pairs:
        kb.button(text=lab, callback_data=f"ans:{qkey}:{val}")
    if allow_personal:
        kb.button(text=t("🧑‍💼 Свой вариант", lang), callback_data=f"free:ask:{qkey}")
    kb.adjust(per_row)
    return kb.as_markup()