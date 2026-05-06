from run_agent import AIAgent


def test_cli_first_turn_greeting_skips_model_call(monkeypatch):
    agent = AIAgent(
        model="test-model",
        provider="custom",
        api_key="dummy",
        base_url="http://127.0.0.1:9/v1",
        enabled_toolsets=[],
        quiet_mode=True,
        skip_memory=True,
        skip_context_files=True,
        platform="cli",
    )

    monkeypatch.setattr(
        agent,
        "_interruptible_streaming_api_call",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("model called")),
    )
    monkeypatch.setattr(agent, "_persist_session", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(agent, "_save_trajectory", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(agent, "_cleanup_task_resources", lambda *_args, **_kwargs: None)

    result = agent.run_conversation("привет", conversation_history=[])

    assert result["api_calls"] == 0
    assert result["completed"] is True
    assert "NOTPUNKS Agent" in result["final_response"]


def test_gateway_first_turn_greeting_skips_model_call(monkeypatch):
    agent = AIAgent(
        model="test-model",
        provider="custom",
        api_key="dummy",
        base_url="http://127.0.0.1:9/v1",
        enabled_toolsets=[],
        quiet_mode=True,
        skip_memory=True,
        skip_context_files=True,
        platform="telegram",
    )

    monkeypatch.setattr(
        agent,
        "_interruptible_streaming_api_call",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("model called")),
    )
    monkeypatch.setattr(agent, "_persist_session", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(agent, "_save_trajectory", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(agent, "_cleanup_task_resources", lambda *_args, **_kwargs: None)

    result = agent.run_conversation("hello", conversation_history=[])

    assert result["api_calls"] == 0
