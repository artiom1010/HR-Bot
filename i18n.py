import json
from pathlib import Path

_TRANSLATIONS: dict[str, dict[str, str]] = {}

def load_translations():
    """Загружает переводы из JSON-файлов при старте бота."""
    global _TRANSLATIONS
    
    translations_dir = Path(__file__).parent / "translations"
    
    for lang in ["en", "ro"]:
        filepath = translations_dir / f"{lang}.json"
        try:
            with open(filepath, encoding="utf-8") as f:
                _TRANSLATIONS[lang] = json.load(f)
        except FileNotFoundError:
            _TRANSLATIONS[lang] = {}

def t(text: str, lang: str) -> str:
    """
    Переводит текст на указанный язык.
    
    - Если lang == "ru" → возвращает текст как есть
    - Если перевод не найден → возвращает оригинал
    """
    if not text or lang == "ru":
        return text
    return _TRANSLATIONS.get(lang, {}).get(text, text)

def t_choices(pairs: list[tuple], lang: str) -> list[tuple]:
    """Переводит labels в списке (code, label)."""
    return [(code, t(label, lang)) for code, label in pairs]

# Загружаем при импорте модуля
load_translations()