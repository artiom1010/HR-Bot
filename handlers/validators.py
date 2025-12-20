# handlers/validators.py
import re
from typing import Tuple

from i18n import t

Name = Tuple[bool, str | None, str | None]  # ok, normalized, error_key

# ──────────────────────────────────────────────────────────────────────────────
# Вспомогательные функции - Ограничение по словам
# ──────────────────────────────────────────────────────────────────────────────
def _word_count(s: str) -> int:
    return len(re.findall(r"\w+", s or ""))

def _too_many_words(s: str, limit: int) -> bool:
    return _word_count(s) > limit


# ——— FULL NAME ———
# 2 слова, кириллица/латиница, допускаем дефисы и пробелы между
_NAME_RE = re.compile(r"^\s*([A-Za-zА-Яа-яЁё\-]+)\s+([A-Za-zА-Яа-яЁё\-]+)\s*$")

def validate_full_name(text: str) -> Name:
    m = _NAME_RE.match(text or "")
    if not m:
        return False, None, "bad_full_name"
    first, last = m.group(1), m.group(2)
    # нормализация: Имя Фамилия c заглавной буквы
    norm = f"{first.capitalize()} {last.capitalize()}"
    return True, norm, None

# ——— PHONE ———
# только цифры, длина 6–15 (можно предварительно убрать +, (), -, пробелы)
def validate_phone(text: str) -> Name:
    raw = re.sub(r"[^\d]", "", text or "")
    if not (6 <= len(raw) <= 15):
        return False, None, "bad_phone"
    return True, raw, None

# ——— EMAIL ———
# латиница, в нижнем регистре, простая проверка `something@something.tld`
_EMAIL_RE = re.compile(r"^[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}$")

def validate_email(text: str) -> Name:
    t = (text or "").strip().lower()
    # отбрасываем пробелы и запрещённые символы по краям
    if not _EMAIL_RE.match(t):
        return False, None, "bad_email"
    return True, t, None

# ——— LINKS ———
# одна или несколько ссылок через запятую; каждая должна начинаться с http(s)://
_URL_RE = re.compile(r"^https?://", re.IGNORECASE)

def validate_links(text: str) -> Name:
    parts = [p.strip() for p in (text or "").split(",") if p.strip()]
    if not parts:
        return False, None, "bad_links_empty"
    if not all(_URL_RE.match(p) for p in parts):
        return False, None, "bad_links_format"
    # вернём нормализованно: через запятую и пробел
    return True, ", ".join(parts), None


# ──────────────────────────────────────────────────────────────────────────────
# Основная функция валидации поля по его ключу
# ──────────────────────────────────────────────────────────────────────────────
def validate_field(current_key: str, text: str, lang: str = "ru"):
    ok, normalized, err = (True, text, None)
    hint = None

    if current_key == "full_name":
        ok, normalized, err = validate_full_name(text)
        if not ok and err == "bad_full_name":
            hint = t("🖋 Имя и фамилия — двумя словами. Пример: «Сергей Петренко».", lang)

    elif current_key == "contact_phone":
        ok, normalized, err = validate_phone(text)
        if not ok and err == "bad_phone":
            hint = t("📞 Введи номер цифрами (6–15 знаков). Пример: 067309067.", lang)

    elif current_key == "contact_email":
        ok, normalized, err = validate_email(text)
        if not ok and err == "bad_email":
            hint = t("✉️ Похоже, это не e-mail. Пример: m.greenwood@gmail.com", lang)

    elif current_key == "contact_links":
        ok, normalized, err = validate_links(text)
        if not ok:
            if err == "bad_links_empty":
                hint = t("🔗 Добавь хотя бы одну ссылку. Пример: https://www.linkedin.com/jobs", lang)
            elif err == "bad_links_format":
                hint = t("🔗 Каждая ссылка должна начинаться с http:// или https://", lang)

    return ok, normalized, err, hint