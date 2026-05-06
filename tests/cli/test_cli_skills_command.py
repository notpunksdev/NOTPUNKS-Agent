from unittest.mock import MagicMock, patch

from cli import HermesCLI


def _make_cli():
    cli_obj = HermesCLI.__new__(HermesCLI)
    cli_obj._pending_input = MagicMock()
    cli_obj.language = "en"
    return cli_obj


def test_bare_skills_create_starts_agent_driven_wizard():
    cli_obj = _make_cli()

    with patch("hermes_cli.skills_hub.handle_skills_slash") as slash:
        cli_obj._handle_skills_command("/skills create")

    slash.assert_not_called()
    queued = cli_obj._pending_input.put.call_args.args[0]
    assert "interactive NOTPUNKS skill creation wizard" in queued
    assert "skill management tool" in queued


def test_named_skills_create_still_uses_skills_dispatcher():
    cli_obj = _make_cli()

    with patch("hermes_cli.skills_hub.handle_skills_slash") as slash:
        cli_obj._handle_skills_command("/skills create wallet-debug")

    slash.assert_called_once()
    cli_obj._pending_input.put.assert_not_called()


def test_bare_skills_create_uses_russian_wizard_when_language_ru():
    cli_obj = _make_cli()
    cli_obj.language = "ru"

    cli_obj._handle_skills_command("/skills create")

    queued = cli_obj._pending_input.put.call_args.args[0]
    assert "Задавай вопросы на русском языке" in queued
