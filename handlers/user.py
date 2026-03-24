import logging
import re
import asyncio
from aiogram import Router, F
from aiogram.filters import Command,CommandStart
from aiogram.types import Message, CallbackQuery
from sqlalchemy.orm import Session

from handlers.db_helpers import (
    get_or_create_user, get_first_unanswered, get_drafts_values_ordered,
    save_answer, 
    clear_user_answers, clear_user_drafts, clear_user_answer_options,
    clear_parent_drafts, get_draft_value,
    drafts_completed_count,
    drafts_count, add_draft, replace_draft,
    pop_personal_wait, set_personal_wait,
    pop_free_wait, set_free_wait,
    get_user_lang
    )

from handlers.order import create_order_and_run_ai
from handlers.bot_app import bot
from handlers.options_repo import get_options, get_label_for, normalize_position, get_user_context
from handlers.ai_options import (
    chain_generate_after_position,
    generate_experience_tasks_options,
    generate_experience_achievements_examples)

from _database import engine, User
from i18n import t, t_choices

from _key import (
    text_or_skip_keyboard, 
    start_keyboard, 
    single_select_kb, 
    build_multiselect_kb, add_own_kb,
    continue_kb, experience_intro_kb,
    exp_finish_kb,
    edu_start_kb, edu_finish_kb, courses_kb,
    lang_actions_kb, lang_choose_kb, lang_levels_kb, lang_levels_custom_kb
    )

from _utils import (
    logger,
    CHOICES, QUESTION_FLOW, 
    INTRO_BEFORE, START_MESSAGE,
    SUMMARY_STEPS, PERSONAL_MESSAGE,
    EXPERIENCE_STEPS, ACHIEVEMENTS_FALLBACK_EXAMPLES,
    EDUCATION_STEPS, EDUCATION_INTRO,
    COURSES_INTRO, COURSES_ASK_TEXT,
    SKILLS_MSG_HARD, SKILLS_MSG_TOOLS, 
    LANGS_MSG, LANG_LIST, LANG_LEVELS,
    PERSONAL_MESSAGE_POSITION, USE_KEYBOARD_MSG,
    KEYBOARD_ONLY_MSG)

from _utils import (
    MSG_ONLY_START, MSG_NO_EXPERIENCE,
    MSG_THANKS_RESUME, MSG_ONLY_TEXT,
    MSG_RATE_WORD_COUNT, MSG_POSITION_TOO_LONG, MSG_EXAMPLE_POSITION,
    MSG_EXPERIENCE_TOO_LONG, MSG_EXAMPLE_EXPERIENCE, MSG_ALL_ANSWERS_DONE,
    MSG_SAVED_VARIANTS, MSG_CHOOSE_LANG_LEVEL, MSG_CHOOSE_LVL,
    MSG_ACHIEVEMENTS, MSG_ACHMENT_EXAMPLES, MSG_ONE_WORK,
    MSG_MANY_WORKS, MSG_ONE_EDUCATION, MSG_MANY_EDUCATIONS,
    MSG_MANY_COURSES, MSG_LANGUAGE_EXAMPLE, MSG_LANGUAGE_ADDED,
    MSG_LANGUAGE_SAVED, MSG_LANGUAGE_CHOSEN, MSG_SAVING_PROGRESS,
    MSG_CURRENTLY_SELECTED, MSG_ADD_MORE, MSG_CHOOSE_AT_LEAST_ONE,
    MSG_SUPPORT, MSG_POLICY
)

from handlers.ms_repo import (
    get_selected_set, 
    toggle_selection, 
    clear_selection, 
    build_answer_text_from_selection)

from handlers.validators import (
    _too_many_words, validate_field
)


router = Router()

# --- constants --- 
SUMMARY_SINGLE_WITH_PERSONAL = {"summary_industry"}
MULTI_SELECT_KEYS = {"summary_tasks", "summary_values", "experience_tasks", "skills_tools"}
PAUSE_BEFORE = {"summary", "skills"}

WORD_LIMIT = 100
JOB_DESC_WORD_LIMIT = 10000
TRGT_POSITION_MAX = 50
EXP_POSITION_MAX  = 50



# ──────────────────────────────────────────────────────────────────────────────
# Gate
# ──────────────────────────────────────────────────────────────────────────────
BUTTON_TEXT_STEPS = {"job_description"}
FREE_INPUT_WITH_BUTTON = {"contact_links"}

# >>> НОВОЕ: мастер-шаги, где по умолчанию ожидаются КНОПКИ
BUTTON_ONLY_MASTER_STEPS = {
    "main_language",
    "resume_language",
    "resume_goal",
    "summary",
    "experience",
    "education",
    "courses",
    "skills",
    "languages",
}
def _is_button_step(session: Session, user_id: int, qkey: str) -> bool:
    if not qkey:
        return False
    base = qkey.split("::", 1)[1] if "::" in qkey else qkey
    if base in BUTTON_TEXT_STEPS:
        return False
    if base in FREE_INPUT_WITH_BUTTON:  # 👈 новое
        return True
    if base in MULTI_SELECT_KEYS or base.startswith("summary_"):
        return True
    pairs = get_options(base, user_id, None, None)
    return bool(pairs)



_text_gate: dict[int, str] = {}

glog = logging.getLogger("gate")

def open_text_gate(user_id: int, key: str) -> None:
    _text_gate[user_id] = key
    glog.debug("gate OPEN user_id=%s key=%s", user_id, key)

def close_text_gate(user_id: int) -> None:
    if user_id in _text_gate:
        glog.debug("gate CLOSE user_id=%s key=%s", user_id, _text_gate[user_id])
    _text_gate.pop(user_id, None)

def gate_for(user_id: int) -> str | None:
    return _text_gate.get(user_id)









# ──────────────────────────────────────────────────────────────────────────────
# Антиспам-таймаут для "используй кнопки"
import time

_last_warn_ts: dict[int, float] = {}
WARN_TTL_SEC = 15

def _should_warn(user_id: int) -> bool:
    now = time.time()
    last = _last_warn_ts.get(user_id, 0)
    if now - last >= WARN_TTL_SEC:
        _last_warn_ts[user_id] = now
        return True
    return False






# ──────────────────────────────────────────────────────────────────────────────
# Утилиты
# ──────────────────────────────────────────────────────────────────────────────

PERSONAL_FOR_KEYS = {"experience_title", "summary_industry"}

def personal_msg_for(qkey: str) -> str:
    t = (qkey or "").strip()
    base = t.split("::", 1)[-1]
    if base in PERSONAL_FOR_KEYS or any(k in t for k in PERSONAL_FOR_KEYS):
        return PERSONAL_MESSAGE_POSITION
    return PERSONAL_MESSAGE

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

@router.message(Command("support"))
async def cmd_support(message: Message):
    tg_id = message.from_user.id

    def _get_lang():
        with Session(engine) as s:
            user = s.query(User).filter_by(telegram_id=tg_id).first()
            return get_user_lang(s, user.id) if user else "ro"
    lang = await asyncio.to_thread(_get_lang)

    await message.answer(t(MSG_SUPPORT, lang), parse_mode="HTML")



@router.message(Command("policy"))
async def cmd_policy(message: Message):
    tg_id = message.from_user.id
    
    def _get_lang():
        with Session(engine) as s:
            user = s.query(User).filter_by(telegram_id=tg_id).first()
            return get_user_lang(s, user.id) if user else "ro"
    lang = await asyncio.to_thread(_get_lang)
    
    # base = f"https://turbocv.md/{lang}"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🔒 Privacy Policy",
            url="https://turbocv.md/privacy"
            # url=f"{base}/privacy"
        )],
        [InlineKeyboardButton(
            text="📋 Terms of Use",
            url="https://turbocv.md/terms-of-use"
            # url=f"{base}/terms-of-use"
        )],
        [InlineKeyboardButton(
            text="🍪 Data Processing Consent",
            url="https://turbocv.md/data-processing"
            # url=f"{base}/data-processing"
        )]
    ])
    
    await message.answer(t(MSG_POLICY, lang), reply_markup=keyboard)
    
# ──────────────────────────────────────────────────────────────────────────────
# Навигация по вопросам
# ──────────────────────────────────────────────────────────────────────────────
async def send_next_with_intro(chat_id: int, next_key: str, user_id: int | None = None):
    # спец-случай для experience
    
    def _get_lang():
        with Session(engine) as s:
            return get_user_lang(s, user_id) if user_id else "ru"
    lang = await asyncio.to_thread(_get_lang) if user_id else "ru"


    if next_key == "experience":
        await bot.send_message(
            chat_id,
            t(INTRO_BEFORE["experience"], lang),
            reply_markup=experience_intro_kb(lang),
        )
        if user_id:
            open_text_gate(user_id, "intro:experience")
        return

    intro = INTRO_BEFORE.get(next_key)
    if intro and next_key in PAUSE_BEFORE:
        await bot.send_message(chat_id, t(intro, lang), reply_markup=continue_kb(next_key, lang=lang))
        if user_id:
            open_text_gate(user_id, f"intro:{next_key}")
        return
    if intro:
        await bot.send_message(chat_id, t(intro, lang))
    await send_question(chat_id, next_key, user_id)


@router.callback_query(F.data == "experience_continue")
async def on_experience_continue(cb: CallbackQuery):
    tg_id = cb.from_user.id

    def _get_user_and_lang():
        with Session(engine) as s:
            user = s.query(User).filter_by(telegram_id=tg_id).first()
            if not user:
                return None, "ru"
            return user.id, get_user_lang(s, user.id)
    
    user_id, lang = await asyncio.to_thread(_get_user_and_lang)
    
    if not user_id:
        await cb.message.answer(t(MSG_ONLY_START, lang))
        return

    await cb.message.edit_reply_markup(reply_markup=None)
    await ask_next_experience_step(cb.message.chat.id, user_id, exp_index=1)
    await cb.answer()


@router.callback_query(F.data == "experience_noexp")
async def on_experience_noexp(cb: CallbackQuery):
    tg_id = cb.from_user.id

    def _work():
        with Session(engine) as s:
            user = s.query(User).filter_by(telegram_id=tg_id).first()
            if not user:
                return None, "ru"
            save_answer(s, user.id, "experience", "no_experience")
            s.commit()
            lang = get_user_lang(s, user.id)
            return user.id, lang

    user_id, lang = await asyncio.to_thread(_work)
    
    if not user_id:
        await cb.message.answer(t(MSG_ONLY_START, lang))
        return

    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.message.answer(t(MSG_NO_EXPERIENCE, lang))
    await send_next_with_intro(cb.message.chat.id, "education", user_id)
    await cb.answer()








async def send_first_question(chat_id: int, user_db_id: int):
    first_key = QUESTION_FLOW[0][0]
    await send_question(chat_id, first_key, user_db_id)



async def send_question(chat_id: int, qkey: str, user_id: int | None = None):
    PROMPTS = dict(QUESTION_FLOW)
    
    # Получаем язык пользователя
    def _get_lang():
        with Session(engine) as s:  
            return get_user_lang(s, user_id) if user_id else "ru"
    lang = await asyncio.to_thread(_get_lang)

    # ▶ summary → мини-мастер
    if qkey == "summary" and user_id:
        await bot.send_message(chat_id, t(PROMPTS[qkey], lang))
        def _reset():
            with Session(engine) as s:
                clear_parent_drafts(s, user_id, "summary")
                s.commit()
        await asyncio.to_thread(_reset)
        await ask_next_summary_step(chat_id, user_id)
        return

    # ▶ experience → мини-мастер
    if qkey == "experience" and user_id:
        def _reset():
            with Session(engine) as s:
                clear_parent_drafts(s, user_id, "exp1")
                s.commit()
        await asyncio.to_thread(_reset)
        def _mark_done():
            with Session(engine) as s:
                save_answer(s, user_id, "experience", "")
        await asyncio.to_thread(_mark_done)
        await ask_next_experience_step(chat_id, user_id, exp_index=1)
        return

    # ▶ education → старт с клавиатурой
    if qkey == "education" and user_id:
        await bot.send_message(chat_id, t(EDUCATION_INTRO, lang), reply_markup=edu_start_kb(lang))
        # здесь клавиатура → включаем ворота
        open_text_gate(user_id, "education")
        def _mark_done():
            with Session(engine) as s:
                save_answer(s, user_id, "education", "")
        await asyncio.to_thread(_mark_done)
        return

    # ▶ courses → кнопки
    if qkey == "courses" and user_id:
        def _reset():
            with Session(engine) as s:
                clear_parent_drafts(s, user_id, "courses")
                s.commit()
        await asyncio.to_thread(_reset)
        def _mark_done():
            with Session(engine) as s:
                save_answer(s, user_id, "courses", "")
        await asyncio.to_thread(_mark_done)
        await bot.send_message(chat_id, t(COURSES_INTRO, lang), reply_markup=courses_kb(lang))
        # здесь клавиатура → включаем ворота
        open_text_gate(user_id, "courses")
        return

    # ▶ skills → мини-мастер
    if qkey == "skills" and user_id:
        def _reset():
            with Session(engine) as s:
                clear_parent_drafts(s, user_id, "skills")
                s.commit()
        await asyncio.to_thread(_reset)
        await ask_next_skills_step(chat_id, user_id)
        return

    # Общий случай
    pairs = await asyncio.to_thread(get_options, qkey, user_id, None, None)
    pairs = t_choices(pairs, lang)
    markup = None

    if qkey in MULTI_SELECT_KEYS and user_id and pairs:
        def _sel():
            with Session(engine) as s:
                return get_selected_set(s, user_id, qkey)
        selected = await asyncio.to_thread(_sel)
        pairs = [(v, l) for (v, l) in (pairs or []) if _fits_keyboard_label(l)]
        markup = build_multiselect_kb(qkey, pairs, selected, lang)

    elif pairs:
        allow_personal = qkey in SUMMARY_SINGLE_WITH_PERSONAL
        markup = single_select_kb(qkey, pairs, allow_personal=allow_personal, per_row=1, lang=lang)

    elif qkey in ("job_description", "contact_links"):
        markup = text_or_skip_keyboard(qkey, lang)

    await bot.send_message(chat_id, t(PROMPTS[qkey], lang), reply_markup=markup)

    # ВАЖНО: если это клавиатурный шаг — включаем ворота
    if user_id and markup:
        if qkey not in ["job_description"]:
            open_text_gate(user_id, qkey)









# ──────────────────────────────────────────────────────────────────────────────
# Обработчик одиночного выбора кнопки / сохранение ответа
# ──────────────────────────────────────────────────────────────────────────────
@router.callback_query(F.data.startswith("ans:"))
async def handle_choice(callback: CallbackQuery):
    await callback.answer()

    _, rest = callback.data.split(":", 1)
    key, value = rest.rsplit(":", 1)

    tg_id = callback.from_user.id

    # обработка кнопки «Пропустить»
    if key == "skip":
        skip_key = value  # какой шаг пропускаем (напр. "contact_links")

        def _work_skip():
            with Session(engine) as s:
                user = s.query(User).filter_by(telegram_id=tg_id).first()
                if not user:
                    return None, None, "no_user"
                # помечаем шаг как пройденный пустым значением
                save_answer(s, user.id, skip_key, "")
                next_key = get_first_unanswered(s, user.id, QUESTION_FLOW)
                return user.id, next_key, None

        user_id, next_key, error = await asyncio.to_thread(_work_skip)
        if user_id:
            close_text_gate(user_id)

        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception as e:
            logger.debug("edit_reply_markup skip: %s", e)

        if error == "no_user":
            await callback.message.answer(t(MSG_ONLY_START, lang))
            return

        if next_key:
            await send_next_with_intro(callback.message.chat.id, next_key, user_id)
        else:
            await callback.message.answer(t(MSG_THANKS_RESUME, lang))
            await create_order_and_run_ai(callback.message, user_id)
        return


    def _work():
        with Session(engine) as s:
            user = s.query(User).filter_by(telegram_id=tg_id).first()
            if not user:
                return None, None, "no_user", None
            
            label_db = get_label_for(s, key, value, user.id)
            label = label_db or dict(CHOICES.get(key, [])).get(value, value)

            if key.startswith("exp") and "::" in key:
                parent, step = key.split("::", 1)

                base_key = step
                label_db = get_label_for(s, base_key, value, user.id)
                label_raw = label_db or dict(CHOICES.get(base_key, [])).get(value, value)

                lang = get_user_lang(s, user.id)
                label = t(label_raw, lang)

                add_draft(s, user.id, parent, step, label)
                s.commit()

                saved_marker = "__experience_title_saved__" if step == "experience_title" else "__experience_step_saved__"
                return user.id, f"{saved_marker}:{parent}", None, label

            # ▶ Подвопросы summary
            if key.startswith("summary_"):
                label_raw = get_label_for(s, key, value, user.id)
                lang = get_user_lang(s, user.id)
                label = t(label_raw, lang)
                add_draft(s, user.id, "summary", key, label)
                s.commit()
                return user.id, "summary_step_saved", None, label

            # ♦ Обычные вопросы
            label_raw = dict(CHOICES.get(key, [])).get(value, value)
            lang = get_user_lang(s, user.id)
            label = t(label_raw, lang)
            save_answer(s, user.id, key, label)
            next_key = get_first_unanswered(s, user.id, QUESTION_FLOW)
            return user.id, next_key, None, label

    user_id, next_key, error, echo_label = await asyncio.to_thread(_work)
    if user_id:
        close_text_gate(user_id)

    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception as e:
        logger.debug("edit_reply_markup skip: %s", e)

    if error == "no_user":
        await callback.message.answer(t(MSG_ONLY_START, lang))
        return

    if echo_label:
        # Получаем язык пользователя
        def _get_lang():
            with Session(engine) as s:
                return get_user_lang(s, user_id) if user_id else "ru"
        lang = await asyncio.to_thread(_get_lang)

        translated_label = t(echo_label, lang)
        await callback.message.answer(f"{t(MSG_SAVED_VARIANTS, lang)} {translated_label}")

    if isinstance(next_key, str) and next_key.startswith("__experience_title_saved__"):
        parent = next_key.split(":", 1)[1]
        exp_index = int(parent.replace("exp", "") or "1")
        # 🔔 ХУК: генерация задач и достижений для опыта работы
        asyncio.create_task(generate_experience_tasks_options(user_id, echo_label))

        async def _gen_achievements_early_cb():
            from handlers.ai_options import get_user_identity
            from alerts import alert
            labels = await generate_experience_achievements_examples(user_id, echo_label, tasks_text="")
            username, full_name, _ = get_user_identity(user_id)
            txt = "\n".join([
                f"Username: @{username or '-'}",
                f"Full name: {full_name or '-'}",
                f"Experience role: {echo_label}",
                "⚡ Early (no tasks yet)",
                "Achievements:",
                "- " + "\n- ".join(labels or []),
            ])
            await alert.send(txt, level="INFO")

        asyncio.create_task(_gen_achievements_early_cb())
        await ask_next_experience_step(callback.message.chat.id, user_id, exp_index=exp_index)
        return

    if next_key == "summary_step_saved":
        await ask_next_summary_step(callback.message.chat.id, user_id)
        return

    if next_key:
        await send_next_with_intro(callback.message.chat.id, next_key, user_id)
    else:
        await callback.message.answer(t(MSG_THANKS_RESUME, lang))
        await create_order_and_run_ai(callback.message, user_id)



# ──────────────────────────────────────────────────────────────────────────────
# /start
# ──────────────────────────────────────────────────────────────────────────────
@router.message(CommandStart())
async def start_handler(message: Message):
    tg_id = message.from_user.id
    username = message.from_user.username or ""
    full_name = message.from_user.full_name or ""

    await asyncio.to_thread(lambda: (Session(engine),))
    def _work():
        with Session(engine) as s:
            get_or_create_user(s, tg_id, username, full_name)
    await asyncio.to_thread(_work)
    # await message.answer(START_MESSAGE,reply_markup=start_keyboard())
    await send_question(message.chat.id, "main_language", None)



@router.callback_query(F.data == "starts")
async def handle_start(callback: CallbackQuery):
    await callback.answer()
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception as e:
        logger.debug("edit_reply_markup skip: %s", e)

    tg_id = callback.from_user.id
    
    def _work():
        with Session(engine) as s:
            user = s.query(User).filter(User.telegram_id == tg_id).first()
            if not user:
                user = get_or_create_user(s, tg_id, callback.from_user.username or "", callback.from_user.full_name or "")
            next_key = get_first_unanswered(s, user.id, QUESTION_FLOW)
            lang = get_user_lang(s, user.id)  # 👈
            return user.id, next_key, lang

    user_id, next_key, lang = await asyncio.to_thread(_work)  # 👈

    if next_key:
        await send_next_with_intro(callback.message.chat.id, next_key, user_id)
    else:
        await callback.message.answer(t(MSG_THANKS_RESUME, lang))
        await create_order_and_run_ai(callback.message, user_id)


# ──────────────────────────────────────────────────────────────────────────────
# Ответы текстом
# ──────────────────────────────────────────────────────────────────────────────
@router.message(~F.text)  # любое не-текстовое
async def handle_non_text(message: Message):
    def _get_lang():
        with Session(engine) as s:
            user = s.query(User).filter_by(telegram_id=message.from_user.id).first()
            if not user:
                return "ru"
            return get_user_lang(s, user.id)
    
    lang = await asyncio.to_thread(_get_lang)
    await message.answer(t(MSG_ONLY_TEXT, lang))

def _limit_for_key(step_key: str | None) -> int:
    if not step_key:
        return WORD_LIMIT
    base = step_key.split("::", 1)[1] if "::" in step_key else step_key
    return JOB_DESC_WORD_LIMIT if base == "job_description" else WORD_LIMIT


TMP_PARENT = "__tmp__"

def _norm_qkey(q: str) -> str:
    return (q or "").lstrip(":").strip()

def _tmp_key_for(qkey: str) -> str:
    return f"ms::{_norm_qkey(qkey)}"

def _store_tmp_selection(s, user_id: int, qkey: str, text: str) -> None:
    replace_draft(s, user_id, parent_key=TMP_PARENT, step_key=_tmp_key_for(qkey), value=text or "")

def _load_tmp_selection(s, user_id: int, qkey: str) -> str:
    return (get_draft_value(s, user_id, parent_key=TMP_PARENT, step_key=_tmp_key_for(qkey)) or "").strip()

def _clear_tmp_selection(s, user_id: int, qkey: str) -> None:
    replace_draft(s, user_id, parent_key=TMP_PARENT, step_key=_tmp_key_for(qkey), value="")


def _finalize_multiselect_text(s, user_id: int, qkey: str, final_text: str):
    """
    Кладёт final_text туда, куда нужно для данного qkey,
    чистит выбор и возвращает next_key-маркер, как твой старый код.
    """
    qkey = _norm_qkey(qkey)

    if "::" in qkey and qkey.startswith("exp"):
        parent, step = qkey.split("::", 1)
        replace_draft(s, user_id, parent_key=parent, step_key=step, value=final_text)
        clear_selection(s, user_id, qkey)
        s.commit()
        return f"__continue_experience__:{parent}"

    if qkey.startswith("summary_"):
        replace_draft(s, user_id, parent_key="summary", step_key=qkey, value=final_text)
        clear_selection(s, user_id, qkey)
        s.commit()
        return "__continue_summary__"

    if qkey.startswith("skills_"):
        replace_draft(s, user_id, parent_key="skills", step_key=qkey, value=final_text)
        clear_selection(s, user_id, qkey)
        s.commit()
        return "__continue_skills__"

    # обычные шаги
    save_answer(s, user_id, qkey, final_text)
    clear_selection(s, user_id, qkey)
    next_key = get_first_unanswered(s, user_id, QUESTION_FLOW)
    s.commit()
    return next_key



@router.message(F.text & ~F.text.startswith("/"))
async def handle_answer(message: Message, user_id: int | None = None):
    tg_id = message.from_user.id
    raw_text = (message.text or "").strip()

    # 1) получаем user_id по tg_id
    def _uid():
        with Session(engine) as s:
            u = s.query(User).filter_by(telegram_id=tg_id).first()
            return u.id if u else None
    user_id = await asyncio.to_thread(_uid)

    if not user_id:
        await message.answer(t(MSG_ONLY_START, lang))
        return
    
    # Получаем язык пользователя
    def _get_lang():
        with Session(engine) as s:
            return get_user_lang(s, user_id) if user_id else "ru"
    lang = await asyncio.to_thread(_get_lang)




    # ------- утилиты -------
    def _merge_ms_and_free(ms_text: str, user_text: str) -> str:
        """Склеиваем выбранные пункты мультиселекта и ручной ввод пользователя."""
        user_text = (user_text or "").strip()
        if not user_text:
            return (ms_text or "").strip()
        parts = [p.strip() for p in re.split(r"[,\n;]+", user_text) if p.strip()]
        human = ", ".join(parts) if parts else user_text
        return f"{ms_text}, {human}" if (ms_text and human) else (ms_text or human)

    def _get_tmp_ms_text(s, user_id: int, qkey: str) -> str:
        return get_draft_value(s, user_id, parent_key="__tmp__", step_key=f"ms::{qkey}") or ""

    def _clear_tmp_ms_text(s, user_id: int, qkey: str):
        replace_draft(s, user_id, parent_key="__tmp__", step_key=f"ms::{qkey}", value="")
        s.commit()

    # ------- основная работа в потоке -------
    def _work(text_in: str):
        with Session(engine) as s:
            user = s.query(User).filter_by(telegram_id=tg_id).first()
            if not user:
                return None, None, "no_user", None

            free_qkey     = pop_free_wait(s, user.id)
            awaiting_step = pop_personal_wait(s, user.id)
            s.commit()

            if not free_qkey and not awaiting_step:
                current_key = get_first_unanswered(s, user.id, QUESTION_FLOW)

                need_button = current_key in BUTTON_ONLY_MASTER_STEPS

                if current_key == "summary":
                    idx = drafts_count(s, user.id, "summary")
                    if 0 <= idx < len(SUMMARY_STEPS):
                        sub_key, _ = SUMMARY_STEPS[idx]
                        need_button = True

                if not need_button:
                    need_button = _is_button_step(s, user.id, current_key)

                if need_button:
                    return user.id, current_key, "need_button", None

            # лимиты длины
            step_for_limit = free_qkey or awaiting_step or get_first_unanswered(s, user.id, QUESTION_FLOW)
            lim = _limit_for_key(step_for_limit)
            if _too_many_words(text_in, lim):
                return user.id, None, "too_many_words", lim

            # 1) комбинирование после «Да, добавить»
            if free_qkey and free_qkey.startswith("addown::"):
                qkey_for_merge = _norm_qkey(free_qkey.split("::", 1)[1])
                ms_text = _get_tmp_ms_text(s, user.id, qkey_for_merge)
                parts   = [p.strip() for p in re.split(r"[,\n;]+", text_in) if p.strip()]
                human   = ", ".join(parts) if parts else text_in
                
                final   = _merge_ms_and_free(ms_text, human)
                next_key = _finalize_multiselect_text(s, user.id, qkey_for_merge, final)
                _clear_tmp_ms_text(s, user.id, qkey_for_merge)
                # return user.id, next_key, None, qkey_for_merge
                return user.id, next_key, None, f"__echo__::{final}"

            # 2) experience: свободный ввод подшагов
            if free_qkey and free_qkey.startswith("exp") and "::" in free_qkey:
                parent, step = free_qkey.split("::", 1)
                parts = [p.strip() for p in re.split(r"[,\n;]+", text_in) if p.strip()]
                human = ", ".join(parts) if parts else text_in
                if step == "experience_title" and len(human) > EXP_POSITION_MAX:
                    return user.id, None, "exp_title_too_long", None
                replace_draft(s, user.id, parent_key=parent, step_key=step, value=human)
                s.commit()
                saved_marker = "__experience_title_saved__" if step == "experience_title" else "__experience_step_saved__"
                return user.id, f"{saved_marker}:{parent}", None, human 

            # 3) summary: свой вариант (старый single-select)
            if free_qkey and free_qkey.startswith("summary_"):
                add_draft(s, user.id, "summary", free_qkey, text_in)
                s.commit()
                return user.id, "summary_step_saved", None, free_qkey

            # 4) education
            if free_qkey and free_qkey.startswith("edu") and "::" in free_qkey:
                parent, step = free_qkey.split("::", 1)
                parts = [p.strip() for p in re.split(r"[,\n;]+", text_in) if p.strip()]
                human = ", ".join(parts) if parts else text_in
                replace_draft(s, user.id, parent_key=parent, step_key=step, value=human)
                s.commit()
                return user.id, f"__edu_step_saved__:{parent}", None, step

            # 5) courses
            if free_qkey and free_qkey.startswith("courses") and "::" in free_qkey:
                parts = [p.strip() for p in re.split(r"[,\n;]+", text_in) if p.strip()]
                human = ", ".join(parts) if parts else text_in
                add_draft(s, user.id, parent_key="courses", step_key="course_item", value=human)
                s.commit()
                return user.id, "__courses_step_saved__", None, "courses"

            # 6) skills: свой вариант (single-select)
            if free_qkey and free_qkey.startswith("skills_"):
                parts = [p.strip() for p in re.split(r"[,\n;]+", text_in) if p.strip()]
                human = ", ".join(parts) if parts else text_in
                replace_draft(s, user.id, parent_key="skills", step_key=free_qkey, value=human)
                s.commit()
                return user.id, "__continue_skills__", None, free_qkey

            # 7) кастомный язык → спросить уровень
            if free_qkey == "lang_custom":
                name = _norm_lang_label(text_in)
                replace_draft(s, user.id, parent_key="skills", step_key="skills_langs__tmp_name", value=name)
                s.commit()
                return user.id, "__ask_level_for_custom_lang__", None, name

            # 8) валидация контактов
            if free_qkey in ("full_name", "contact_phone", "contact_email", "contact_links"):
                ok, normalized, err, hint = validate_field(free_qkey, text_in, lang)
                if not ok:
                    set_free_wait(s, user.id, free_qkey)  # оставляем ворота открытыми
                    s.commit()
                    return user.id, None, "bad_input", hint
                text_in = normalized

            # 9) обычный свободный ввод
            if free_qkey:
                save_answer(s, user.id, free_qkey, text_in)
                next_key = get_first_unanswered(s, user.id, QUESTION_FLOW)
                return user.id, next_key, None, free_qkey

            # 10) personal_wait (старый single-select «Свой вариант»)
            if awaiting_step:
                parts = [p.strip() for p in re.split(r"[,\n;]+", text_in) if p.strip()]
                human = ", ".join(parts) if parts else text_in
                if awaiting_step.startswith("summary_"):
                    replace_draft(s, user.id, parent_key="summary", step_key=awaiting_step, value=human)
                    s.commit()
                    return user.id, "summary_step_saved", None, awaiting_step
                if awaiting_step.startswith("skills_"):
                    replace_draft(s, user.id, parent_key="skills", step_key=awaiting_step, value=human)
                    s.commit()
                    return user.id, "__continue_skills__", None, awaiting_step

            # 11) обычный текущий шаг
            current_key = get_first_unanswered(s, user.id, QUESTION_FLOW)

            if current_key == "target_position" and len(text_in) > TRGT_POSITION_MAX:
                return user.id, None, "target_too_long", None

            word_limit = JOB_DESC_WORD_LIMIT if current_key == "job_description" else WORD_LIMIT
            if _too_many_words(text_in, word_limit):
                return user.id, None, "too_many_words", word_limit

            if current_key in ("full_name", "contact_phone", "contact_email", "contact_links"):
                ok, normalized, err, hint = validate_field(current_key, text_in, lang)
                if not ok:
                    return user.id, None, "bad_input", hint
                text_to_save = normalized
            else:
                text_to_save = text_in

            if not current_key:
                return user.id, None, "done", None

            # подмастер summary
            if current_key == "summary":
                idx = drafts_count(s, user.id, "summary")
                if idx < len(SUMMARY_STEPS):
                    step_key, _ = SUMMARY_STEPS[idx]
                    add_draft(s, user.id, "summary", step_key, text_to_save)
                    s.commit()
                    return user.id, "summary_step_saved", None, step_key

            # обычный ответ
            save_answer(s, user.id, current_key, text_to_save)
            next_key = get_first_unanswered(s, user.id, QUESTION_FLOW)
            return user.id, next_key, None, current_key

    async def _run_with_retry():
        for i in range(3):
            try:
                return await asyncio.to_thread(_work, raw_text)
            except Exception as e:
                if "database is locked" in str(e):
                    await asyncio.sleep(0.15 * (i + 1))
                    continue
                raise
        return await asyncio.to_thread(_work, raw_text)

    # ------- запуск -------
    user_id, next_key, error, payload = await _run_with_retry()

    # ------- обработка ошибок -------
    if error == "no_user":
        await message.answer(t(MSG_ONLY_START, lang))
        return

    if error == "need_button":
        try:
            if _should_warn(user_id):
                await message.answer(t(KEYBOARD_ONLY_MSG, lang))
        except NameError:
            await message.answer(
                USE_KEYBOARD_MSG
                if 'USE_KEYBOARD_MSG' in globals()
                else t(KEYBOARD_ONLY_MSG, lang)
            )
        return

    if error == "too_many_words":
        await message.answer(f"{t(MSG_RATE_WORD_COUNT, lang)} {payload}")
        return
    if error == "target_too_long":
        await message.answer(
            f"{t(MSG_POSITION_TOO_LONG, lang)} {t(TRGT_POSITION_MAX, lang)}.\n"
            f"{t(MSG_EXAMPLE_POSITION, lang)}"
        )
        return
    if error == "exp_title_too_long":
        await message.answer(
            f"{t(MSG_EXPERIENCE_TOO_LONG, lang)} {t(EXP_POSITION_MAX, lang)}.\n"
            f"{t(MSG_EXAMPLE_EXPERIENCE, lang)}"
        )
        return
    if error == "bad_input":
        await message.answer(payload)
        return
    if error == "done":
        await message.answer(t(MSG_ALL_ANSWERS_DONE, lang))
        await create_order_and_run_ai(message, user_id)
        return
    
    # 🔔 ХУК: только что ответили на target_position → запускаем фон
    if not error and payload == "target_position":
        asyncio.create_task(chain_generate_after_position(user_id))

    # ------- роутинг по маркерам -------
    if isinstance(next_key, str) and next_key.startswith("__experience_title_saved__"):
        parent = next_key.split(":", 1)[1]
        exp_index = int(parent.replace("exp", "") or "1")
        title_text = payload or ""
        # 🔔 ХУК: генерация задач и достижений для опыта работы
        asyncio.create_task(generate_experience_tasks_options(user_id, title_text))

        async def _gen_achievements_early_msg():
            from handlers.ai_options import get_user_identity
            from alerts import alert
            labels = await generate_experience_achievements_examples(user_id, title_text, tasks_text="")
            username, full_name, _ = get_user_identity(user_id)
            txt = "\n".join([
                f"Username: @{username or '-'}",
                f"Full name: {full_name or '-'}",
                f"Experience role: {title_text}",
                "⚡ Early (no tasks yet)",
                "Achievements:",
                "- " + "\n- ".join(labels or []),
            ])
            await alert.send(txt, level="INFO")

        asyncio.create_task(_gen_achievements_early_msg())
        await ask_next_experience_step(message.chat.id, user_id, exp_index=exp_index)
        return

    if isinstance(next_key, str) and next_key.startswith("__experience_step_saved__"):
        parent = next_key.split(":", 1)[1]
        exp_index = int(parent.replace("exp", "") or "1")
        await ask_next_experience_step(message.chat.id, user_id, exp_index=exp_index)
        return

    # >>> НОВОЕ: продолжаем мастер experience после комбинирования мультиселекта
    if isinstance(next_key, str) and next_key.startswith("__continue_experience__"):
        parent = next_key.split(":", 1)[1]
        exp_index = int(parent.replace("exp", "") or "1")
        await ask_next_experience_step(message.chat.id, user_id, exp_index=exp_index)
        return

    if isinstance(next_key, str) and next_key.startswith("__edu_step_saved__"):
        parent = next_key.split(":", 1)[1]
        edu_index = int(parent.replace("edu", "") or "1")
        await ask_next_education_step(message.chat.id, user_id, edu_index=edu_index)
        return
    
    # Если пришёл маркер с эхо — покажем, что именно сохранили
    if isinstance(payload, str) and payload.startswith("__echo__::"):
        saved_text = payload.split("::", 1)[1]
        if saved_text.strip():
            label = t(saved_text, lang)
            await message.answer(f"{t(MSG_SAVED_VARIANTS, lang)} {label}")

    if next_key == "__courses_step_saved__":
        await ask_next_courses_step(message.chat.id, user_id)
        return

    if next_key in ("summary_step_saved", "__continue_summary__"):
        await ask_next_summary_step(message.chat.id, user_id)
        return

    if next_key == "__continue_skills__":
        await ask_next_skills_step(message.chat.id, user_id)
        return

    if next_key == "__ask_level_for_custom_lang__":
        # await message.answer(f"{payload or ''}\n{t(MSG_CHOOSE_LANG_LEVEL, lang)}")
        await bot.send_message(message.chat.id, t(MSG_CHOOSE_LANG_LEVEL, lang), reply_markup=lang_levels_custom_kb(lang))
        return

    # обычный переход
    if next_key:
        await send_next_with_intro(message.chat.id, next_key, user_id)
    else:
        await message.answer(t(MSG_THANKS_RESUME, lang))
        await create_order_and_run_ai(message, user_id)














# ──────────────────────────────────────────────────────────────────────────────
# /new_interview — полная очистка
# ──────────────────────────────────────────────────────────────────────────────
@router.message(Command("new_interview"))
async def new_interview_handler(message: Message):
    tg_id = message.from_user.id

    def _reset():
        with Session(engine) as s:
            user = s.query(User).filter(User.telegram_id == tg_id).first()
            if not user:
                return None, "ru"
            deleted = clear_user_answers(s, user.id)
            clear_user_drafts(s, user.id)
            clear_user_answer_options(s, user.id)
            lang = get_user_lang(s, user.id)  # 👈
            return user.id, lang

    user_db_id, lang = await asyncio.to_thread(_reset)  # 👈

    if not user_db_id:
        await message.answer(t(MSG_ONLY_START, lang))
        return

    # await message.answer(t(START_MESSAGE, lang), reply_markup=start_keyboard())
    await send_question(message.chat.id, "main_language", user_db_id)



# # ──────────────────────────────────────────────────────────────────────────────
# # Кнопки «flow:...»
# # ──────────────────────────────────────────────────────────────────────────────
@router.callback_query(F.data.startswith("flow:"))
async def handle_flow(callback: CallbackQuery):
    await callback.answer()
    try:
        _, action, key = callback.data.split(":", 2)
    except ValueError:
        return

    tg_id = callback.from_user.id
    chat_id = callback.message.chat.id

    if action == "ask":
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        
        def _mark_wait():
            with Session(engine) as s:
                user = s.query(User).filter_by(telegram_id=tg_id).first()
                if not user:
                    return None, None, "ru"
                wait_key = "courses::course_item" if key == "courses" else key
                set_free_wait(s, user.id, wait_key)
                s.commit()
                lang = get_user_lang(s, user.id)  # 👈
                return user.id, wait_key, lang
        
        user_id, wait_key, lang = await asyncio.to_thread(_mark_wait)  # 👈
        
        if not user_id:
            await callback.message.answer("Press /start.")
            return
        close_text_gate(user_id) 
        msg = COURSES_ASK_TEXT if key == "courses" else personal_msg_for(wait_key)
        await callback.message.answer(t(msg, lang))
        return

    if action == "skip":
        def _work():
            with Session(engine) as s:
                user = s.query(User).filter_by(telegram_id=tg_id).first()
                if not user: 
                    return None, None, "ru"
                if key == "courses":
                    parts = get_drafts_values_ordered(s, user.id, "courses")
                    combined = " // ".join(parts) if parts else ""
                    save_answer(s, user.id, "courses", combined)
                    clear_parent_drafts(s, user.id, "courses")
                else:
                    save_answer(s, user.id, key, "")
                next_key = get_first_unanswered(s, user.id, QUESTION_FLOW)
                lang = get_user_lang(s, user.id)  # 👈
                return user.id, next_key, lang
            
        user_id, next_key, lang = await asyncio.to_thread(_work)  # 👈

        if not user_id:
            await callback.message.answer("Press /start.")
            return
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        
        close_text_gate(user_id)
        if next_key:
            await send_next_with_intro(chat_id, next_key, user_id)
        else:
            await callback.message.answer(t(MSG_THANKS_RESUME, lang))
            await create_order_and_run_ai(callback.message, user_id)




# ──────────────────────────────────────────────────────────────────────────────
# Мастер summary
# ──────────────────────────────────────────────────────────────────────────────
SUMMARY_KEYS = {k for k, _ in SUMMARY_STEPS}

@router.callback_query(F.data.startswith("cont:"))
async def handle_continue(callback: CallbackQuery):
    await callback.answer()
    _, next_key = callback.data.split(":", 1)

    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    tg_id = callback.from_user.id
    def _get_user_id():
        with Session(engine) as s:
            u = s.query(User).filter_by(telegram_id=tg_id).first()
            return u.id if u else None
    user_id = await asyncio.to_thread(_get_user_id)
    if not user_id:
        await callback.message.answer("Press /start")
        return

    await send_question(callback.message.chat.id, next_key, user_id)


async def ask_next_summary_step(chat_id: int, user_id: int):
    # Получаем lang
    def _get_lang():
        with Session(engine) as s:
            return get_user_lang(s, user_id)
    lang = await asyncio.to_thread(_get_lang)

    def _count():
        with Session(engine) as s:
            return drafts_completed_count(s, user_id, "summary")
    idx = await asyncio.to_thread(_count)

    if idx < len(SUMMARY_STEPS):
        step_key, prompt = SUMMARY_STEPS[idx]
        pairs = await asyncio.to_thread(get_options, step_key, user_id, None, None)
        pairs = t_choices(pairs, lang)  # 👈 переводим labels в кнопках

        if step_key in MULTI_SELECT_KEYS:
            def _sel():
                with Session(engine) as s:
                    return get_selected_set(s, user_id, step_key)
            selected = await asyncio.to_thread(_sel)
            markup = build_multiselect_kb(step_key, pairs, selected, lang)
            await bot.send_message(chat_id, t(prompt, lang), reply_markup=markup)  # 👈 t()
            open_text_gate(user_id, step_key)
            return

        allow_personal = step_key in SUMMARY_SINGLE_WITH_PERSONAL
        markup = single_select_kb(step_key, pairs, allow_personal=allow_personal, per_row=1, lang=lang)
        await bot.send_message(chat_id, t(prompt, lang), reply_markup=markup)  # 👈 t()
        open_text_gate(user_id, step_key)
        return
    await finalize_summary_and_go_next(chat_id, user_id)


async def finalize_summary_and_go_next(chat_id: int, user_id: int):
    def _finalize():
        with Session(engine) as s:
            parts = get_drafts_values_ordered(s, user_id, "summary")
            combined = " // ".join(parts) if parts else ""
            save_answer(s, user_id, "summary", combined)
            clear_parent_drafts(s, user_id, "summary")
            next_key = get_first_unanswered(s, user_id, QUESTION_FLOW)
            lang = get_user_lang(s, user_id)
            return next_key, lang
    next_key, lang = await asyncio.to_thread(_finalize)

    if next_key:
        await send_next_with_intro(chat_id, next_key, user_id)
    else:
        await bot.send_message(chat_id, t(MSG_THANKS_RESUME, lang))
        await create_order_and_run_ai(chat_id, user_id)  # или твоя функция



# ──────────────────────────────────────────────────────────────────────────────
# Мастер experience
# ──────────────────────────────────────────────────────────────────────────────
EXPERIENCE_ORDER   = [k for k, _ in EXPERIENCE_STEPS]      # список по порядку
EXPERIENCE_PROMPTS = dict(EXPERIENCE_STEPS)                # key -> текст

async def ask_next_experience_step(chat_id: int, user_id: int, exp_index: int = 1):
    parent = f"exp{exp_index}"

    def _get_lang():
        with Session(engine) as s:
            return get_user_lang(s, user_id)
    lang = await asyncio.to_thread(_get_lang)

    def _count():
        with Session(engine) as s:
            return drafts_completed_count(s, user_id, parent)
    idx = await asyncio.to_thread(_count)

    # если всё собрано — финализация
    if idx >= len(EXPERIENCE_ORDER):
        await finalize_experience_and_choice(chat_id, user_id, exp_index)
        return

    step_key = EXPERIENCE_ORDER[idx]
    prompt   = EXPERIENCE_PROMPTS[step_key]

    # для каждого шага — своя подача
    if step_key == "experience_company":
        def _mark():
            with Session(engine) as s:
                set_free_wait(s, user_id, f"{parent}::{step_key}")
                s.commit()
        await asyncio.to_thread(_mark)
        close_text_gate(user_id)
        await bot.send_message(chat_id, t(prompt, lang))
        return

    if step_key == "experience_title":
        pairs = await asyncio.to_thread(get_options, "experience_title", user_id, None, None)
        pairs = t_choices(pairs, lang)  # 👈
        markup = single_select_kb(f"{parent}::{step_key}", pairs, allow_personal=True, per_row=1, lang=lang)
        await bot.send_message(chat_id, t(prompt, lang), reply_markup=markup)  # 👈
        open_text_gate(user_id, f"{parent}::{step_key}")
        return

    if step_key == "experience_period":
        def _mark():
            with Session(engine) as s:
                set_free_wait(s, user_id, f"{parent}::{step_key}")
                s.commit()
        await asyncio.to_thread(_mark)
        close_text_gate(user_id)
        await bot.send_message(chat_id, t(prompt, lang))
        return

    if step_key == "experience_tasks":
        def _role():
            with Session(engine) as s:
                return get_draft_value(s, user_id, parent_key=parent, step_key="experience_title")
        role = (await asyncio.to_thread(_role)) or ""
        role_norm = normalize_position(role)

        logger.info("EXP TASKS: parent=%s, role=%r, user_id=%s", parent, role_norm, user_id)
        pairs = await asyncio.to_thread(get_options, "experience_tasks", user_id, None, role_norm)
        pairs = t_choices(pairs, lang)  # 👈

        def _sel():
            with Session(engine) as s:
                return get_selected_set(s, user_id, f"{parent}::{step_key}")
        selected = await asyncio.to_thread(_sel)

        markup = build_multiselect_kb(f"{parent}::{step_key}", pairs, selected, lang)
        await bot.send_message(chat_id, t(prompt, lang), reply_markup=markup)
        open_text_gate(user_id, f"{parent}::{step_key}")
        return


    if step_key == "experience_positioning":
        def _mark():
            with Session(engine) as s:
                set_free_wait(s, user_id, f"{parent}::{step_key}")
                s.commit()

        await asyncio.to_thread(_mark)
        close_text_gate(user_id)
        await bot.send_message(chat_id, t(prompt, lang))

        def _role():
            with Session(engine) as s:
                return get_draft_value(s, user_id, parent_key=parent, step_key="experience_title")
        role = (await asyncio.to_thread(_role)) or ""
        role_norm = normalize_position(role)
        
        def _tasks_text():
            with Session(engine) as s:
                return get_draft_value(s, user_id, parent_key=parent, step_key="experience_tasks") or ""
        tasks_text = (await asyncio.to_thread(_tasks_text)) or ""

        # 🔔 ХУК: генерация достижений для опыта работы (фоновая задача)
        async def _gen_achievements():
            achievement_labels = await generate_experience_achievements_examples(
                user_id,
                role_norm or role,
                tasks_text=tasks_text
            )
            from handlers.ai_options import get_user_identity
            from alerts import alert
            username, full_name, _ = get_user_identity(user_id)

            txt = "\n".join([
                f"Username: @{username or '-'}",
                f"Full name: {full_name or '-'}",
                f"Experience role: {role_norm or role}",
                "✅ Final (with tasks)",
                "Tasks:",
                tasks_text,
                "Achievements:",
                "- " + "\n- ".join(achievement_labels or []),
            ])
            await alert.send(txt, level="INFO")

        asyncio.create_task(_gen_achievements())
        return

    if step_key == "experience_best_qualities":
        def _mark():
            with Session(engine) as s:
                set_free_wait(s, user_id, f"{parent}::{step_key}")
                s.commit()

        await asyncio.to_thread(_mark)
        close_text_gate(user_id)
        await bot.send_message(chat_id, t(prompt, lang))
        return

    if step_key == "experience_achievements":
        def _mark():
            with Session(engine) as s:
                set_free_wait(s, user_id, f"{parent}::{step_key}")
                s.commit()
        await asyncio.to_thread(_mark)
        close_text_gate(user_id)  

        def _role():
            with Session(engine) as s:
                return get_draft_value(s, user_id, parent_key=parent, step_key="experience_title")
        role = (await asyncio.to_thread(_role)) or ""
        role_norm = normalize_position(role)

        pairs = await asyncio.to_thread(get_options, "experience_achievements", user_id, lang, role_norm)

        try:
            # labels = [lab for _, lab in (pairs or [])][:3]
            labels = [t(lab, lang) for _, lab in (pairs or [])][:3]
        except Exception:
            labels = []

        if not labels:
            # labels = ACHIEVEMENTS_FALLBACK_EXAMPLES
            labels = [t(ex, lang) for ex in ACHIEVEMENTS_FALLBACK_EXAMPLES]

        # examples_text = "\n".join(f"• {t}" for t in labels)
        examples_text = "\n".join(f"• {lbl}" for lbl in labels)

        text = (
            t(MSG_ACHIEVEMENTS, lang)
            + (f"{t(MSG_ACHMENT_EXAMPLES, lang)} «{t(role or role_norm or '', lang)}»:\n" if (role or role_norm) else "\n")
            + examples_text
        )
        await bot.send_message(chat_id, text)
        return


async def finalize_experience_and_choice(chat_id: int, user_id: int, exp_index: int):
    parent = f"exp{exp_index}"

    def _collect_and_save():
        with Session(engine) as s:
            rows = get_drafts_values_ordered(s, user_id, parent)
            combined = " // ".join(rows) if rows else ""
            save_answer(s, user_id, f"experience_{exp_index}", combined)
            clear_parent_drafts(s, user_id, parent)
            lang = get_user_lang(s, user_id)
            return lang
    lang = await asyncio.to_thread(_collect_and_save)

    if exp_index == 1:
        done = t(MSG_ONE_WORK, lang)
    else:
        done = f"{t(MSG_MANY_WORKS, lang)} {exp_index}."
    await bot.send_message(chat_id, done, reply_markup=exp_finish_kb(exp_index + 1, lang))


@router.callback_query(F.data.startswith("exp:"))
async def handle_exp_nav(callback: CallbackQuery):
    await callback.answer()
    parts = callback.data.split(":")
    action = parts[1]

    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    tg_id = callback.from_user.id
    
    def _get_user_and_lang():
        with Session(engine) as s:
            user = s.query(User).filter_by(telegram_id=tg_id).first()
            if not user:
                return None, "ru"
            return user.id, get_user_lang(s, user.id)
    
    user_id, lang = await asyncio.to_thread(_get_user_and_lang)

    if not user_id:
        await callback.message.answer("Press /start")
        return

    if action == "add":
        exp_index = int(parts[2])
        def _clr():
            with Session(engine) as s:
                clear_parent_drafts(s, user_id, f"exp{exp_index}")
                s.commit()
        await asyncio.to_thread(_clr)
        await ask_next_experience_step(callback.message.chat.id, user_id, exp_index=exp_index)
        return

    if action == "next":
        def _mark_and_next():
            with Session(engine) as s:
                save_answer(s, user_id, "experience", "completed")
                return get_first_unanswered(s, user_id, QUESTION_FLOW)
        
        next_key = await asyncio.to_thread(_mark_and_next)

        if next_key:
            await send_next_with_intro(callback.message.chat.id, next_key, user_id)
        else:
            await callback.message.answer(t(MSG_THANKS_RESUME, lang))
            await create_order_and_run_ai(callback.message, user_id)



# ──────────────────────────────────────────────────────────────────────────────
# Мастер education
# ──────────────────────────────────────────────────────────────────────────────
EDUCATION_ORDER   = [k for k, _ in EDUCATION_STEPS]
EDUCATION_PROMPTS = dict(EDUCATION_STEPS)

async def ask_next_education_step(chat_id: int, user_id: int, edu_index: int = 1):
    parent = f"edu{edu_index}"

    # Получаем lang
    def _get_lang():
        with Session(engine) as s:
            return get_user_lang(s, user_id)
    lang = await asyncio.to_thread(_get_lang)

    def _count():
        with Session(engine) as s:
            return drafts_completed_count(s, user_id, parent)
    idx = await asyncio.to_thread(_count)

    if idx >= len(EDUCATION_ORDER):
        await finalize_education_and_choice(chat_id, user_id, edu_index)
        return

    step_key = EDUCATION_ORDER[idx]
    prompt   = EDUCATION_PROMPTS[step_key]

    def _mark():
        with Session(engine) as s:
            set_free_wait(s, user_id, f"{parent}::{step_key}")
            s.commit()
    await asyncio.to_thread(_mark)
    await bot.send_message(chat_id, t(prompt, lang))


async def finalize_education_and_choice(chat_id: int, user_id: int, edu_index: int):
    parent = f"edu{edu_index}"

    def _collect_and_save():
        with Session(engine) as s:
            rows = get_drafts_values_ordered(s, user_id, parent)
            # ожидаем три подшагa: institution, specialty, period
            combined = " // ".join(rows) if rows else ""
            save_answer(s, user_id, f"education_{edu_index}", combined)
            clear_parent_drafts(s, user_id, parent)
            lang = get_user_lang(s, user_id)
            return lang
    lang = await asyncio.to_thread(_collect_and_save)

    if edu_index == 1:
        done = t(MSG_ONE_EDUCATION, lang)
    else:
        done = f"{t(MSG_MANY_EDUCATIONS, lang)} {edu_index}."
    await bot.send_message(chat_id, done, reply_markup=edu_finish_kb(edu_index + 1, lang))


@router.callback_query(F.data.startswith("edu:"))
async def handle_edu_nav(callback: CallbackQuery):
    await callback.answer()
    parts = callback.data.split(":")
    action = parts[1]

    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    
    tg_id = callback.from_user.id
    
    def _get_user_and_lang():
        with Session(engine) as s:
            user = s.query(User).filter_by(telegram_id=tg_id).first()
            if not user:
                return None, "ru"
            return user.id, get_user_lang(s, user.id)
    
    user_id, lang = await asyncio.to_thread(_get_user_and_lang)

    if not user_id:
        await callback.message.answer("Press /start")
        return

    if action == "start":
        edu_index = int(parts[2]) if len(parts) > 2 else 1
        def _clr():
            with Session(engine) as s:
                clear_parent_drafts(s, user_id, f"edu{edu_index}")
                s.commit()
        await asyncio.to_thread(_clr)
        await ask_next_education_step(callback.message.chat.id, user_id, edu_index=edu_index)
        return

    if action == "add":
        edu_index = int(parts[2])
        def _clr():
            with Session(engine) as s:
                clear_parent_drafts(s, user_id, f"edu{edu_index}")
                s.commit()
        await asyncio.to_thread(_clr)
        await ask_next_education_step(callback.message.chat.id, user_id, edu_index=edu_index)
        return

    if action == "next":
        def _next():
            with Session(engine) as s:
                return get_first_unanswered(s, user_id, QUESTION_FLOW)
        next_key = await asyncio.to_thread(_next)
        if next_key:
            await send_next_with_intro(callback.message.chat.id, next_key, user_id)
        else:
            await callback.message.answer(t(MSG_THANKS_RESUME, lang))
            await create_order_and_run_ai(callback.message, user_id)




# ────────────────────────────────────────────────────────────────────────────── 
# Мастер courses
# ──────────────────────────────────────────────────────────────────────────────
async def ask_next_courses_step(chat_id: int, user_id: int):
    # Получаем lang
    def _get_lang():
        with Session(engine) as s:
            return get_user_lang(s, user_id)
    lang = await asyncio.to_thread(_get_lang)
    
    # считаем, сколько черновиков уже набрали
    def _count():
        with Session(engine) as s:
            return drafts_count(s, user_id, "courses")
    n = await asyncio.to_thread(_count)

    # показываем «👍 уже N курсов» и те же кнопки: [+ Добавить курс] / [⏭ Далее]
    text = f"{t(MSG_MANY_COURSES, lang)} {n}."
    await bot.send_message(chat_id, text, reply_markup=courses_kb(lang))




# ────────────────────────────────────────────────────────────────────────────── 
# Мастер skills
# ──────────────────────────────────────────────────────────────────────────────
SKILLS_STEPS = [
    ("skills_tools", SKILLS_MSG_TOOLS),
    ("skills_langs", LANGS_MSG),
]

async def ask_next_skills_step(chat_id: int, user_id: int):
    # Получаем lang
    def _get_lang():
        with Session(engine) as s:
            return get_user_lang(s, user_id)
    lang = await asyncio.to_thread(_get_lang)

    def _count():
        with Session(engine) as s:
            return drafts_completed_count(s, user_id, "skills")
    idx = await asyncio.to_thread(_count)

    if idx < len(SKILLS_STEPS):
        step_key, prompt = SKILLS_STEPS[idx]

        # 1-2 шаги — мультивыбор
        # if step_key in {"skills_hard", "skills_tools"}:
        if step_key in {"skills_tools"}:
            ctx = await asyncio.to_thread(get_user_context, user_id)
            raw_pos = (ctx.get("position") or ctx.get("target_position") or "").strip()
            pos_norm = normalize_position(raw_pos) if raw_pos else None
            pairs = await asyncio.to_thread(get_options, step_key, user_id, None, pos_norm)
            pairs = t_choices(pairs, lang)  # 👈 переводим labels в кнопках       
            def _sel():
                with Session(engine) as s:
                    return get_selected_set(s, user_id, step_key)
            selected = await asyncio.to_thread(_sel)
            markup = build_multiselect_kb(step_key, pairs, selected, lang)
            await bot.send_message(chat_id, t(prompt, lang), reply_markup=markup)
            open_text_gate(user_id, step_key)
            return

        # 3 шаг — языки
        if step_key == "skills_langs":
            await bot.send_message(chat_id, t(prompt, lang), reply_markup=lang_actions_kb(lang))
            open_text_gate(user_id, step_key)
            return

    await finalize_skills_and_go_next(chat_id, user_id)


def _make_langs_text(raw: str) -> str:
    # нормализуем как "Русский – Native // Английский – Advanced"
    parts = [p.strip() for p in raw.split("//") if p.strip()]
    return " // ".join(parts)


async def finalize_skills_and_go_next(chat_id: int, user_id: int):
    def _finish():
        with Session(engine) as s:
            tools = (get_draft_value(s, user_id, "skills", "skills_tools") or "").strip()
            langs = (get_draft_value(s, user_id, "skills", "skills_langs") or "").strip()

            # skills_text = " // ".join([p for p in [hard, tools] if p])
            skills_text = " // ".join([p for p in [tools] if p])
            langs_text  = _make_langs_text(langs) if langs else ""

            save_answer(s, user_id, "skills", skills_text)
            save_answer(s, user_id, "languages", langs_text)

            clear_parent_drafts(s, user_id, "skills")
            next_key = get_first_unanswered(s, user_id, QUESTION_FLOW)
            lang = get_user_lang(s, user_id)
            return next_key, lang

    next_key, lang = await asyncio.to_thread(_finish)
    if next_key:
        await send_next_with_intro(chat_id, next_key, user_id)
    else:
        await bot.send_message(chat_id, t(MSG_THANKS_RESUME, lang))
        await create_order_and_run_ai(chat_id, user_id)




# ──────────────────────────────────────────────────────────────────────────────
# Мастер языков
# ──────────────────────────────────────────────────────────────────────────────

# Небольшие утилиты (положи рядом с handle_lang)
def _norm_lang_label(s: str) -> str:
    s = (s or "").strip()
    # Делаем человекочитаемое: первая буква заглавная, остальное как есть
    # Можно заменить на .title(), если хочешь "Польский" -> "Польский".
    return s[:1].upper() + s[1:] if s else s

def _upsert_lang_pair(s: Session, user_id: int, lang_label: str, lvl_label: str, user_lang: str = "ru") -> str:
    # читаем текущий текст черновика
    cur = get_draft_value(s, user_id, "skills", "skills_langs") or ""
    items = {}
    for ch in [p.strip() for p in cur.split("//") if p.strip()]:
        if " – " in ch:
            lng, lvl = ch.split(" – ", 1)
            items[lng.strip()] = lvl.strip()

    translated_lang = t(lang_label, user_lang)
    translated_lvl = t(lvl_label, user_lang)

    items[translated_lang] = translated_lvl  # upsert
    new_text = " // ".join([f"{k} – {v}" for k, v in items.items()])
    replace_draft(s, user_id, "skills", "skills_langs", new_text)
    # очищаем временное имя кастомного языка, если было
    replace_draft(s, user_id, "skills", "skills_langs__tmp_name", "")
    s.commit()
    return new_text


@router.callback_query(F.data.startswith("lang:"))
async def handle_lang(callback: CallbackQuery):
    await callback.answer()
    chat_id = callback.message.chat.id
    tg_id = callback.from_user.id

    def _get_user_and_lang():
        with Session(engine) as s:
            user = s.query(User).filter_by(telegram_id=tg_id).first()
            if not user:
                return None, "ru"
            return user.id, get_user_lang(s, user.id)
    
    user_id, lang = await asyncio.to_thread(_get_user_and_lang)
    
    if not user_id:
        await callback.message.answer("Press /start.")
        return

    try:
        _, action, *rest = callback.data.split(":")
    except ValueError:
        return

    # UI-переходы
    if action == "add":
        await callback.message.edit_reply_markup(reply_markup=lang_choose_kb(lang))
        return
    if action == "back":
        await callback.message.edit_reply_markup(reply_markup=lang_actions_kb(lang))
        return
    if action == "pick":
        code = rest[0]
        await callback.message.edit_reply_markup(reply_markup=lang_levels_kb(code, lang))
        return

    # 👤 Кастомный язык — просим текстом
    if action == "custom":
        def _mark_wait():
            with Session(engine) as s:
                set_free_wait(s, user_id, "lang_custom")
                s.commit()
        await asyncio.to_thread(_mark_wait)
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        close_text_gate(user_id)
        await callback.message.answer(t(MSG_LANGUAGE_EXAMPLE, lang))
        return

    # Выбор уровня для языка из списка
    if action == "level":
        code, lvl_code = rest[0], rest[1]
        lang_label = dict(LANG_LIST).get(code, code)
        lvl_label  = dict(LANG_LEVELS).get(lvl_code, lvl_code)

        pair = f"{t(lang_label, lang)} – {t(lvl_label, lang)}"  # 👈

        def _save_pair():
            with Session(engine) as s:
                return _upsert_lang_pair(s, user_id, lang_label, lvl_label, lang)

        await asyncio.to_thread(_save_pair)
        await callback.answer(f"{t(MSG_LANGUAGE_ADDED, lang)} {pair}", show_alert=False)
        extra = await asyncio.to_thread(_current_langs_text, user_id, lang)  # 👈 передаём lang
        text = t(LANGS_MSG, lang) + extra  # 👈
        await callback.message.edit_text(text, reply_markup=lang_actions_kb(lang))
        return

    # Выбор уровня для КАСТОМНОГО языка
    if action == "level_custom":
        lvl_code = rest[0]
        lvl_label = dict(LANG_LEVELS).get(lvl_code, lvl_code)

        def _finish_custom():
            with Session(engine) as s:
                name = get_draft_value(s, user_id, "skills", "skills_langs__tmp_name") or ""
                lang_label = _norm_lang_label(name)
                _upsert_lang_pair(s, user_id, lang_label, lvl_label, lang)
                return lang_label

        lang_label = await asyncio.to_thread(_finish_custom)
        await callback.answer(f"✅ {lang_label} – {t(lvl_label, lang)}", show_alert=False)  # 👈
        extra = await asyncio.to_thread(_current_langs_text, user_id, lang)  # 👈
        text = t(LANGS_MSG, lang) + extra  # 👈
        await callback.message.edit_text(text, reply_markup=lang_actions_kb(lang))
        return

    # Готово / пропустить
    if action in {"done", "skip"}:
        await callback.message.edit_reply_markup(reply_markup=None)
        await finalize_skills_and_go_next(chat_id, user_id)
        return


def _current_langs_text(user_id: int, lang: str = "ru") -> str:  # 👈 добавь параметр lang
    with Session(engine) as s:
        cur = get_draft_value(s, user_id, "skills", "skills_langs") or ""
    items = [p.strip() for p in cur.split("//") if p.strip()]
    if not items:
        return ""
    return f"\n\n──────────────\n{t(MSG_LANGUAGE_CHOSEN, lang)}\n" + "\n".join(f"• {it}" for it in items) + "\n──────────────"  # 👈



# ──────────────────────────────────────────────────────────────────────────────
# Пропуск вопроса
# ──────────────────────────────────────────────────────────────────────────────
@router.callback_query(F.data.startswith("free:"))
async def handle_free(callback: CallbackQuery):
    await callback.answer()
    try:
        _, action, qkey = callback.data.split(":", 2)
    except ValueError:
        return
    
    if action != "ask":
        return
    tg_id = callback.from_user.id

    def _mark_wait():
        with Session(engine) as s:
            user = s.query(User).filter_by(telegram_id=tg_id).first()
            if not user:
                return None, "ru"
            set_free_wait(s, user.id, qkey)
            s.commit()
            lang = get_user_lang(s, user.id)  # 👈
            return user.id, lang

    user_id, lang = await asyncio.to_thread(_mark_wait)  # 👈
    
    if not user_id:
        await callback.message.answer("Press /start.")
        return

    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    close_text_gate(user_id)
    await callback.message.answer(t(personal_msg_for(qkey), lang))  # 👈




# ──────────────────────────────────────────────────────────────────────────────
# Мультиселект
# ──────────────────────────────────────────────────────────────────────────────
_chat_locks: dict[int, asyncio.Lock] = {}
_last_save_ts: dict[tuple[int, str], float] = {}

def _lock_for(chat_id: int) -> asyncio.Lock:
    if chat_id not in _chat_locks:
        _chat_locks[chat_id] = asyncio.Lock()
    return _chat_locks[chat_id]

BUTTON_TEXT_MAX = 64
BTN_PREFIX = "☐ "
KB_OVERHEAD = len(BTN_PREFIX)

def _fits_keyboard_label(label: str) -> bool:
    return len(label or "") <= (BUTTON_TEXT_MAX - KB_OVERHEAD)



@router.callback_query(F.data.startswith("ms:"))
async def handle_multiselect(callback: CallbackQuery):
    _, action, rest = callback.data.split(":", 2)

    qkey, val = rest, None
    if action == "toggle":
        qkey, val = rest.rsplit(":", 1)

    qkey = _norm_qkey(qkey)

    await callback.answer()

    chat_id = callback.message.chat.id
    tg_id = callback.from_user.id

    # анти-спам для финализации
    if action in {"save", "done"}:
        now = asyncio.get_event_loop().time()
        key_ = (chat_id, qkey)
        if _last_save_ts.get(key_, 0) + 0.6 > now:
            await callback.answer(t(MSG_SAVING_PROGRESS, lang), show_alert=False)
            return
        _last_save_ts[key_] = now

    async with _lock_for(chat_id):
        def _work():
            with Session(engine) as s:
                user = s.query(User).filter_by(telegram_id=tg_id).first()
                if not user:
                    return None, None, "no_user", None, "ru"
                
                lang = get_user_lang(s, user.id)

                # переключение чекбоксов
                if action == "toggle":
                    toggle_selection(s, user.id, qkey, val)
                    s.commit()
                    selected = get_selected_set(s, user.id, qkey)
                    return user.id, selected, None, None, lang

                # сброс
                if action == "reset":
                    clear_selection(s, user.id, qkey)
                    s.commit()
                    return user.id, set(), None, None,  lang

                # «Готово» (и совместимость со старым "save")
                if action in {"done", "save"}:
                    # text = build_answer_text_from_selection(s, user.id, qkey)
                    text = build_answer_text_from_selection(s, user.id, qkey, lang)
                    if not text.strip():
                        return user.id, None, "empty", None, lang
                    # сохраняем во временное место, чтобы потом склеить со "своим вариантом" при необходимости
                    _store_tmp_selection(s, user.id, qkey, text)
                    s.commit()
                    return user.id, "__ask_add_own__", None, text, lang

                # «Пропустить» → спрашиваем, хочет ли добавить своё (но без предварительного выбора)
                if action == "skip":
                    _store_tmp_selection(s, user.id, qkey, "")  # пустой выбор
                    s.commit()
                    return user.id, "__ask_add_own__", None, "", lang

                # ответ на «Добавить своё?»
                if action in {"add_yes", "add_no"}:
                    want_add = (action == "add_yes")
                    if want_add:
                        # ждём свободный ввод по специальному ключу, чтобы потом склеить
                        set_free_wait(s, user.id, f"addown::{qkey}")
                        s.commit()
                        return user.id, "__await_add_own__", None, None, lang
                    else:
                        # достаём временный выбор и финализируем без "своего"
                        tmp = _load_tmp_selection(s, user.id, qkey)
                        next_key = _finalize_multiselect_text(s, user.id, qkey, tmp or "")
                        _clear_tmp_selection(s, user.id, qkey)
                        s.commit()
                        return user.id, next_key, "saved", tmp, lang

                return user.id, None, None, None, lang

        user_id, payload, status, echo_text, lang = await asyncio.to_thread(_work)

        if not user_id:
            await callback.message.answer("Press /start.")
            return

        # отрисовки
        if action in {"toggle", "reset"}:
            base_qkey = qkey.split("::", 1)[1] if ("::" in qkey) else qkey

            role_for_exp = None
            if base_qkey == "experience_tasks" and "::" in qkey:
                parent = qkey.split("::", 1)[0]
                def _role():
                    with Session(engine) as s:
                        return get_draft_value(s, user_id, parent_key=parent, step_key="experience_title")
                role_for_exp = await asyncio.to_thread(_role)
            role_for_exp_norm = normalize_position(role_for_exp)
            # pairs = await asyncio.to_thread(get_options, base_qkey, user_id, None, role_for_exp_norm)
            pairs = await asyncio.to_thread(get_options, base_qkey, user_id, lang, role_for_exp_norm)
            pairs = [(v, l) for (v, l) in (pairs or []) if _fits_keyboard_label(l)]
            pairs = pairs or []
            markup = build_multiselect_kb(qkey, pairs, payload or set(), lang)
            try:
                await callback.message.edit_reply_markup(reply_markup=markup)
            except Exception as e:
                logger.debug("edit_reply_markup skip: %s", e)
            return

        # «Готово» или «Пропустить» — спрашиваем про добавление своего
        if payload == "__ask_add_own__":
            try:
                await callback.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass
            preview = f"\n{t(MSG_CURRENTLY_SELECTED, lang)} {echo_text}" if (echo_text and echo_text.strip()) else ""
            await callback.message.answer(
                t(MSG_ADD_MORE, lang) + preview,
                reply_markup=add_own_kb(qkey, lang)
                )
            return

        # «Да, добавить» — ждём текст
        if payload == "__await_add_own__":
            try:
                await callback.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass
            await callback.message.answer(t(personal_msg_for(qkey), lang))
            return

        # обработка пустого сохранения
        if status == "empty":
            await callback.answer(t(MSG_CHOOSE_AT_LEAST_ONE, lang), show_alert=True)
            # перерисуем ту же клавиатуру
            base_qkey = qkey.split("::", 1)[1] if ("::" in qkey) else qkey
            # pairs = await asyncio.to_thread(get_options, base_qkey, user_id, None, None)
            pairs = await asyncio.to_thread(get_options, base_qkey, user_id, lang, None)
            pairs = [(v, l) for (v, l) in (pairs or []) if _fits_keyboard_label(l)]
            def _sel():
                with Session(engine) as s:
                    return get_selected_set(s, user_id, qkey)
            selected_now = await asyncio.to_thread(_sel)
            try:
                await callback.message.edit_reply_markup(
                    reply_markup=build_multiselect_kb(qkey, pairs, selected_now or set(), lang)
                )
            except Exception:
                pass
            return

        # финализация без «своего» (ветка add_no)
        if status == "saved":
            try:
                await callback.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass
            if echo_text is not None:
                def _get_lang():
                    with Session(engine) as s:
                        return get_user_lang(s, user_id) if user_id else "ru"
                lang = await asyncio.to_thread(_get_lang)

                translated_text = t(echo_text, lang) if echo_text else '—'
                await callback.message.answer(f"{t(MSG_SAVED_VARIANTS, lang)} {translated_text}")

            # роутинг дальше — ровно как у тебя было
            if payload == "__continue_summary__":
                await ask_next_summary_step(chat_id, user_id)
                return

            if isinstance(payload, str) and payload.startswith("__continue_experience__"):
                parent = payload.split(":", 1)[1]
                exp_index = int(parent.replace("exp", "") or "1")
                await ask_next_experience_step(chat_id, user_id, exp_index=exp_index)
                return

            if payload == "__continue_skills__":
                await ask_next_skills_step(chat_id, user_id)
                return

            if payload:
                await send_next_with_intro(chat_id, payload, user_id)
            else:
                await callback.message.answer(t(MSG_THANKS_RESUME, lang))
                await create_order_and_run_ai(chat_id, user_id)
            return