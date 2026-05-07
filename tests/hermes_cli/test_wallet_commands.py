from unittest.mock import Mock
from io import BytesIO
import webbrowser

from agent.wallet import callback_server
from agent.wallet import context as wallet_context
from agent.wallet.models import WalletContext
from hermes_cli import config as hermes_config
from hermes_cli import wallet_commands


def test_wallet_connect_web_preserves_blocking_flag(monkeypatch):
    connect_web = Mock(return_value=0)
    monkeypatch.setattr(wallet_commands, "_wallet_connect_web", connect_web)

    assert wallet_commands._wallet_connect(console="console", web=True, blocking=False) == 0

    connect_web.assert_called_once_with(console="console", blocking=False)


def test_cloudflared_path_uses_hermes_home(tmp_path, monkeypatch):
    hermes_home = tmp_path / "profile-home"
    cf_path = hermes_home / "bin" / "cloudflared"
    cf_path.parent.mkdir(parents=True)
    cf_path.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))

    path = wallet_commands._ensure_cloudflared()

    assert path == str(cf_path)


def test_wallet_connect_web_uses_skilzzz_hosted_page(monkeypatch):
    printed = []
    opened = []

    class Console:
        def print(self, *args, **kwargs):
            printed.append(" ".join(str(arg) for arg in args))

    monkeypatch.setattr(wallet_commands, "_poll_hosted_wallet_session", lambda session_id, timeout=300.0: None)
    monkeypatch.setattr(wallet_commands, "load_config", lambda: {"wallet": {"network": "mainnet"}})
    monkeypatch.setattr(wallet_commands.WalletConnector, "get_connected_wallet", lambda self: None)
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url))

    assert wallet_commands._wallet_connect_web(console=Console(), blocking=True, force=True) == 1

    assert opened
    assert opened[0].startswith("https://skilzzz.com/wallet-connect?")
    assert "session_id=" in opened[0]
    assert "payload=notpunks-" in opened[0]
    assert "callback_url=" not in opened[0]


def test_connect_page_posts_restored_wallet_state():
    html = callback_server._CONNECT_HTML

    assert "connectionRestored.then" in html
    assert "sendWallet(tonConnectUI.wallet)" in html
    assert "let sent = false" in html


def test_callback_options_returns_cors_headers():
    class Handler(callback_server._CallbackHandler):
        def __init__(self):
            self.headers = {
                "Access-Control-Request-Headers": "content-type,x-test",
            }
            self.responses = []
            self.wfile = BytesIO()

        def send_response(self, code, message=None):
            self.responses.append(("status", code))

        def send_header(self, key, value):
            self.responses.append((key, value))

        def end_headers(self):
            self.responses.append(("end", None))

    handler = Handler()
    handler.do_OPTIONS()

    assert ("status", 204) in handler.responses
    assert ("Access-Control-Allow-Origin", "*") in handler.responses
    assert ("Access-Control-Allow-Methods", "GET, POST, OPTIONS") in handler.responses
    assert ("Access-Control-Allow-Headers", "content-type,x-test") in handler.responses


def test_startup_wallet_check_prompts_connect_when_disconnected(monkeypatch):
    connect_web = Mock(return_value=0)
    monkeypatch.setattr(wallet_commands, "_wallet_connect_web", connect_web)
    monkeypatch.setattr(wallet_context, "load_config", lambda: {"wallet": {"address": None}})

    assert wallet_context.check_wallet_on_startup(console=None, prompt_connect=True) is None

    connect_web.assert_called_once_with(console=None, blocking=False, force=True)


def test_startup_wallet_check_does_not_prompt_connect_when_connected(monkeypatch):
    connect_web = Mock(return_value=0)
    monkeypatch.setattr(wallet_commands, "_wallet_connect_web", connect_web)
    monkeypatch.setattr(wallet_context, "load_config", lambda: {
        "wallet": {
            "address": "EQwallet",
            "verified": True,
            "network": "mainnet",
            "auto_scan_on_start": False,
        }
    })

    ctx = wallet_context.check_wallet_on_startup(console=None, prompt_connect=True)

    assert ctx is not None
    assert ctx.wallet_address == "EQwallet"
    connect_web.assert_not_called()


def test_startup_wallet_check_can_reset_saved_wallet(monkeypatch):
    saved = {}
    storage_cleared = Mock()
    connect_web = Mock(return_value=0)
    config = {
        "wallet": {
            "address": "EQwallet",
            "verified": True,
            "network": "mainnet",
            "reset_on_start": True,
        }
    }
    monkeypatch.setattr(wallet_commands, "_wallet_connect_web", connect_web)
    monkeypatch.setattr(wallet_context, "load_config", lambda: config)
    monkeypatch.setattr(wallet_context.FileStorage, "clear", storage_cleared)
    monkeypatch.setattr(hermes_config, "save_config", lambda value: saved.update(value))

    ctx = wallet_context.check_wallet_on_startup(console=None, prompt_connect=True, reset_on_start=True)

    assert ctx is None
    assert config["wallet"]["address"] is None
    assert config["wallet"]["verified"] is False
    assert saved["wallet"]["address"] is None
    storage_cleared.assert_called_once()
    connect_web.assert_called_once()


def test_startup_wallet_check_resets_tonconnect_storage_without_saved_address(monkeypatch):
    saved = {}
    storage_cleared = Mock()
    connect_web = Mock(return_value=0)
    config = {"wallet": {"address": None, "network": "mainnet"}}

    class Storage:
        _path = Mock()

        def clear(self):
            storage_cleared()

    Storage._path.exists.return_value = True

    monkeypatch.setattr(wallet_commands, "_wallet_connect_web", connect_web)
    monkeypatch.setattr(wallet_context, "load_config", lambda: config)
    monkeypatch.setattr(wallet_context, "FileStorage", Storage)
    monkeypatch.setattr(hermes_config, "save_config", lambda value: saved.update(value))

    ctx = wallet_context.check_wallet_on_startup(console=None, prompt_connect=True, reset_on_start=True)

    assert ctx is None
    assert config["wallet"]["address"] is None
    assert saved["wallet"]["address"] is None
    storage_cleared.assert_called_once()
    connect_web.assert_called_once()


def test_startup_wallet_check_can_force_refresh_nfts(monkeypatch):
    config = {
        "wallet": {
            "address": "EQwallet",
            "verified": True,
            "network": "mainnet",
        }
    }
    ctx = WalletContext(wallet_address="EQwallet", verified=True, network="mainnet", active_modes=[], nfts=[])
    build = Mock(return_value=ctx)
    monkeypatch.setattr(wallet_context, "load_config", lambda: config)
    monkeypatch.setattr(wallet_context, "build_not_punks_holder_context", build)
    monkeypatch.setattr(wallet_context, "wallet_context_has_not_punks", lambda value: True)

    result = wallet_context.check_wallet_on_startup(
        console=None,
        prompt_connect=False,
        require_not_punks=True,
        force_refresh=True,
    )

    assert result is ctx
    build.assert_called_once_with(config, force_refresh=True)


def test_startup_wallet_check_reports_not_punks_count(monkeypatch):
    messages = []
    config = {
        "wallet": {
            "address": "EQwallet",
            "verified": True,
            "network": "mainnet",
        }
    }
    ctx = WalletContext(wallet_address="EQwallet", verified=True, network="mainnet", active_modes=[], nfts=[object(), object()])

    monkeypatch.setattr(wallet_context, "load_config", lambda: config)
    monkeypatch.setattr(wallet_context, "build_not_punks_holder_context", Mock(return_value=ctx))
    monkeypatch.setattr(wallet_context, "wallet_context_not_punks_count", lambda value: 2)

    class Console:
        def print(self, message):
            messages.append(str(message))

    result = wallet_context.check_wallet_on_startup(
        console=Console(),
        prompt_connect=False,
        require_not_punks=True,
    )

    assert result is ctx
    assert any("2 NOT Punks NFT(s)" in message for message in messages)


def test_startup_wallet_check_quiet_suppresses_success_messages(monkeypatch):
    messages = []
    config = {
        "wallet": {
            "address": "EQwallet",
            "verified": True,
            "network": "mainnet",
        }
    }
    ctx = WalletContext(wallet_address="EQwallet", verified=True, network="mainnet", active_modes=[], nfts=[object()])

    monkeypatch.setattr(wallet_context, "load_config", lambda: config)
    monkeypatch.setattr(wallet_context, "build_not_punks_holder_context", Mock(return_value=ctx))
    monkeypatch.setattr(wallet_context, "wallet_context_not_punks_count", lambda value: 1)

    class Console:
        def print(self, message):
            messages.append(str(message))

    result = wallet_context.check_wallet_on_startup(
        console=Console(),
        prompt_connect=False,
        require_not_punks=True,
        quiet=True,
    )

    assert result is ctx
    assert messages == []


def test_startup_wallet_check_explains_skill_nfts_do_not_unlock_agent(monkeypatch):
    messages = []
    config = {
        "wallet": {
            "address": "EQwallet",
            "verified": True,
            "network": "mainnet",
        }
    }
    ctx = WalletContext(wallet_address="EQwallet", verified=True, network="mainnet", active_modes=[], nfts=[])

    monkeypatch.setattr(wallet_context, "load_config", lambda: config)
    monkeypatch.setattr(wallet_context, "build_not_punks_holder_context", Mock(return_value=ctx))
    monkeypatch.setattr(wallet_context, "wallet_context_not_punks_count", lambda value: 0)

    class Console:
        def print(self, message):
            messages.append(str(message))

    result = wallet_context.check_wallet_on_startup(
        console=Console(),
        prompt_connect=False,
        require_not_punks=True,
    )

    assert result is ctx
    assert any("requires at least one NOT Punks NFT" in message for message in messages)
    assert any("Skill NFT licenses do not unlock" in message for message in messages)


def test_startup_wallet_check_does_not_scan_nfts(monkeypatch):
    messages = []

    class Console:
        def print(self, message):
            messages.append(str(message))

    monkeypatch.setattr(wallet_context, "load_config", lambda: {
        "wallet": {
            "address": "EQwallet123456",
            "verified": True,
            "network": "mainnet",
            "auto_scan_on_start": True,
        }
    })

    scan = Mock(side_effect=RuntimeError("scanner should not run on startup"))

    monkeypatch.setattr(wallet_context, "build_wallet_context", scan)

    ctx = wallet_context.check_wallet_on_startup(console=Console(), prompt_connect=True)
    assert ctx is not None
    assert ctx.wallet_address == "EQwallet123456"
    scan.assert_not_called()
    assert any("TON Wallet detected" in message for message in messages)
    assert any("/wallet connect" in message for message in messages)
