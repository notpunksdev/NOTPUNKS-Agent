from cli import HermesCLI


def test_agent_prewarm_initializes_in_background(monkeypatch):
    cli = HermesCLI.__new__(HermesCLI)
    cli.agent = None
    cli._agent_prewarm_thread = None
    cli._active_agent_route_signature = None
    calls = []

    cli._resolve_turn_agent_config = lambda _message: {
        "model": "test-model",
        "runtime": {"provider": "custom"},
        "signature": ("test-model", "custom"),
        "request_overrides": None,
    }

    def fake_init_agent(**kwargs):
        calls.append(kwargs)
        cli.agent = object()
        return True

    cli._init_agent = fake_init_agent

    cli._start_agent_prewarm()
    cli._agent_prewarm_thread.join(timeout=3)

    assert cli.agent is not None
    assert calls == [{
        "model_override": "test-model",
        "runtime_override": {"provider": "custom"},
        "request_overrides": None,
    }]


def test_agent_prewarm_can_be_disabled(monkeypatch):
    cli = HermesCLI.__new__(HermesCLI)
    cli.agent = None
    cli._agent_prewarm_thread = None
    monkeypatch.setenv("NOTPUNKS_DISABLE_AGENT_PREWARM", "1")

    cli._start_agent_prewarm()

    assert cli._agent_prewarm_thread is None
