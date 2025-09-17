import re
import asyncio
from aiogram import Router, F
from aiogram.filters import Command,CommandStart
from aiogram.types import Message, CallbackQuery
from sqlalchemy.orm import Session

from handlers.db_helpers import (
    get_or_create_user, 
    get_first_unanswered, 
    save_answer, 
    clear_user_answers, 
    clear_user_drafts, 
    clear_parent_drafts, 
    drafts_count,
    get_drafts_values_ordered,
    drafts_completed_count,
    add_draft,
    replace_draft,
    pop_personal_wait,
    set_personal_wait,
    pop_free_wait,
    set_free_wait)

from handlers.order import create_order_and_run_ai
from handlers.bot_app import bot
from handlers.options_repo import get_options, get_label_for, normalize_position
from handlers.ai_tasks import (
    chain_generate_after_position,
    generate_experience_tasks_options,
    generate_experience_achievements_examples,
    generate_experience_bundle)

from _database import engine, User

from _key import (
    text_or_skip_keyboard, 
    start_keyboard, 
    single_select_kb, 
    build_multiselect_kb,
    continue_kb,
    exp_finish_kb,
    edu_start_kb, 
    edu_finish_kb,
    courses_kb)

from _utils import (
    logger,
    CHOICES, 
    INTRO_BEFORE, 
    QUESTION_FLOW, 
    START_MESSAGE,
    SUMMARY_STEPS,
    PERSONAL_MESSAGE,
    EXPERIENCE_STEPS,
    ACHIEVEMENTS_FALLBACK_EXAMPLES,
    EDUCATION_STEPS, 
    EDUCATION_INTRO,
    COURSES_INTRO,
    COURSES_ASK_TEXT)

from handlers.ms_repo import (
    get_selected_set, 
    toggle_selection, 
    clear_selection, 
    build_answer_text_from_selection)


router = Router()

# --- constants --- 
SUMMARY_SINGLE_WITH_PERSONAL = {"summary_industry"}
MULTI_SELECT_KEYS = {"summary_tasks", "summary_values", "experience_tasks"}
PAUSE_BEFORE = {"summary", "experience"}


# ──────────────────────────────────────────────────────────────────────────────
# Навигация по вопросам
# ──────────────────────────────────────────────────────────────────────────────
async def send_next_with_intro(chat_id: int, next_key: str, user_id: int | None = None):
    intro = INTRO_BEFORE.get(next_key)
    if intro and next_key in PAUSE_BEFORE:
        await bot.send_message(chat_id, intro, reply_markup=continue_kb(next_key))
        return
    if intro:
        await bot.send_message(chat_id, intro)
    await send_question(chat_id, next_key, user_id)


async def send_first_question(chat_id: int, user_db_id: int):
    first_key = QUESTION_FLOW[0][0]
    await send_question(chat_id, first_key, user_db_id)


async def send_question(chat_id: int, qkey: str, user_id: int | None = None):
    PROMPTS = dict(QUESTION_FLOW)

    # ▶ Если это summary — запускаем ask_next_summary_step мини-мастер
    if qkey == "summary" and user_id:
        await bot.send_message(chat_id, PROMPTS[qkey])
        def _reset():
            with Session(engine) as s:
                clear_parent_drafts(s, user_id, "summary")
                s.commit()
        await asyncio.to_thread(_reset)
        await ask_next_summary_step(chat_id, user_id)
        return
    
    # ▶ Если это experience — запускаем ask_next_experience_step мини-мастер 
    if qkey == "experience" and user_id:
        # 1) чистим старые черновики exp1 (на всякий случай)
        def _reset():
            with Session(engine) as s:
                clear_parent_drafts(s, user_id, "exp1")
                s.commit()
        await asyncio.to_thread(_reset)

        # 2) помечаем шаг "experience" как пройденный, чтобы get_first_unanswered не зацикливался
        def _mark_done():
            with Session(engine) as s:
                save_answer(s, user_id, "experience", "")
        await asyncio.to_thread(_mark_done)

        # 3) стартуем первый подпроцесс опыта
        await ask_next_experience_step(chat_id, user_id, exp_index=1)
        return
    
    # ▶ Если это education — запускаем мини-мастер образования
    if qkey == "education" and user_id:
        await bot.send_message(chat_id, EDUCATION_INTRO, reply_markup=edu_start_kb())
        
        def _mark_done():
            with Session(engine) as s:
                save_answer(s, user_id, "education", "")
        await asyncio.to_thread(_mark_done)
        return
    
    # ▶ Если это courses — просто текст с кнопками
    if qkey == "courses" and user_id:
        await bot.send_message(chat_id, COURSES_INTRO, reply_markup=courses_kb())
        return
    
    pairs = await asyncio.to_thread(get_options, qkey, user_id, None, None)
    
    markup = None
    if qkey in MULTI_SELECT_KEYS and user_id and pairs:
        # вытягиваем выбранные value из БД
        def _sel():
            with Session(engine) as s:
                return get_selected_set(s, user_id, qkey)
        selected = await asyncio.to_thread(_sel)
        markup = build_multiselect_kb(qkey, pairs, selected)
    elif pairs:
        allow_personal = qkey in SUMMARY_SINGLE_WITH_PERSONAL
        markup = single_select_kb(qkey, pairs, allow_personal=allow_personal, per_row=1)
    elif qkey == "job_description":
        markup = text_or_skip_keyboard(qkey)

    await bot.send_message(chat_id, PROMPTS[qkey], reply_markup=markup)


# ──────────────────────────────────────────────────────────────────────────────
# Обработчик одиночного выбора кнопки / сохранение ответа
# ──────────────────────────────────────────────────────────────────────────────
@router.callback_query(F.data.startswith("ans:"))
async def handle_choice(callback: CallbackQuery):
    await callback.answer()

    _, rest = callback.data.split(":", 1)
    key, value = rest.rsplit(":", 1)

    tg_id = callback.from_user.id

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
                label = label_db or dict(CHOICES.get(base_key, [])).get(value, value)

                add_draft(s, user.id, parent, step, label)
                s.commit()

                saved_marker = "__experience_title_saved__" if step == "experience_title" else "__experience_step_saved__"
                return user.id, f"{saved_marker}:{parent}", None, label

            # ▶ Подвопросы summary
            if key.startswith("summary_"):
                label = get_label_for(s, key, value, user.id)
                add_draft(s, user.id, "summary", key, label)
                s.commit()
                return user.id, "summary_step_saved", None, label

            # ♦ Обычные вопросы
            label = dict(CHOICES.get(key, [])).get(value, value)
            save_answer(s, user.id, key, label)
            next_key = get_first_unanswered(s, user.id, QUESTION_FLOW)
            return user.id, next_key, None, label

    user_id, next_key, error, echo_label = await asyncio.to_thread(_work)

    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception as e:
        logger.debug("edit_reply_markup skip: %s", e)

    if error == "no_user":
        await callback.message.answer("Напишите /start сначала, пожалуйста.")
        return

    if echo_label:
        await callback.message.answer(f"💾 Записал вариант(ы): {echo_label}")

    # если только что сохранили должность — запускаем генерацию задач в event loop
    if isinstance(next_key, str) and next_key.startswith("__experience_title_saved__"):
        parent = next_key.split(":", 1)[1]          # exp1 / exp2 / ...
        exp_index = int(parent.replace("exp", "") or "1")
        # 🔔 ХУК: генерация задач для опыта работы
        # asyncio.create_task(generate_experience_tasks_options(user_id, echo_label))
        asyncio.create_task(generate_experience_bundle(user_id, echo_label))
        asyncio.create_task(generate_experience_achievements_examples(user_id, echo_label))
        await ask_next_experience_step(callback.message.chat.id, user_id, exp_index=exp_index)
        return

    if next_key == "summary_step_saved":
        await ask_next_summary_step(callback.message.chat.id, user_id)
        return

    if next_key:
        await send_next_with_intro(callback.message.chat.id, next_key, user_id)
    else:
        await callback.message.answer("Спасибо! Всё заполнено.")
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
    await message.answer(START_MESSAGE,reply_markup=start_keyboard())


@router.callback_query(F.data == "starts")
async def handle_start(callback: CallbackQuery):
    await callback.answer()
    # скрыть клавиатуру под приветствием (чтобы не жали повторно)
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
            return user.id, next_key

    user_id, next_key = await asyncio.to_thread(_work)

    if next_key:
        await send_next_with_intro(callback.message.chat.id, next_key, user_id)
    else:
        await callback.message.answer("🎉 Все вопросы уже собраны.\n\nИдёт генерация…")
        await create_order_and_run_ai(callback.message, user_id)



# ──────────────────────────────────────────────────────────────────────────────
# Ответы текстом
# ──────────────────────────────────────────────────────────────────────────────
@router.message(F.text & ~F.text.startswith("/"))
async def handle_answer(message: Message):
    tg_id = message.from_user.id
    text = message.text.strip()

    def _work():
        with Session(engine) as s:
            user = s.query(User).filter_by(telegram_id=tg_id).first()
            if not user:
                return None, None, "no_user", None

            # 0) Свободный текст для ЛЮБОГО ключа (универсальный флаг __free__)
            free_qkey = pop_free_wait(s, user.id)
            if free_qkey:
                if free_qkey.startswith("exp") and "::" in free_qkey:
                    parent, step = free_qkey.split("::", 1)
                    parts = [p.strip() for p in re.split(r"[,\n;]+", text) if p.strip()]
                    human = ", ".join(parts) if parts else text
                    replace_draft(s, user.id, parent_key=parent, step_key=step, value=human)
                    s.commit()

                    if step == "experience_title":
                        return user.id, f"__experience_title_saved__:{parent}", None, human
                    return user.id, f"__experience_step_saved__:{parent}", None, step

                # как и было: summary_* свой вариант
                if free_qkey.startswith("summary_"):
                    add_draft(s, user.id, "summary", free_qkey, text)
                    s.commit()
                    return user.id, "summary_step_saved", None, free_qkey
                
                # user.py — ВНУТРИ handle_answer(), в ветке free_qkey:
                if free_qkey.startswith("edu") and "::" in free_qkey:
                    parent, step = free_qkey.split("::", 1)
                    parts = [p.strip() for p in re.split(r"[,\n;]+", text) if p.strip()]
                    human = ", ".join(parts) if parts else text
                    replace_draft(s, user.id, parent_key=parent, step_key=step, value=human)
                    s.commit()
                    return user.id, f"__edu_step_saved__:{parent}", None, step

                save_answer(s, user.id, free_qkey, text)
                next_key = get_first_unanswered(s, user.id, QUESTION_FLOW)
                return user.id, next_key, None, free_qkey

            # 🔎 1) Сначала проверяем, ждём ли «личный ввод» для какого-то summary_* подшага
            awaiting_step = pop_personal_wait(s, user.id)
            if awaiting_step:
                parts = [p.strip() for p in re.split(r"[,\n;]+", text) if p.strip()]
                human = ", ".join(parts) if parts else text
                replace_draft(s, user.id, parent_key="summary", step_key=awaiting_step, value=human)
                s.commit()
                return user.id, "summary_step_saved", None, awaiting_step

            current_key = get_first_unanswered(s, user.id, QUESTION_FLOW)

            # ▶ Если мы на шаге summary — это ответ на ТЕКУЩИЙ подвопрос
            if current_key == "summary":
                idx = drafts_count(s, user.id, "summary")
                if idx < len(SUMMARY_STEPS):
                    step_key, _ = SUMMARY_STEPS[idx]
                    add_draft(s, user.id, "summary", step_key, text)
                    s.commit()
                    return user.id, "summary_step_saved", None, step_key

            # ♦ Иначе обычное сохранение
            if not current_key:
                return user.id, None, "done", None
            save_answer(s, user.id, current_key, text)
            next_key = get_first_unanswered(s, user.id, QUESTION_FLOW)
            return user.id, next_key, None, current_key

    user_id, next_key, error, just_answered_key = await asyncio.to_thread(_work)

    if error == "no_user":
        await message.answer("Напиши /start сначала, пожалуйста.")
        return

    # 🔔 ХУК: только что ответили на target_position → запускаем фон
    if just_answered_key == "target_position":
        asyncio.create_task(chain_generate_after_position(user_id))

    if next_key == "summary_step_saved":
        await ask_next_summary_step(message.chat.id, user_id)
        return
    
    if isinstance(next_key, str) and next_key.startswith("__experience_title_saved__"):
        parent = next_key.split(":", 1)[1]         # exp1/exp2
        exp_index = int(parent.replace("exp", "") or "1")
        title_text = just_answered_key or ""
        # 🔔 ХУК: генерация задач для опыта работы
        # asyncio.create_task(generate_experience_tasks_options(user_id, title_text))
        asyncio.create_task(generate_experience_bundle(user_id, title_text))
        asyncio.create_task(generate_experience_achievements_examples(user_id, title_text))
        await ask_next_experience_step(message.chat.id, user_id, exp_index=exp_index)
        return

    # обычное «шаг сохранён» для прочих подшагов опыта
    if isinstance(next_key, str) and next_key.startswith("__experience_step_saved__"):
        parent = next_key.split(":", 1)[1]
        exp_index = int(parent.replace("exp", "") or "1")
        await ask_next_experience_step(message.chat.id, user_id, exp_index=exp_index)
        return
    
    if isinstance(next_key, str) and next_key.startswith("__edu_step_saved__"):
        parent = next_key.split(":", 1)[1]      # edu1 / edu2 / ...
        edu_index = int(parent.replace("edu", "") or "1")
        await ask_next_education_step(message.chat.id, user_id, edu_index=edu_index)
        return

    if error == "done":
        await message.answer("Все ответы уже собраны. 🚀")
        await create_order_and_run_ai(message, user_id)
        return

    if next_key:
        await send_next_with_intro(message.chat.id, next_key, user_id)
    else:
        await message.answer("🎉 Спасибо! Всё заполнено.\n\nРезюме генерируется ... (1-2 мин.)")
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
                return None, 0
            deleted = clear_user_answers(s, user.id)
            clear_user_drafts(s, user.id)
            return user.id, deleted

    user_db_id, _ = await asyncio.to_thread(_reset)

    if not user_db_id:
        await message.answer("Напиши /start сначала, пожалуйста.")
        return

    await message.answer(START_MESSAGE, reply_markup=start_keyboard())



# ──────────────────────────────────────────────────────────────────────────────
# Кнопки «flow:...»
# ──────────────────────────────────────────────────────────────────────────────
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
                    return None
                set_free_wait(s, user.id, key)
                s.commit()
                return user.id

        user_id = await asyncio.to_thread(_mark_wait)
        if not user_id:
            await callback.message.answer("Сначала /start.")
            return

        if key == "courses":
            await callback.message.answer(COURSES_ASK_TEXT)
        else:
            await callback.message.answer(PERSONAL_MESSAGE)
        return

    if action == "skip":
        def _work():
            with Session(engine) as s:
                user = s.query(User).filter_by(telegram_id=tg_id).first()
                if not user: return None, None
                save_answer(s, user.id, key, "")
                next_key = get_first_unanswered(s, user.id, QUESTION_FLOW)
                return user.id, next_key
            
        user_id, next_key = await asyncio.to_thread(_work)

        if not user_id:
            await callback.message.answer("Сначала /start.")
            return
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await callback.message.answer("Пропущено.")
        if next_key:
            await send_next_with_intro(chat_id, next_key, user_id)
        else:
            await callback.message.answer("Спасибо! Всё заполнено.")
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
        await callback.message.answer("Сначала /start, пожалуйста.")
        return

    await send_question(callback.message.chat.id, next_key, user_id)


async def ask_next_summary_step(chat_id: int, user_id: int):
    def _count():
        with Session(engine) as s:
            return drafts_completed_count(s, user_id, "summary")
    idx = await asyncio.to_thread(_count)

    if idx < len(SUMMARY_STEPS):
        step_key, prompt = SUMMARY_STEPS[idx]
        pairs = await asyncio.to_thread(get_options, step_key, user_id, None, None)

        if step_key in MULTI_SELECT_KEYS:
            def _sel():
                with Session(engine) as s:
                    return get_selected_set(s, user_id, step_key)
            selected = await asyncio.to_thread(_sel)
            markup = build_multiselect_kb(step_key, pairs, selected)
            await bot.send_message(chat_id, prompt, reply_markup=markup)
            return

        allow_personal = step_key in SUMMARY_SINGLE_WITH_PERSONAL
        markup = single_select_kb(step_key, pairs, allow_personal=allow_personal, per_row=1)
        await bot.send_message(chat_id, prompt, reply_markup=markup)
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
            return next_key
    next_key = await asyncio.to_thread(_finalize)

    if next_key:
        await send_next_with_intro(chat_id, next_key, user_id)
    else:
        await bot.send_message(chat_id, "🎉 Спасибо! Всё заполнено.\nРезюме генерируется ...(1-2 мин.)")
        await create_order_and_run_ai(chat_id, user_id)  # или твоя функция



# ──────────────────────────────────────────────────────────────────────────────
# Мастер experience
# ──────────────────────────────────────────────────────────────────────────────
EXPERIENCE_ORDER   = [k for k, _ in EXPERIENCE_STEPS]      # список по порядку
EXPERIENCE_PROMPTS = dict(EXPERIENCE_STEPS)                # key -> текст

async def ask_next_experience_step(chat_id: int, user_id: int, exp_index: int = 1):
    parent = f"exp{exp_index}"

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
        await bot.send_message(chat_id, prompt)
        return

    if step_key == "experience_title":
        pairs = await asyncio.to_thread(get_options, "experience_title", user_id, None, None)
        markup = single_select_kb(f"{parent}::{step_key}", pairs, allow_personal=True, per_row=1)
        await bot.send_message(chat_id, prompt, reply_markup=markup)
        return

    if step_key == "experience_period":
        def _mark():
            with Session(engine) as s:
                set_free_wait(s, user_id, f"{parent}::{step_key}")
                s.commit()
        await asyncio.to_thread(_mark)
        await bot.send_message(chat_id, prompt)
        return

    if step_key == "experience_tasks":
        def _role():
            with Session(engine) as s:
                from handlers.db_helpers import get_draft_value
                return get_draft_value(s, user_id, parent_key=parent, step_key="experience_title")
        role = (await asyncio.to_thread(_role)) or ""

        role_norm = normalize_position(role)

        logger.info("EXP TASKS: parent=%s, role=%r, user_id=%s", parent, role_norm, user_id)
        pairs = await asyncio.to_thread(get_options, "experience_tasks", user_id, None, role_norm)

        def _sel():
            with Session(engine) as s:
                return get_selected_set(s, user_id, f"{parent}::{step_key}")
        selected = await asyncio.to_thread(_sel)

        markup = build_multiselect_kb(f"{parent}::{step_key}", pairs, selected)
        await bot.send_message(chat_id, prompt, reply_markup=markup)
        return
    
    if step_key == "experience_achievements":
        def _mark():
            with Session(engine) as s:
                set_free_wait(s, user_id, f"{parent}::{step_key}")
                s.commit()
        await asyncio.to_thread(_mark)

        def _role():
            with Session(engine) as s:
                from handlers.db_helpers import get_draft_value
                return get_draft_value(s, user_id, parent_key=parent, step_key="experience_title")
        role = (await asyncio.to_thread(_role)) or ""
        role_norm = normalize_position(role)

        pairs = await asyncio.to_thread(get_options, "experience_achievements", user_id, None, role_norm)

        try:
            labels = [lab for _, lab in (pairs or [])][:3]
        except Exception:
            labels = []

        if not labels:
            labels = ACHIEVEMENTS_FALLBACK_EXAMPLES

        examples_text = "\n".join(f"• {t}" for t in labels)

        text = (
            "5️⃣ Достижения — пришли 1–2.\n\n"
            "Формула: действие + метрика + период + за счёт + эффект.\n"
            "Вопросы: сколько? за какой срок? как сделал?\n\n"
            + (f"Примеры для роли «{role or role_norm or 'ваша роль'}»:\n" if (role or role_norm) else "Примеры:\n")
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
    await asyncio.to_thread(_collect_and_save)

    if exp_index == 1:
        done = "👍 Отлично! У нас уже есть 1 место работы."
    else:
        done = f"👍 Отлично! У нас уже есть {exp_index} места(мест) работы."
    await bot.send_message(chat_id, done, reply_markup=exp_finish_kb(exp_index + 1))



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
    def _get_user():
        with Session(engine) as s:
            u = s.query(User).filter_by(telegram_id=tg_id).first()
            return (u.id if u else None)
    user_id = await asyncio.to_thread(_get_user)
    if not user_id:
        await callback.message.answer("Сначала /start, пожалуйста.")
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
        def _next():
            with Session(engine) as s:
                return get_first_unanswered(s, user_id, QUESTION_FLOW)
        next_key = await asyncio.to_thread(_next)

        if next_key:
            await send_next_with_intro(callback.message.chat.id, next_key, user_id)
        else:
            await callback.message.answer("🎉 Спасибо! Всё заполнено.\nРезюме генерируется ...(1-2 мин.)")
            await create_order_and_run_ai(callback.message, user_id)



# ──────────────────────────────────────────────────────────────────────────────
# Мастер education
# ──────────────────────────────────────────────────────────────────────────────
EDUCATION_ORDER   = [k for k, _ in EDUCATION_STEPS]
EDUCATION_PROMPTS = dict(EDUCATION_STEPS)

async def ask_next_education_step(chat_id: int, user_id: int, edu_index: int = 1):
    parent = f"edu{edu_index}"

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
    await bot.send_message(chat_id, prompt)


async def finalize_education_and_choice(chat_id: int, user_id: int, edu_index: int):
    parent = f"edu{edu_index}"

    def _collect_and_save():
        with Session(engine) as s:
            rows = get_drafts_values_ordered(s, user_id, parent)
            # ожидаем три подшагa: institution, specialty, period
            combined = " // ".join(rows) if rows else ""
            save_answer(s, user_id, f"education_{edu_index}", combined)
            clear_parent_drafts(s, user_id, parent)
    await asyncio.to_thread(_collect_and_save)

    if edu_index == 1:
        done = "👍 Отлично! У нас уже есть 1 образование."
    else:
        done = f"👍 Отлично! У нас уже есть {edu_index} образования."
    await bot.send_message(chat_id, done, reply_markup=edu_finish_kb(edu_index + 1))


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
    def _get_user():
        with Session(engine) as s:
            u = s.query(User).filter_by(telegram_id=tg_id).first()
            return (u.id if u else None)
    user_id = await asyncio.to_thread(_get_user)
    if not user_id:
        await callback.message.answer("Сначала /start, пожалуйста.")
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
            await callback.message.answer("🎉 Спасибо! Всё заполнено.\nРезюме генерируется ... (1-2 мин)")
            await create_order_and_run_ai(callback.message, user_id)



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
                return None
            set_free_wait(s, user.id, qkey)
            s.commit()
            return user.id

    user_id = await asyncio.to_thread(_mark_wait)
    if not user_id:
        await callback.message.answer("Сначала /start.")
        return

    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    await callback.message.answer(PERSONAL_MESSAGE)





# ──────────────────────────────────────────────────────────────────────────────
# Мультиселект
# ──────────────────────────────────────────────────────────────────────────────
_chat_locks: dict[int, asyncio.Lock] = {}
def _lock_for(chat_id: int) -> asyncio.Lock:
    if chat_id not in _chat_locks:
        _chat_locks[chat_id] = asyncio.Lock()
    return _chat_locks[chat_id]

@router.callback_query(F.data.startswith("ms:"))
async def handle_multiselect(callback: CallbackQuery):
    _, action, rest = callback.data.split(":", 2)

    qkey = rest
    val  = None

    if action == "toggle":
        qkey, val = rest.rsplit(":", 1)


    await callback.answer()  # убираем «спиннер»

    chat_id = callback.message.chat.id
    tg_id = callback.from_user.id

    async with _lock_for(chat_id):
        def _work():
            with Session(engine) as s:
                user = s.query(User).filter_by(telegram_id=tg_id).first()
                if not user:
                    return None, None, "no_user", None
                
                if action == "personal":
                    if qkey.startswith("exp") and "::" in qkey:
                        set_free_wait(s, user.id, qkey)
                    else:
                        set_personal_wait(s, user.id, qkey)
                    s.commit()
                    return user.id, None, "__await_text__", None

                if action == "toggle":
                    toggle_selection(s, user.id, qkey, val)
                    s.commit()
                    selected = get_selected_set(s, user.id, qkey)
                    return user.id, selected, None, None

                if action == "reset":
                    clear_selection(s, user.id, qkey)
                    s.commit()
                    return user.id, set(), None, None

                if action == "save":
                    text = build_answer_text_from_selection(s, user.id, qkey)
                    if not text.strip():
                        return user.id, None, "empty", None

                    if "::" in qkey and qkey.startswith("exp"):
                        parent, step = qkey.split("::", 1)
                        replace_draft(s, user.id, parent_key=parent, step_key=step, value=text)
                        clear_selection(s, user.id, qkey)
                        s.commit()
                        return user.id, f"__continue_experience__:{parent}", "saved", text

                    if qkey in SUMMARY_KEYS:
                        replace_draft(s, user.id, parent_key="summary", step_key=qkey, value=text)
                        clear_selection(s, user.id, qkey)
                        s.commit()
                        return user.id, "__continue_summary__", "saved", text

                    # Остальные мультивыборы — как было
                    save_answer(s, user.id, qkey, text)
                    clear_selection(s, user.id, qkey)
                    next_key = get_first_unanswered(s, user.id, QUESTION_FLOW)
                    s.commit()
                    return user.id, next_key, "saved", text

                return user.id, None, None, None

        user_id, payload, status, echo_text = await asyncio.to_thread(_work)
        if not user_id:
            await callback.message.answer("Сначала /start.")
            return
        
        if action == "personal":
            try:
                await callback.message.edit_reply_markup(reply_markup=None)
            except Exception as e:
                logger.debug("edit_reply_markup skip: %s", e)
            await callback.message.answer(PERSONAL_MESSAGE)
            return

        if action in {"toggle", "reset"}:
            base_qkey = qkey.split("::", 1)[1] if ("::" in qkey) else qkey

            role_for_exp = None
            if base_qkey == "experience_tasks" and "::" in qkey:
                parent = qkey.split("::", 1)[0]   # exp1 / exp2 ...
                def _role():
                    with Session(engine) as s:
                        from handlers.db_helpers import get_draft_value
                        return get_draft_value(s, user_id, parent_key=parent, step_key="experience_title")
                role_for_exp = await asyncio.to_thread(_role)

            role_for_exp_norm = normalize_position(role_for_exp)

            pairs = await asyncio.to_thread(get_options, base_qkey, user_id, None, role_for_exp_norm)

            markup = build_multiselect_kb(qkey, pairs, payload or set())
            try:
                await callback.message.edit_reply_markup(reply_markup=markup)
            except Exception as e:
                logger.debug("edit_reply_markup skip: %s", e)
            return

        if action == "save":
            await callback.message.edit_reply_markup(reply_markup=None)

            if status == "empty":
                await callback.answer("Выбери хотя бы один вариант или сбрось выбор.", show_alert=True)
                return

            if echo_text:
                await callback.message.answer(f"💾 Записал вариант(ы): {echo_text}")

            if payload == "__continue_summary__":
                await ask_next_summary_step(chat_id, user_id)
                return

            if isinstance(payload, str) and payload.startswith("__continue_experience__"):
                parent = payload.split(":", 1)[1]
                exp_index = int(parent.replace("exp", "") or "1")
                await ask_next_experience_step(chat_id, user_id, exp_index=exp_index)
                return

            if payload:
                await send_next_with_intro(chat_id, payload, user_id)
            else:
                await callback.message.answer("🎉 Спасибо! Всё заполнено.\nРезюме генерируется ...(1-2 мин.)")
                await create_order_and_run_ai(chat_id, user_id)