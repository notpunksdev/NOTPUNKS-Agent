"""Language preference helpers for CLI, TUI, and gateway prompts."""

from __future__ import annotations

from typing import Any


SUPPORTED_LANGUAGES = {"en", "ru"}
DEFAULT_LANGUAGE = "en"

_LANGUAGE_INSTRUCTIONS = {
    "en": (
        "Language policy: respond in English by default. Ask interactive wizard "
        "questions in English unless the user explicitly requests another language."
    ),
    "ru": (
        "Language policy: respond only in Russian by default. Do not answer in English, "
        "do not add English translations, and do not say that English is the default. "
        "Исключение: сохраняй код, команды, имена файлов, API-поля и цитаты на языке оригинала. "
        "Все уточняющие вопросы и интерактивные мастера задавай на русском, если пользователь явно "
        "не попросит другой язык."
    ),
}


def normalize_language(value: Any) -> str:
    raw = str(value or "").strip().lower()
    aliases = {
        "english": "en",
        "eng": "en",
        "английский": "en",
        "en-us": "en",
        "en_us": "en",
        "russian": "ru",
        "rus": "ru",
        "русский": "ru",
        "ru-ru": "ru",
        "ru_ru": "ru",
    }
    return aliases.get(raw, raw) if aliases.get(raw, raw) in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE


def language_instruction(language: Any) -> str:
    return _LANGUAGE_INSTRUCTIONS[normalize_language(language)]


def language_label(language: Any) -> str:
    return {"en": "English", "ru": "Русский"}[normalize_language(language)]


def get_config_language(config: dict | None) -> str:
    agent = (config or {}).get("agent") if isinstance(config, dict) else {}
    if not isinstance(agent, dict):
        return DEFAULT_LANGUAGE
    return normalize_language(agent.get("language", DEFAULT_LANGUAGE))


def append_language_instruction(prompt: str | None, language: Any) -> str:
    instruction = language_instruction(language)
    base = (prompt or "").strip()
    for existing in _LANGUAGE_INSTRUCTIONS.values():
        base = base.replace(existing, "").strip()
    return "\n\n".join(part for part in (base, instruction) if part)
