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
from handlers.options_repo import get_options, get_label_for
from handlers.ai_tasks import chain_generate_after_position
from _database import engine, User

from _key import (
    text_or_skip_keyboard, 
    start_keyboard, 
    single_select_kb, 
    build_multiselect_kb,
    continue_kb)

from _utils import (
    logger,
    CHOICES, 
    INTRO_BEFORE, 
    QUESTION_FLOW, 
    START_MESSAGE,
    SUMMARY_STEPS,
    PERSONAL_MESSAGE,
    EXPERIENCE_STEPS)

from handlers.ms_repo import (
    get_selected_set, 
    toggle_selection, 
    clear_selection, 
    build_answer_text_from_selection)


router = Router()

# --- constants --- 
SUMMARY_SINGLE_WITH_PERSONAL = {"summary_industry"}
MULTI_SELECT_KEYS = {"summary_tasks", "summary_values"}
SUMMARY_KEYS = {k for k, _ in SUMMARY_STEPS}

PAUSE_BEFORE = {"summary"}

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

    # ▶ Если это summary — запускаем мини-мастер
    if qkey == "summary" and user_id:
        await bot.send_message(chat_id, PROMPTS[qkey])
        def _reset():
            with Session(engine) as s:
                clear_parent_drafts(s, user_id, "summary")
                s.commit()
        await asyncio.to_thread(_reset)
        await ask_next_summary_step(chat_id, user_id)
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
# Обработчик одиночного выбора (inline buttons ans:<qkey>:<value>)
# ──────────────────────────────────────────────────────────────────────────────
@router.callback_query(F.data.startswith("ans:"))
async def handle_choice(callback: CallbackQuery):
    await callback.answer()
    _, key, value = callback.data.split(":", 2)
    tg_id = callback.from_user.id

    def _work():
        with Session(engine) as s:
            user = s.query(User).filter_by(telegram_id=tg_id).first()
            if not user:
                return None, None, "no_user", None
            
            label_db = get_label_for(s, key, value, user.id)
            label = label_db or dict(CHOICES.get(key, [])).get(value, value)

            # ▶ Подвопросы summary
            if key.startswith("summary_"):
                # красивая подпись кнопки
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
                if free_qkey.startswith("summary_"):
                    add_draft(s, user.id, "summary", free_qkey, text)
                    s.commit()
                    return user.id, "summary_step_saved", None, free_qkey
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

    if error == "done":
        await message.answer("Все ответы уже собраны. 🚀")
        await create_order_and_run_ai(message, user_id)
        return

    if next_key:
        await send_next_with_intro(message.chat.id, next_key, user_id)
    else:
        await message.answer("🎉 Спасибо! Всё заполнено.")
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
            deleted = clear_user_answers(s, user.id)   # ← реальная очистка
            clear_user_drafts(s, user.id)              # ← опционально
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
    await callback.answer()  # снять "спиннер
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

        # ставим флаг универсального ожидания текста для ЛЮБОГО ключа
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
@router.callback_query(F.data.startswith("cont:"))
async def handle_continue(callback: CallbackQuery):
    await callback.answer()
    _, next_key = callback.data.split(":", 1)

    # спрячем кнопку под сообщением с интро
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    # найдём user_id по tg_id (нужно для send_question)
    tg_id = callback.from_user.id
    def _get_user_id():
        with Session(engine) as s:
            u = s.query(User).filter_by(telegram_id=tg_id).first()
            return u.id if u else None
    user_id = await asyncio.to_thread(_get_user_id)
    if not user_id:
        await callback.message.answer("Сначала /start, пожалуйста.")
        return

    # продолжаем обычный поток
    await send_question(callback.message.chat.id, next_key, user_id)


async def ask_next_summary_step(chat_id: int, user_id: int):
    # какой по счёту подвопрос уже сохранён?
    def _count():
        with Session(engine) as s:
            return drafts_completed_count(s, user_id, "summary")
    idx = await asyncio.to_thread(_count)

    # если остались подвопросы — задаём следующий
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

    await bot.send_message(chat_id, "✅ Собрал блок «Обо мне». Едем дальше.")
    if next_key:
        await send_next_with_intro(chat_id, next_key, user_id)
    else:
        await bot.send_message(chat_id, "🎉 Спасибо! Всё заполнено.\nРезюме генерируется ...")
        await create_order_and_run_ai(chat_id, user_id)  # или твоя функция



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
    parts = callback.data.split(":", 3)
    _, action, qkey, *rest = parts
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
                    set_personal_wait(s, user.id, qkey)
                    s.commit()
                    return user.id, None, "__await_text__", None

                if action == "toggle":
                    value = rest[0]
                    toggle_selection(s, user.id, qkey, value)
                    s.commit()
                    selected = get_selected_set(s, user.id, qkey)
                    # вернём свежий список выбранного
                    return user.id, selected, None, None

                if action == "reset":
                    clear_selection(s, user.id, qkey)
                    s.commit()
                    return user.id, set(), None, None

                if action == "save":
                    text = build_answer_text_from_selection(s, user.id, qkey)
                    if not text.strip():
                        return user.id, None, "empty", None

                    if qkey in SUMMARY_KEYS:
                        # КЛЮЧЕВОЕ: сохраняем в drafts родителя 'summary'
                        replace_draft(s, user.id, parent_key="summary", step_key=qkey, value=text)
                        clear_selection(s, user.id, qkey)
                        s.commit()
                        return user.id, "__continue_summary__", "saved", text

                    # Остальные мультивыборы — по старой схеме
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
            # перестроить клавиатуру в ТОМ ЖЕ сообщении
            pairs = await asyncio.to_thread(get_options, qkey, user_id, None, None)
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

            # отзеркаливание
            if echo_text:
                await callback.message.answer(f"💾 Записал вариант(ы): {echo_text}")

            if payload == "__continue_summary__":
                # Идём к следующему подшагу (или завершаем summary)
                await ask_next_summary_step(chat_id, user_id)
                return

            if payload:
                await send_next_with_intro(chat_id, payload, user_id)
            else:
                await callback.message.answer("🎉 Спасибо! Всё заполнено.\nРезюме генерируется ...")
                await create_order_and_run_ai(chat_id, user_id)  # см. примечание ниже