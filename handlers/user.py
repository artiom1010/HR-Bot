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
    clear_parent_drafts, 
    drafts_completed_count,
    drafts_count, add_draft, replace_draft,
    pop_personal_wait,
    set_personal_wait,
    pop_free_wait,
    set_free_wait,
    get_draft_value)

from handlers.order import create_order_and_run_ai
from handlers.bot_app import bot
from handlers.options_repo import get_options, get_label_for, normalize_position, get_user_context
from handlers.ai_options import (
    chain_generate_after_position,
    generate_experience_bundle)

from _database import engine, User

from _key import (
    text_or_skip_keyboard, 
    start_keyboard, 
    single_select_kb, 
    build_multiselect_kb,
    continue_kb,
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
    PERSONAL_MESSAGE_POSITION, USE_KEYBOARD_MSG)

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
MULTI_SELECT_KEYS = {"summary_tasks", "summary_values", "experience_tasks", "skills_hard", "skills_tools"}
PAUSE_BEFORE = {"summary", "experience", "skills"}

WORD_LIMIT = 100
JOB_DESC_WORD_LIMIT = 10000
TRGT_POSITION_MAX = 50
EXP_POSITION_MAX  = 50



# ──────────────────────────────────────────────────────────────────────────────
# Gate
# ──────────────────────────────────────────────────────────────────────────────
BUTTON_TEXT_STEPS = {"job_description", "contact_links"}  # тут всё, что точно текстом
def _is_button_step(session: Session, user_id: int, qkey: str) -> bool:
    if not qkey:
        return False
    base = qkey.split("::", 1)[1] if "::" in qkey else qkey
    if base in BUTTON_TEXT_STEPS:
        return False
    if base in MULTI_SELECT_KEYS or base.startswith("summary_"):
        return True
    pairs = get_options(base, user_id, None, None)
    return bool(pairs)



_text_gate: dict[int, str] = {}

def open_text_gate(user_id: int, key: str) -> None:
    _text_gate[user_id] = key

def close_text_gate(user_id: int) -> None:
    _text_gate.pop(user_id, None)

def gate_for(user_id: int) -> str | None:
    return _text_gate.get(user_id)

KEYBOARD_ONLY_MSG = "Пожалуйста, используй кнопки под сообщением."




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


# ──────────────────────────────────────────────────────────────────────────────
# Навигация по вопросам
# ──────────────────────────────────────────────────────────────────────────────
async def send_next_with_intro(chat_id: int, next_key: str, user_id: int | None = None):
    intro = INTRO_BEFORE.get(next_key)
    if intro and next_key in PAUSE_BEFORE:
        await bot.send_message(chat_id, intro, reply_markup=continue_kb(next_key))
        if user_id:
            open_text_gate(user_id, f"intro:{next_key}")
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
        def _reset():
            with Session(engine) as s:
                clear_parent_drafts(s, user_id, "courses")
                s.commit()
        await asyncio.to_thread(_reset)
        def _mark_done():
            with Session(engine) as s:
                save_answer(s, user_id, "courses", "")
        await asyncio.to_thread(_mark_done)
        await bot.send_message(chat_id, COURSES_INTRO, reply_markup=courses_kb())
        return
    
    # ▶ Если это skills — запускаем мини-мастер навыков
    if qkey == "skills" and user_id:
        def _reset():
            with Session(engine) as s:
                clear_parent_drafts(s, user_id, "skills")
                s.commit()
        await asyncio.to_thread(_reset)
        await ask_next_skills_step(chat_id, user_id)
        return

    pairs = await asyncio.to_thread(get_options, qkey, user_id, None, None)
    
    markup = None
    if markup is not None:
        await bot.send_message(chat_id, PROMPTS[qkey], reply_markup=markup)
        if user_id and qkey not in ("job_description", "contact_links"):
            open_text_gate(user_id, qkey)
        return

    if qkey in MULTI_SELECT_KEYS and user_id and pairs:
        # вытягиваем выбранные value из БД
        def _sel():
            with Session(engine) as s:
                return get_selected_set(s, user_id, qkey)
        selected = await asyncio.to_thread(_sel)
        pairs = [(v, l) for (v, l) in (pairs or []) if _fits_keyboard_label(l)]
        markup = build_multiselect_kb(qkey, pairs, selected)
    elif pairs:
        allow_personal = qkey in SUMMARY_SINGLE_WITH_PERSONAL
        markup = single_select_kb(qkey, pairs, allow_personal=allow_personal, per_row=1)
    # elif qkey == "job_description":
    elif qkey in ("job_description", "contact_links"):
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
            await callback.message.answer("Напишите /start сначала, пожалуйста.")
            return

        if next_key:
            await send_next_with_intro(callback.message.chat.id, next_key, user_id)
        else:
            await callback.message.answer("🎉 Спасибо! Всё заполнено.\nРезюме генерируется ...(1-2 мин.)")
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
    if user_id:
        close_text_gate(user_id)

    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception as e:
        logger.debug("edit_reply_markup skip: %s", e)

    if error == "no_user":
        await callback.message.answer("Напишите /start сначала, пожалуйста.")
        return

    if echo_label:
        await callback.message.answer(f"🌟 Готово! Сохранил вариант(ы): {echo_label}")

    if isinstance(next_key, str) and next_key.startswith("__experience_title_saved__"):
        parent = next_key.split(":", 1)[1]
        exp_index = int(parent.replace("exp", "") or "1")
        # 🔔 ХУК: генерация задач для опыта работы
        asyncio.create_task(generate_experience_bundle(user_id, echo_label))
        await ask_next_experience_step(callback.message.chat.id, user_id, exp_index=exp_index)
        return

    if next_key == "summary_step_saved":
        await ask_next_summary_step(callback.message.chat.id, user_id)
        return

    if next_key:
        await send_next_with_intro(callback.message.chat.id, next_key, user_id)
    else:
        await callback.message.answer("🎉 Спасибо! Всё заполнено.\nРезюме генерируется ...(1-2 мин.)")
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
@router.message(~F.text)  # любое не-текстовое
async def handle_non_text(message: Message):
    await message.answer("Я пока понимаю только текст 😊 Пришли, пожалуйста, текстом.")

def _limit_for_key(step_key: str | None) -> int:
    if not step_key:
        return WORD_LIMIT
    base = step_key.split("::", 1)[1] if "::" in step_key else step_key
    return JOB_DESC_WORD_LIMIT if base == "job_description" else WORD_LIMIT

@router.message(F.text & ~F.text.startswith("/"))
async def handle_answer(message: Message):
    tg_id = message.from_user.id
    raw_text = (message.text or "").strip()

    def _work(text_in: str):
        with Session(engine) as s:
            user = s.query(User).filter_by(telegram_id=tg_id).first()
            if not user:
                return None, None, "no_user", None

            free_qkey = pop_free_wait(s, user.id)
            awaiting_step = pop_personal_wait(s, user.id)

            if not free_qkey and not awaiting_step:
                if gate_for(user.id):
                    return user.id, None, "keyboard_only", gate_for(user.id)

            step_for_limit = (
                free_qkey or 
                awaiting_step or 
                get_first_unanswered(s, user.id, QUESTION_FLOW)
                )

            lim = _limit_for_key(step_for_limit)
            if _too_many_words(text_in, lim):
                return user.id, None, "too_many_words", lim

            if free_qkey:
                if free_qkey.startswith("exp") and "::" in free_qkey:
                    parent, step = free_qkey.split("::", 1)
                    parts = [p.strip() for p in re.split(r"[,\n;]+", text_in) if p.strip()]
                    human = ", ".join(parts) if parts else text_in
                    if step == "experience_title" and len(human) > EXP_POSITION_MAX:
                        return user.id, None, "exp_title_too_long", None
                    replace_draft(s, user.id, parent_key=parent, step_key=step, value=human)
                    s.commit()

                    if step == "experience_title":
                        return user.id, f"__experience_title_saved__:{parent}", None, human
                    return user.id, f"__experience_step_saved__:{parent}", None, step

                # как и было: summary_* свой вариант
                if free_qkey.startswith("summary_"):
                    add_draft(s, user.id, "summary", free_qkey, text_in)
                    s.commit()
                    return user.id, "summary_step_saved", None, free_qkey
                
                # user.py — ВНУТРИ handle answer(), в ветке free_qkey:
                if free_qkey.startswith("edu") and "::" in free_qkey:
                    parent, step = free_qkey.split("::", 1)
                    parts = [p.strip() for p in re.split(r"[,\n;]+", text_in) if p.strip()]
                    human = ", ".join(parts) if parts else text_in
                    replace_draft(s, user.id, parent_key=parent, step_key=step, value=human)
                    s.commit()
                    return user.id, f"__edu_step_saved__:{parent}", None, step
                
                # курсы: каждый текст — отдельная запись в parent="courses"
                if free_qkey.startswith("courses") and "::" in free_qkey:
                    parts = [p.strip() for p in re.split(r"[,\n;]+", text_in) if p.strip()]
                    human = ", ".join(parts) if parts else text_in
                    # для курсов используем add_draft (НЕ replace), чтобы не затирать предыдущие
                    add_draft(s, user.id, parent_key="courses", step_key="course_item", value=human)
                    s.commit()
                    return user.id, "__courses_step_saved__", None, "courses"

                if free_qkey.startswith("skills_"):
                    parts = [p.strip() for p in re.split(r"[,\n;]+", text_in) if p.strip()]
                    human = ", ".join(parts) if parts else text_in
                    replace_draft(s, user.id, parent_key="skills", step_key=free_qkey, value=human)
                    s.commit()
                    return user.id, "__continue_skills__", None, free_qkey

                if free_qkey == "lang_custom":
                    name = _norm_lang_label(text_in)   # ту же утилиту можно скопировать сюда или импортнуть
                    replace_draft(s, user.id, parent_key="skills", step_key="skills_langs__tmp_name", value=name)
                    s.commit()
                    # просим выбрать уровень
                    return user.id, "__ask_level_for_custom_lang__", None, name

                if free_qkey in ("full_name", "contact_phone", "contact_email", "contact_links"):
                    ok, normalized, err, hint = validate_field(free_qkey, text_in)
                    if not ok:
                        return user.id, None, "bad_input", hint
                    text_in = normalized
            
                save_answer(s, user.id, free_qkey, text_in)
                next_key = get_first_unanswered(s, user.id, QUESTION_FLOW)
                return user.id, next_key, None, free_qkey

            awaiting_step = pop_personal_wait(s, user.id)
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

            current_key = get_first_unanswered(s, user.id, QUESTION_FLOW)

            # если сейчас шаг кнопочный, а мы НЕ ждём текст — блокируем текстовый ввод
            if (not free_qkey) and (not awaiting_step) and _is_button_step(s, user.id, current_key):
                return user.id, current_key, "need_button", None

            # защита длины для target_position
            if current_key == "target_position" and len(text_in) > TRGT_POSITION_MAX:
                return user.id, None, "target_too_long", None
            
            word_limit = JOB_DESC_WORD_LIMIT if current_key == "job_description" else WORD_LIMIT
            if _too_many_words(text_in, word_limit):
                return user.id, None, "too_many_words", word_limit

            if current_key in ("full_name", "contact_phone", "contact_email", "contact_links"):
                ok, normalized, err, hint = validate_field(current_key, text_in)
                if not ok:
                    return user.id, None, "bad_input", hint  # хинт отдадим наружу
                text_to_save = normalized
            else:
                text_to_save = text_in

            # ▶ Если мы на шаге summary — это ответ на ТЕКУЩИЙ подвопрос
            if current_key == "summary":
                idx = drafts_count(s, user.id, "summary")
                if idx < len(SUMMARY_STEPS):
                    step_key, _ = SUMMARY_STEPS[idx]
                    add_draft(s, user.id, "summary", step_key, text_to_save)
                    s.commit()
                    return user.id, "summary_step_saved", None, step_key

            # ♦ Иначе обычное сохранение
            if not current_key:
                return user.id, None, "done", None
            save_answer(s, user.id, current_key, text_to_save)
            next_key = get_first_unanswered(s, user.id, QUESTION_FLOW)
            return user.id, next_key, None, current_key

    user_id, next_key, error, payload = await asyncio.to_thread(_work, raw_text)

    if error == "keyboard_only":
        await message.answer(KEYBOARD_ONLY_MSG)
        return
    
    if error == "need_button":
        await message.answer(USE_KEYBOARD_MSG)
        await send_question(message.chat.id, next_key, user_id)
        return

    if error == "too_many_words":
        await message.answer(f"💡 Сообщение длинновато. Давай уложимся в {payload} слов 🙂")
        return

    # ---------- обработка ошибок из _work ----------
    if error == "no_user":
        await message.answer("Напиши /start сначала, пожалуйста.")
        return
    if error == "target_too_long":
        await message.answer(
            f"💡 Название должности немного длинное. Давай уложим его в {TRGT_POSITION_MAX} символов.\n"
            f"Например: «Product Manager», «Архитектор»."
        )
        return
    if error == "exp_title_too_long":
        await message.answer(
            f"💡 Должность в опыте получилась длинной. Нужно до {EXP_POSITION_MAX} символов.\n"
            f"Примеры: «QA», «Бариста», «Аналитик»."
        )
        return
    if error == "bad_input":
        await message.answer(payload)
        return

    # 🔔 ХУК: только что ответили на target_position → запускаем фон
    if not error and payload == "target_position":
        asyncio.create_task(chain_generate_after_position(user_id))

    if next_key == "summary_step_saved":
        await ask_next_summary_step(message.chat.id, user_id)
        return
    
    if isinstance(next_key, str) and next_key.startswith("__experience_title_saved__"):
        parent = next_key.split(":", 1)[1]         # exp1/exp2
        exp_index = int(parent.replace("exp", "") or "1")
        title_text = payload or ""
        # 🔔 ХУК: генерация задач для опыта работы
        asyncio.create_task(generate_experience_bundle(user_id, title_text))
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
    
    # повторный показ клавиатуры курсов после добавления пункта
    if next_key == "__courses_step_saved__":
        await ask_next_courses_step(message.chat.id, user_id)
        return

    if error == "done":
        await message.answer("Все ответы уже собраны. 🚀")
        await create_order_and_run_ai(message, user_id)
        return

    if next_key == "__continue_skills__":
        await ask_next_skills_step(message.chat.id, user_id)
        return
    
    if next_key == "__ask_level_for_custom_lang__":
        await message.answer(f"Язык: {payload or ''}\nТеперь выбери уровень:")
        await bot.send_message(message.chat.id, "Уровень владения:", reply_markup=lang_levels_custom_kb())
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
            clear_user_answer_options(s, user.id)
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
                    return None, None
                wait_key = "courses::course_item" if key == "courses" else key
                set_free_wait(s, user.id, wait_key)
                s.commit()
                return user.id, wait_key
        user_id, wait_key = await asyncio.to_thread(_mark_wait)
        if not user_id:
            await callback.message.answer("Сначала /start.")
            return
        close_text_gate(user_id) 
        msg = COURSES_ASK_TEXT if key == "courses" else personal_msg_for(wait_key)
        await callback.message.answer(msg)
        return

    if action == "skip":
        def _work():
            with Session(engine) as s:
                user = s.query(User).filter_by(telegram_id=tg_id).first()
                if not user: 
                    return None, None
                if key == "courses":
                    # собрать все курсы в одну строку и очистить черновики
                    from handlers.db_helpers import get_drafts_values_ordered
                    parts = get_drafts_values_ordered(s, user.id, "courses")
                    combined = " // ".join(parts) if parts else ""
                    save_answer(s, user.id, "courses", combined)
                    clear_parent_drafts(s, user.id, "courses")
                else:
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
        
        close_text_gate(user_id)
        if next_key:
            await send_next_with_intro(chat_id, next_key, user_id)
        else:
            await callback.message.answer("🎉 Спасибо! Всё заполнено.\nРезюме генерируется ...(1-2 мин.)")
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
            open_text_gate(user_id, step_key)
            return

        allow_personal = step_key in SUMMARY_SINGLE_WITH_PERSONAL
        markup = single_select_kb(step_key, pairs, allow_personal=allow_personal, per_row=1)
        await bot.send_message(chat_id, prompt, reply_markup=markup)
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
        close_text_gate(user_id)
        await bot.send_message(chat_id, prompt)
        return

    if step_key == "experience_title":
        pairs = await asyncio.to_thread(get_options, "experience_title", user_id, None, None)
        markup = single_select_kb(f"{parent}::{step_key}", pairs, allow_personal=True, per_row=1)
        await bot.send_message(chat_id, prompt, reply_markup=markup)
        open_text_gate(user_id, f"{parent}::{step_key}")
        return

    if step_key == "experience_period":
        def _mark():
            with Session(engine) as s:
                set_free_wait(s, user_id, f"{parent}::{step_key}")
                s.commit()
        await asyncio.to_thread(_mark)
        close_text_gate(user_id)
        await bot.send_message(chat_id, prompt)
        return

    if step_key == "experience_tasks":
        def _role():
            with Session(engine) as s:
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
        open_text_gate(user_id, f"{parent}::{step_key}")
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
            + (f"Примеры для твоей роли «{role or role_norm or 'ваша роль'}»:\n" if (role or role_norm) else "Примеры:\n")
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
# Мастер courses
# ──────────────────────────────────────────────────────────────────────────────
async def ask_next_courses_step(chat_id: int, user_id: int):
    # считаем, сколько черновиков уже набрали
    def _count():
        with Session(engine) as s:
            return drafts_count(s, user_id, "courses")
    n = await asyncio.to_thread(_count)

    # показываем «👍 уже N курсов» и те же кнопки: [+ Добавить курс] / [⏭ Далее]
    text = f"👍 Отлично! У нас уже есть {n} курс{'а' if 1 < n < 5 else '' if n==1 else 'ов'}."
    await bot.send_message(chat_id, text, reply_markup=courses_kb())




# ────────────────────────────────────────────────────────────────────────────── 
# Мастер skills
# ──────────────────────────────────────────────────────────────────────────────
SKILLS_STEPS = [
    ("skills_hard",  SKILLS_MSG_HARD),
    ("skills_tools", SKILLS_MSG_TOOLS),
    ("skills_langs", LANGS_MSG),
]

async def ask_next_skills_step(chat_id: int, user_id: int):
    def _count():
        with Session(engine) as s:
            return drafts_completed_count(s, user_id, "skills")
    idx = await asyncio.to_thread(_count)

    if idx < len(SKILLS_STEPS):
        step_key, prompt = SKILLS_STEPS[idx]

        # 1-2 шаги — мультивыбор
        if step_key in {"skills_hard", "skills_tools"}:
            ctx = await asyncio.to_thread(get_user_context, user_id)
            raw_pos = (ctx.get("position") or ctx.get("target_position") or "").strip()
            pos_norm = normalize_position(raw_pos) if raw_pos else None
            pairs = await asyncio.to_thread(get_options, step_key, user_id, None, pos_norm)
            def _sel():
                with Session(engine) as s:
                    return get_selected_set(s, user_id, step_key)
            selected = await asyncio.to_thread(_sel)
            markup = build_multiselect_kb(step_key, pairs, selected)
            await bot.send_message(chat_id, prompt, reply_markup=markup)
            open_text_gate(user_id, step_key)
            return

        # 3 шаг — языки
        if step_key == "skills_langs":
            await bot.send_message(chat_id, prompt, reply_markup=lang_actions_kb())
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
            hard  = (get_draft_value(s, user_id, "skills", "skills_hard")  or "").strip()
            tools = (get_draft_value(s, user_id, "skills", "skills_tools") or "").strip()
            langs = (get_draft_value(s, user_id, "skills", "skills_langs") or "").strip()

            skills_text = " // ".join([p for p in [hard, tools] if p])
            langs_text  = _make_langs_text(langs) if langs else ""

            if skills_text:
                save_answer(s, user_id, "skills", skills_text)
            if langs_text or "languages" in dict(QUESTION_FLOW):
                save_answer(s, user_id, "languages", langs_text)

            clear_parent_drafts(s, user_id, "skills")
            next_key = get_first_unanswered(s, user_id, QUESTION_FLOW)
            return next_key

    next_key = await asyncio.to_thread(_finish)
    if next_key:
        await send_next_with_intro(chat_id, next_key, user_id)
    else:
        await bot.send_message(chat_id, "🎉 Спасибо! Всё заполнено.\n\nРезюме генерируется ... (1–2 мин.)")
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

def _upsert_lang_pair(s: Session, user_id: int, lang_label: str, lvl_label: str) -> str:
    # читаем текущий текст черновика
    cur = get_draft_value(s, user_id, "skills", "skills_langs") or ""
    items = {}
    for ch in [p.strip() for p in cur.split("//") if p.strip()]:
        if " – " in ch:
            lng, lvl = ch.split(" – ", 1)
            items[lng.strip()] = lvl.strip()
    items[lang_label] = lvl_label  # upsert
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

    def _get_uid():
        with Session(engine) as s:
            u = s.query(User).filter_by(telegram_id=tg_id).first()
            return (u.id if u else None)
    user_id = await asyncio.to_thread(_get_uid)
    if not user_id:
        await callback.message.answer("Сначала /start.")
        return

    try:
        _, action, *rest = callback.data.split(":")
    except ValueError:
        return

    # UI-переходы
    if action == "add":
        await callback.message.edit_reply_markup(reply_markup=lang_choose_kb())
        return
    if action == "back":
        await callback.message.edit_reply_markup(reply_markup=lang_actions_kb())
        return
    if action == "pick":
        code = rest[0]
        await callback.message.edit_reply_markup(reply_markup=lang_levels_kb(code))
        return

    # 👤 Кастомный язык — просим текстом
    if action == "custom":                                         # ← НОВОЕ
        # ждём текст от пользователя с ключом 'lang_custom'
        def _mark_wait():
            with Session(engine) as s:
                set_free_wait(s, user_id, "lang_custom")
                s.commit()
        await asyncio.to_thread(_mark_wait)
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await callback.message.answer("Напиши язык (например: Польский, Итальянский).")
        return

    # Выбор уровня для языка из списка
    if action == "level":
        code, lvl_code = rest[0], rest[1]
        lang_label = dict(LANG_LIST).get(code, code)
        lvl_label  = dict(LANG_LEVELS).get(lvl_code, lvl_code)

        pair = f"{lang_label} – {lvl_label}"

        def _save_pair():
            with Session(engine) as s:
                return _upsert_lang_pair(s, user_id, lang_label, lvl_label)

        await asyncio.to_thread(_save_pair)
        # await callback.message.answer(f"✅ Добавил: {pair}")
        await callback.answer(f"✅ Добавлено: {pair}", show_alert=False)
        # await callback.message.edit_reply_markup(reply_markup=lang_actions_kb())
        extra = await asyncio.to_thread(_current_langs_text, user_id)
        text = LANGS_MSG + extra
        await callback.message.edit_text(text, reply_markup=lang_actions_kb())
        await callback.answer("✅ Сохранено", show_alert=False)
        return

    # Выбор уровня для КАСТОМНОГО языка
    if action == "level_custom":                                   # ← НОВОЕ
        lvl_code = rest[0]
        lvl_label = dict(LANG_LEVELS).get(lvl_code, lvl_code)

        def _finish_custom():
            with Session(engine) as s:
                name = get_draft_value(s, user_id, "skills", "skills_langs__tmp_name") or ""
                lang_label = _norm_lang_label(name)
                _upsert_lang_pair(s, user_id, lang_label, lvl_label)
                return lang_label

        lang_label = await asyncio.to_thread(_finish_custom)
        # await callback.message.answer(f"✅ Добавил: {lang_label} – {lvl_label}")
        await callback.answer(f"✅ {lang_label} – {lvl_label}", show_alert=False)
        # await callback.message.edit_reply_markup(reply_markup=lang_actions_kb())
        extra = await asyncio.to_thread(_current_langs_text, user_id)
        text = LANGS_MSG + extra
        await callback.message.edit_text(text, reply_markup=lang_actions_kb())
        await callback.answer("✅ Сохранено", show_alert=False)
        return

    # Готово / пропустить
    if action in {"done", "skip"}:
        await callback.message.edit_reply_markup(reply_markup=None)
        await finalize_skills_and_go_next(chat_id, user_id)
        return

def _current_langs_text(user_id: int) -> str:
    with Session(engine) as s:
        cur = get_draft_value(s, user_id, "skills", "skills_langs") or ""
    items = [p.strip() for p in cur.split("//") if p.strip()]
    if not items:
        return ""
    return "\n\n──────────────\n🌟 Сейчас выбрано:\n" + "\n".join(f"• {it}" for it in items) + "\n──────────────"
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

    await callback.message.answer(personal_msg_for(qkey))





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

    qkey = rest
    val  = None

    if action == "toggle":
        qkey, val = rest.rsplit(":", 1)

    await callback.answer()

    chat_id = callback.message.chat.id
    tg_id = callback.from_user.id

    if action == "save":
        now = asyncio.get_event_loop().time()
        key_ = (chat_id, qkey)
        if _last_save_ts.get(key_, 0) + 0.6 > now:
            await callback.answer("Уже сохраняю…", show_alert=False)
            return
        _last_save_ts[key_] = now

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

                    if qkey.startswith("skills_"):
                        replace_draft(s, user.id, parent_key="skills", step_key=qkey, value=text)
                        clear_selection(s, user.id, qkey)
                        s.commit()
                        return user.id, "__continue_skills__", "saved", text

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
            msg = personal_msg_for(qkey)
            close_text_gate(user_id)
            await callback.message.answer(msg)
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
            pairs = [(v, l) for (v, l) in (pairs or []) if _fits_keyboard_label(l)]
            pairs = pairs or []
            markup = build_multiselect_kb(qkey, pairs, payload or set())
            try:
                await callback.message.edit_reply_markup(reply_markup=markup)
            except Exception as e:
                logger.debug("edit_reply_markup skip: %s", e)
            return

        if action == "save":
            if status == "empty":
                await callback.answer("Выбери хотя бы один вариант или сбрось выбор.", show_alert=True)
                base_qkey = qkey.split("::", 1)[1] if ("::" in qkey) else qkey
                role_for_exp = None
                if base_qkey == "experience_tasks" and "::" in qkey:
                    parent = qkey.split("::", 1)[0]
                    def _role():
                        with Session(engine) as s:
                            from handlers.db_helpers import get_draft_value
                            return get_draft_value(s, user_id, parent_key=parent, step_key="experience_title")
                    role_for_exp = await asyncio.to_thread(_role)

                role_for_exp_norm = normalize_position(role_for_exp)

                pairs = await asyncio.to_thread(get_options, base_qkey, user_id, None, role_for_exp_norm)
                pairs = [(v, l) for (v, l) in (pairs or []) if _fits_keyboard_label(l)]
                def _sel():
                    with Session(engine) as s:
                        return get_selected_set(s, user_id, qkey)
                selected_now = await asyncio.to_thread(_sel)
                try:
                    await callback.message.edit_reply_markup(
                        reply_markup=build_multiselect_kb(qkey, pairs, selected_now or set())
                    )
                except Exception as e:
                    logger.debug("edit_reply_markup (empty-save) skip: %s", e)
                return
            # 2) а вот ТЕПЕРЬ, когда точно не пусто — снимаем клавиатуру
            await callback.message.edit_reply_markup(reply_markup=None)
            close_text_gate(user_id)

            if echo_text:
                await callback.message.answer(f"🌟 Готово! Сохранил вариант(ы): {echo_text}")

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
                await callback.message.answer("🎉 Спасибо! Всё заполнено.\nРезюме генерируется ...(1-2 мин.)")
                await create_order_and_run_ai(chat_id, user_id)