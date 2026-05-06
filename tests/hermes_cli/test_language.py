from hermes_cli.language import (
    append_language_instruction,
    get_config_language,
    language_instruction,
    language_label,
    normalize_language,
)


def test_normalize_language_accepts_supported_aliases():
    assert normalize_language("ru") == "ru"
    assert normalize_language("русский") == "ru"
    assert normalize_language("english") == "en"
    assert normalize_language("unknown") == "en"


def test_language_instruction_russian():
    assert "respond only in Russian" in language_instruction("ru")
    assert "не попросит другой язык" in language_instruction("ru")
    assert language_label("ru") == "Русский"


def test_get_config_language_from_agent_config():
    assert get_config_language({"agent": {"language": "ru"}}) == "ru"
    assert get_config_language({"agent": {"language": "bad"}}) == "en"


def test_append_language_instruction_preserves_custom_prompt():
    prompt = append_language_instruction("Custom prompt", "ru")
    assert "Custom prompt" in prompt
    assert "respond only in Russian" in prompt


def test_append_language_instruction_replaces_existing_language_policy():
    prompt = append_language_instruction("Custom prompt", "en")
    prompt = append_language_instruction(prompt, "ru")
    assert "Custom prompt" in prompt
    assert "respond only in Russian" in prompt
    assert "respond in English by default" not in prompt
