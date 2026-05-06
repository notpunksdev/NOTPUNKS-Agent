import http.client
import hashlib
import json
import threading
from types import SimpleNamespace


def _request(
    port: int,
    method: str,
    path: str,
    *,
    body=None,
    origin="https://app.notpunks.com",
    headers=None,
    bridge_token=True,
):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = dict(headers or {})
    if origin is not None:
        headers["Origin"] = origin
    if body is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(body)
    if method in {"POST", "DELETE"} and bridge_token is True:
        token_status, _token_headers, token_payload = _request(
            port,
            "GET",
            "/api/skills/marketplace/status",
            origin=origin,
            bridge_token=False,
        )
        assert token_status == 200
        headers["X-NOTPUNKS-Bridge-Token"] = token_payload["bridgeToken"]
    elif isinstance(bridge_token, str):
        headers["X-NOTPUNKS-Bridge-Token"] = bridge_token
    conn.request(method, path, body=body, headers=headers)
    response = conn.getresponse()
    raw = response.read()
    conn.close()
    payload = json.loads(raw.decode("utf-8")) if raw else {}
    return response.status, dict(response.headers), payload


def _install_signature(name="alpha", wallet="EQwallet", bundle_hash="", metadata_url="", mode="install", force=False):
    payload = {
        "protocol": "notpunks-skill-install",
        "version": 1,
        "action": "uninstall" if mode == "uninstall" else "install",
        "skillId": name,
        "walletAddress": wallet,
        "bundleHash": bundle_hash,
        "metadataUrl": metadata_url,
        "mode": mode,
        "force": force,
        "issuedAt": 4102444800,
    }
    return {
        "result": {
            "signature": "test-signature",
            "address": wallet,
            "timestamp": 4102444800,
            "domain": "skilzzz.com",
            "payload": {"type": "text", "text": json.dumps(payload)},
        }
    }


def _opaque_install_signature(name="alpha", wallet="EQwallet", bundle_hash="", metadata_url="", mode="install", force=False):
    intent = {
        "protocol": "notpunks-skill-install",
        "version": 1,
        "action": "uninstall" if mode == "uninstall" else "install",
        "skillId": name,
        "walletAddress": wallet,
        "bundleHash": bundle_hash,
        "metadataUrl": metadata_url,
        "mode": mode,
        "force": force,
        "issuedAt": 4102444800,
    }
    canonical = json.dumps(intent, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    challenge = f"notpunks-install:v1:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"
    return {
        "result": {
            "signature": "test-signature",
            "address": wallet,
            "timestamp": 4102444800,
            "domain": "skilzzz.com",
            "payload": challenge,
        },
        "intent": intent,
        "challenge": challenge,
    }


def test_local_bridge_status(monkeypatch):
    import hermes_cli.skill_marketplace as skill_marketplace
    from hermes_cli.local_bridge import start_local_bridge

    monkeypatch.setattr(skill_marketplace, "list_installed_marketplace_skills", lambda name="": [{
        "skillId": name or "alpha",
        "status": "installed",
        "path": "alpha",
    }])
    handle = start_local_bridge({"marketplace": {"local_bridge": {"enabled": True, "port": 0}}})
    assert handle is not None
    try:
        status, headers, payload = _request(handle.port, "GET", "/api/skills/marketplace/status?name=alpha")
        assert status == 200
        assert headers["Access-Control-Allow-Origin"] == "https://app.notpunks.com"
        assert payload["skills"][0]["skillId"] == "alpha"
        assert payload["bridgeTokenHeader"] == "X-NOTPUNKS-Bridge-Token"
        assert len(payload["bridgeToken"]) >= 32
    finally:
        handle.stop()


def test_local_bridge_allows_agent_origin_and_root_health(monkeypatch):
    from hermes_cli.local_bridge import start_local_bridge

    handle = start_local_bridge({"marketplace": {"local_bridge": {"enabled": True, "port": 0}}})
    assert handle is not None
    try:
        status, headers, payload = _request(
            handle.port,
            "GET",
            "/",
            origin="https://agent.notpunks.com",
        )
        assert status == 200
        assert headers["Access-Control-Allow-Origin"] == "https://agent.notpunks.com"
        assert payload["ok"] is True
        assert payload["service"] == "NOTPUNKS local marketplace bridge"
        assert payload["bridgeTokenHeader"] == "X-NOTPUNKS-Bridge-Token"
    finally:
        handle.stop()


def test_local_bridge_allows_skills_origin(monkeypatch):
    from hermes_cli.local_bridge import start_local_bridge

    handle = start_local_bridge({"marketplace": {"local_bridge": {"enabled": True, "port": 0}}})
    assert handle is not None
    try:
        status, headers, payload = _request(
            handle.port,
            "GET",
            "/",
            origin="https://skilzzz.com",
        )
        assert status == 200
        assert headers["Access-Control-Allow-Origin"] == "https://skilzzz.com"
        assert payload["installIntent"]["approvalRequired"] is True
    finally:
        handle.stop()


def test_local_bridge_rejects_unknown_origin(monkeypatch):
    from hermes_cli.local_bridge import start_local_bridge

    handle = start_local_bridge({"marketplace": {"local_bridge": {"enabled": True, "port": 0}}})
    assert handle is not None
    try:
        status, _headers, payload = _request(
            handle.port,
            "GET",
            "/api/skills/marketplace/status",
            origin="https://example.com",
        )
        assert status == 403
        assert payload["error"] == "Origin not allowed"
    finally:
        handle.stop()


def test_local_bridge_rejects_mutation_without_pairing_token(monkeypatch):
    from hermes_cli.local_bridge import start_local_bridge

    handle = start_local_bridge({"marketplace": {"local_bridge": {"enabled": True, "port": 0}}})
    assert handle is not None
    try:
        status, _headers, payload = _request(
            handle.port,
            "POST",
            "/api/skills/marketplace/install",
            body={"name": "alpha"},
            bridge_token=False,
        )
        assert status == 401
        assert payload["error"] == "Bridge pairing token is required"
    finally:
        handle.stop()


def test_local_bridge_install(monkeypatch):
    import agent.wallet.context as wallet_context
    import hermes_cli.skill_marketplace as skill_marketplace
    from hermes_cli.local_bridge import approve_install_request, start_local_bridge

    monkeypatch.setattr(wallet_context, "build_wallet_context", lambda: SimpleNamespace(wallet_address="EQwallet", skill_licenses={"alpha": {}}))
    monkeypatch.setattr(skill_marketplace, "fetch_remote_listing", lambda name: {
        "skillId": name,
        "access": {"policy": "license"},
        "bundleHash": "sha256:test",
    })
    monkeypatch.setattr(skill_marketplace, "check_skill_access", lambda ctx, listing: (True, "ok"))
    monkeypatch.setattr(skill_marketplace, "install_marketplace_skill", lambda name, **kwargs: {
        "name": name,
        "path": name,
        "bundle_hash": "sha256:test",
        "scan_verdict": "safe",
    })
    monkeypatch.setattr(skill_marketplace, "list_installed_marketplace_skills", lambda name="": [{
        "skillId": name,
        "status": "installed",
        "path": name,
        "bundleHash": "sha256:test",
        "scanVerdict": "safe",
    }])

    handle = start_local_bridge({"marketplace": {"local_bridge": {"enabled": True, "port": 0}}})
    assert handle is not None
    try:
        status, _headers, payload = _request(
            handle.port,
            "POST",
            "/api/skills/marketplace/install",
            body={
                "name": "alpha",
                "walletAddress": "EQwallet",
                "metadataUrl": "https://app.notpunks.com/api/skills/marketplace/alpha/metadata",
                "expectedBundleHash": "sha256:test",
                "mode": "buy_install",
            },
        )
        assert status == 202
        assert payload["approval_required"] is True
        assert payload["mode"] == "buy_install"
        assert payload["walletAddress"] == "EQwallet"
        assert payload["approveCommand"].startswith("/marketplace bridge approve ")

        approve_install_request(payload["requestId"])
        status, _headers, payload = _request(
            handle.port,
            "POST",
            "/api/skills/marketplace/install",
            body={
                "name": "alpha",
                "requestId": payload["requestId"],
                "walletAddress": "EQwallet",
                "metadataUrl": "https://app.notpunks.com/api/skills/marketplace/alpha/metadata",
                "expectedBundleHash": "sha256:test",
                "mode": "buy_install",
            },
        )
        assert status == 200
        assert payload["installed"]["name"] == "alpha"
        assert payload["status"]["status"] == "installed"
        assert payload["mode"] == "buy_install"
    finally:
        handle.stop()


def test_local_bridge_install_rejects_wallet_mismatch(monkeypatch):
    import agent.wallet.context as wallet_context
    import hermes_cli.skill_marketplace as skill_marketplace
    from hermes_cli.local_bridge import approve_install_request, start_local_bridge

    monkeypatch.setattr(wallet_context, "build_wallet_context", lambda: SimpleNamespace(wallet_address="EQlocal", skill_licenses={"alpha": {}}))
    monkeypatch.setattr(skill_marketplace, "fetch_remote_listing", lambda name: {"skillId": name, "access": {"policy": "license"}})

    handle = start_local_bridge({"marketplace": {"local_bridge": {"enabled": True, "port": 0}}})
    assert handle is not None
    try:
        status, _headers, payload = _request(
            handle.port,
            "POST",
            "/api/skills/marketplace/install",
            body={"name": "alpha", "walletAddress": "EQmarket"},
        )
        assert status == 202
        approve_install_request(payload["requestId"])
        status, _headers, payload = _request(
            handle.port,
            "POST",
            "/api/skills/marketplace/install",
            body={"name": "alpha", "walletAddress": "EQmarket", "requestId": payload["requestId"]},
        )
        assert status == 409
        assert "does not match" in payload["error"]
    finally:
        handle.stop()


def test_local_bridge_signed_install_uses_marketplace_wallet_without_local_wallet(monkeypatch):
    import agent.wallet.context as wallet_context
    import hermes_cli.skill_marketplace as skill_marketplace
    from hermes_cli.local_bridge import approve_install_request, start_local_bridge

    monkeypatch.setattr(wallet_context, "build_wallet_context", lambda: None)
    monkeypatch.setattr(skill_marketplace, "fetch_remote_listing", lambda name: {
        "skillId": name,
        "access": {"policy": "license"},
        "bundleHash": "sha256:test",
    })
    monkeypatch.setattr(skill_marketplace, "install_marketplace_skill", lambda name, **kwargs: {
        "name": name,
        "path": name,
        "bundle_hash": "sha256:test",
        "scan_verdict": "safe",
    })
    monkeypatch.setattr(skill_marketplace, "list_installed_marketplace_skills", lambda name="": [{
        "skillId": name,
        "status": "installed",
        "path": name,
        "bundleHash": "sha256:test",
        "scanVerdict": "safe",
    }])

    body = {
        "name": "alpha",
        "walletAddress": "EQwallet",
        "metadataUrl": "https://app.notpunks.com/api/skills/marketplace/alpha/metadata",
        "expectedBundleHash": "sha256:test",
        "mode": "install",
        "requireWalletSignature": True,
        "walletSignature": _opaque_install_signature(
            bundle_hash="sha256:test",
            metadata_url="https://app.notpunks.com/api/skills/marketplace/alpha/metadata",
        ),
    }
    seen_results = []
    result_event = threading.Event()

    def on_install_request(event):
        if event.get("event") == "install_result":
            seen_results.append(event)
            result_event.set()

    handle = start_local_bridge(
        {"marketplace": {"local_bridge": {"enabled": True, "port": 0}}},
        on_install_request=on_install_request,
    )
    assert handle is not None
    try:
        status, _headers, payload = _request(
            handle.port,
            "POST",
            "/api/skills/marketplace/install",
            body=body,
        )
        assert status == 202
        approve_install_request(payload["requestId"])
        body["requestId"] = payload["requestId"]

        status, _headers, payload = _request(
            handle.port,
            "POST",
            "/api/skills/marketplace/install",
            body=body,
        )
        assert status == 200
        assert payload["installed"]["name"] == "alpha"
        assert result_event.wait(2)
        assert seen_results[0]["ok"] is True
        assert seen_results[0]["name"] == "alpha"

        from hermes_cli.config import load_config

        assert load_config()["wallet"]["address"] == "EQwallet"
        assert load_config()["wallet"]["verified"] is True
    finally:
        handle.stop()


def test_local_bridge_create_draft(monkeypatch, tmp_path):
    import agent.wallet.context as wallet_context
    import hermes_cli.skill_marketplace as skill_marketplace
    import tools.skill_manager_tool as skill_manager
    from hermes_cli.local_bridge import start_local_bridge

    skill_dir = tmp_path / "wallet-debug"
    monkeypatch.setattr(wallet_context, "build_wallet_context", lambda: SimpleNamespace(
        wallet_address="EQcreator",
        can_create_skills=True,
        access_roles=["creator"],
        matching_pair_numbers=[7],
    ))

    def fake_create(name, content, category=None):
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
        return {"success": True, "path": name, "category": category}

    monkeypatch.setattr(skill_manager, "_create_skill", fake_create)
    monkeypatch.setattr(skill_marketplace, "find_local_skill", lambda name: skill_dir if name == "wallet-debug" else None)
    monkeypatch.setattr(skill_marketplace, "build_listing", lambda path, creator_wallet="", price_ton=0, **kwargs: {
        "skill_id": "wallet-debug",
        "name": "wallet-debug",
        "path": str(path),
        "creator_wallet": creator_wallet,
        "price_ton": price_ton,
        "mint_model": kwargs.get("mint_model", "open_edition"),
        "mint_supply_max": kwargs.get("mint_supply_max"),
        "bundle": {"bundle_hash": "sha256:test"},
        "nft_metadata": {
            "url": "https://app.notpunks.com/api/skills/marketplace/wallet-debug/metadata",
            "mint": {"model": kwargs.get("mint_model", "open_edition"), "supply": {"max": kwargs.get("mint_supply_max")}},
        },
    })
    monkeypatch.setattr(skill_marketplace, "save_listing", lambda listing: tmp_path / "wallet-debug.json")
    monkeypatch.setattr(skill_marketplace, "list_installed_marketplace_skills", lambda name="": [])

    handle = start_local_bridge({"marketplace": {"local_bridge": {"enabled": True, "port": 0}}})
    assert handle is not None
    try:
        status, _headers, payload = _request(
            handle.port,
            "POST",
            "/api/skills/marketplace/create-draft",
            body={
                "name": "Wallet Debug",
                "description": "Debug wallet connections",
                "instructions": "## Steps\n1. Check wallet status.",
                "priceTon": 1,
                "mintModel": "limited_edition",
                "mintSupplyMax": 25,
                "collectionName": "Wallet Debug Collection",
            },
            origin="https://skilzzz.com",
        )
        assert status == 200
        assert payload["ok"] is True
        assert payload["installed"] is True
        assert payload["listing"]["creator_wallet"] == "EQcreator"
        assert payload["listing"]["price_ton"] == 1
        assert payload["listing"]["mint_model"] == "limited_edition"
        assert payload["listing"]["mint_supply_max"] == 25
        assert payload["listing"]["nft_metadata"]["mint"]["model"] == "limited_edition"
        assert payload["commands"]["publishRemote"] == "/skills publish wallet-debug --price 1.0 --remote"
        assert "name: wallet-debug" in (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    finally:
        handle.stop()


def test_local_bridge_create_draft_requires_beta_access(monkeypatch):
    import agent.wallet.context as wallet_context
    from hermes_cli.local_bridge import start_local_bridge

    monkeypatch.setattr(wallet_context, "build_wallet_context", lambda: SimpleNamespace(
        wallet_address="EQwallet",
        can_create_skills=False,
        access_roles=["holder"],
        matching_pair_numbers=[],
    ))

    handle = start_local_bridge({"marketplace": {"local_bridge": {"enabled": True, "port": 0}}})
    assert handle is not None
    try:
        status, _headers, payload = _request(
            handle.port,
            "POST",
            "/api/skills/marketplace/create-draft",
            body={"name": "alpha"},
            origin="https://skilzzz.com",
        )
        assert status == 403
        assert payload["ok"] is False
        assert "Beta access is locked" in payload["error"]
    finally:
        handle.stop()


def test_local_bridge_skill_wizard_chat_uses_local_agent(monkeypatch):
    import agent.wallet.context as wallet_context
    import hermes_cli.local_bridge as local_bridge
    from hermes_cli.local_bridge import start_local_bridge

    prompts = []
    monkeypatch.setattr(wallet_context, "build_wallet_context", lambda: SimpleNamespace(
        wallet_address="EQcreator",
        can_create_skills=True,
        access_roles=["creator"],
        matching_pair_numbers=[7],
    ))

    def fake_run_agent(prompt):
        prompts.append(prompt)
        return json.dumps({
            "reply": "Какой результат должен проверять skill?",
            "done": False,
            "draft": {
                "name": "wallet-debug",
                "category": "debugging",
                "description": "Debug wallet connections",
                "instructions": "## When to Use\nUse for wallet issues.",
                "priceTon": 1,
            },
            "missing": ["verification"],
        })

    monkeypatch.setattr(local_bridge, "_run_skill_wizard_agent", fake_run_agent)

    handle = start_local_bridge({"marketplace": {"local_bridge": {"enabled": True, "port": 0}}})
    assert handle is not None
    try:
        status, _headers, payload = _request(
            handle.port,
            "POST",
            "/api/skills/marketplace/wizard-chat",
            body={
                "action": "chat",
                "language": "ru",
                "messages": [{"role": "user", "content": "Хочу skill для проверки кошелька"}],
                "draft": {"priceTon": 1},
            },
            origin="https://skilzzz.com",
        )
        assert status == 200
        assert payload["ok"] is True
        assert payload["reply"] == "Какой результат должен проверять skill?"
        assert payload["draft"]["name"] == "wallet-debug"
        assert payload["draft"]["category"] == "debugging"
        assert payload["draft"]["priceTon"] == 1
        assert payload["missing"] == ["verification"]
        assert "local NOTPUNKS Agent" in prompts[0]
        assert "Action: chat" in prompts[0]
        assert "Хочу skill" in prompts[0]
    finally:
        handle.stop()


def test_local_bridge_skill_wizard_chat_falls_back_when_agent_times_out(monkeypatch):
    import agent.wallet.context as wallet_context
    import hermes_cli.local_bridge as local_bridge
    from hermes_cli.local_bridge import start_local_bridge

    monkeypatch.setattr(wallet_context, "build_wallet_context", lambda: SimpleNamespace(
        wallet_address="EQcreator",
        can_create_skills=True,
        access_roles=["creator"],
        matching_pair_numbers=[7],
    ))

    def fake_run_agent(_prompt):
        raise local_bridge.SkillWizardAgentTimeout("Local agent did not answer within 75s")

    monkeypatch.setattr(local_bridge, "_run_skill_wizard_agent", fake_run_agent)

    handle = start_local_bridge({"marketplace": {"local_bridge": {"enabled": True, "port": 0}}})
    assert handle is not None
    try:
        status, _headers, payload = _request(
            handle.port,
            "POST",
            "/api/skills/marketplace/wizard-chat",
            body={
                "action": "chat",
                "language": "ru",
                "messages": [{"role": "user", "content": "торговля на полимаркете"}],
            },
            origin="https://skilzzz.com",
        )
        assert status == 200
        assert payload["ok"] is True
        assert payload["done"] is False
        assert payload["agentFallback"] is True
        assert "продолжаем диалог здесь" in payload["reply"]
        assert "полимаркете" in payload["reply"]
        assert payload["walletAddress"] == "EQcreator"
    finally:
        handle.stop()


def test_local_bridge_skill_wizard_chat_falls_back_on_empty_agent_reply(monkeypatch):
    import agent.wallet.context as wallet_context
    import hermes_cli.local_bridge as local_bridge
    from hermes_cli.local_bridge import start_local_bridge

    monkeypatch.setattr(wallet_context, "build_wallet_context", lambda: SimpleNamespace(
        wallet_address="EQcreator",
        can_create_skills=True,
        access_roles=["creator"],
        matching_pair_numbers=[7],
    ))
    monkeypatch.setattr(local_bridge, "_run_skill_wizard_agent", lambda _prompt: "")

    handle = start_local_bridge({"marketplace": {"local_bridge": {"enabled": True, "port": 0}}})
    assert handle is not None
    try:
        status, _headers, payload = _request(
            handle.port,
            "POST",
            "/api/skills/marketplace/wizard-chat",
            body={
                "action": "chat",
                "language": "en",
                "messages": [{"role": "user", "content": "polymarket trading"}],
            },
            origin="https://skilzzz.com",
        )
        assert status == 200
        assert payload["ok"] is True
        assert payload["done"] is False
        assert payload["agentFallback"] is True
        assert "continue in the interface" in payload["reply"]
    finally:
        handle.stop()


def test_local_bridge_skill_wizard_generate_instruction(monkeypatch):
    import agent.wallet.context as wallet_context
    import hermes_cli.local_bridge as local_bridge
    from hermes_cli.local_bridge import start_local_bridge

    prompts = []
    monkeypatch.setattr(wallet_context, "build_wallet_context", lambda: SimpleNamespace(
        wallet_address="EQcreator",
        can_create_skills=True,
        access_roles=["creator"],
        matching_pair_numbers=[7],
    ))

    def fake_run_agent(prompt):
        prompts.append(prompt)
        return json.dumps({
            "reply": "Инструкция сформирована. Проверь поля и создай draft.",
            "done": True,
            "draft": {
                "name": "wallet-debug",
                "category": "debugging",
                "description": "Debug wallet connections",
                "instructions": "## When to Use\nUse for wallet issues.\n\n## Verification\nConfirm wallet status.",
                "priceTon": 1,
            },
            "missing": [],
        })

    monkeypatch.setattr(local_bridge, "_run_skill_wizard_agent", fake_run_agent)

    handle = start_local_bridge({"marketplace": {"local_bridge": {"enabled": True, "port": 0}}})
    assert handle is not None
    try:
        status, _headers, payload = _request(
            handle.port,
            "POST",
            "/api/skills/marketplace/wizard-chat",
            body={
                "action": "generate",
                "language": "ru",
                "messages": [
                    {"role": "user", "content": "Хочу skill для проверки кошелька"},
                    {"role": "assistant", "content": "Какие проверки нужны?"},
                    {"role": "user", "content": "Проверить статус и подсказать ошибку."},
                ],
                "draft": {"name": "wallet-debug", "priceTon": 1},
            },
            origin="https://skilzzz.com",
        )
        assert status == 200
        assert payload["ok"] is True
        assert payload["done"] is True
        assert "Verification" in payload["draft"]["instructions"]
        assert "Action: generate" in prompts[0]
    finally:
        handle.stop()


def test_local_bridge_skill_wizard_chat_requires_beta_access(monkeypatch):
    import agent.wallet.context as wallet_context
    from hermes_cli.local_bridge import start_local_bridge

    monkeypatch.setattr(wallet_context, "build_wallet_context", lambda: SimpleNamespace(
        wallet_address="EQwallet",
        can_create_skills=False,
        access_roles=["holder"],
        matching_pair_numbers=[],
    ))

    handle = start_local_bridge({"marketplace": {"local_bridge": {"enabled": True, "port": 0}}})
    assert handle is not None
    try:
        status, _headers, payload = _request(
            handle.port,
            "POST",
            "/api/skills/marketplace/wizard-chat",
            body={"messages": [{"role": "user", "content": "create"}]},
            origin="https://skilzzz.com",
        )
        assert status == 403
        assert payload["ok"] is False
        assert "Beta access is locked" in payload["error"]
    finally:
        handle.stop()


def test_local_bridge_publish_draft_local(monkeypatch, tmp_path):
    import agent.wallet.context as wallet_context
    import hermes_cli.skill_marketplace as skill_marketplace
    from hermes_cli.local_bridge import start_local_bridge

    skill_dir = tmp_path / "wallet-debug"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: wallet-debug\ndescription: test\n---\n\n# Test\n", encoding="utf-8")
    monkeypatch.setattr(wallet_context, "build_wallet_context", lambda: SimpleNamespace(
        wallet_address="EQcreator",
        can_create_skills=True,
        can_publish_skills=True,
        access_roles=["creator"],
        matching_pair_numbers=[7],
    ))
    monkeypatch.setattr(skill_marketplace, "find_local_skill", lambda name: skill_dir if name == "wallet-debug" else None)
    monkeypatch.setattr(skill_marketplace, "build_listing", lambda path, creator_wallet="", price_ton=0, **kwargs: {
        "skill_id": "wallet-debug",
        "name": "wallet-debug",
        "path": str(path),
        "creator_wallet": creator_wallet,
        "price_ton": price_ton,
        "mint_model": kwargs.get("mint_model", "open_edition"),
        "bundle": {"bundle_hash": "sha256:test"},
        "nft_metadata": {"url": "https://app.notpunks.com/api/skills/marketplace/wallet-debug/metadata"},
    })
    monkeypatch.setattr(skill_marketplace, "save_listing", lambda listing: tmp_path / "wallet-debug.json")

    handle = start_local_bridge({"marketplace": {"local_bridge": {"enabled": True, "port": 0}}})
    assert handle is not None
    try:
        status, _headers, payload = _request(
            handle.port,
            "POST",
            "/api/skills/marketplace/publish-draft",
            body={"name": "wallet-debug", "priceTon": 2, "remote": False},
            origin="https://skilzzz.com",
        )
        assert status == 200
        assert payload["ok"] is True
        assert payload["remote"] is False
        assert payload["listing"]["creator_wallet"] == "EQcreator"
        assert payload["listing"]["price_ton"] == 2
    finally:
        handle.stop()


def test_local_bridge_install_pending_request_not_approved(monkeypatch):
    from hermes_cli.local_bridge import start_local_bridge

    handle = start_local_bridge({"marketplace": {"local_bridge": {"enabled": True, "port": 0}}})
    assert handle is not None
    try:
        status, _headers, payload = _request(
            handle.port,
            "POST",
            "/api/skills/marketplace/install",
            body={"name": "alpha"},
        )
        assert status == 202
        status, _headers, retry_payload = _request(
            handle.port,
            "POST",
            "/api/skills/marketplace/install",
            body={"name": "alpha", "requestId": payload["requestId"]},
        )
        assert status == 202
        assert retry_payload["approval_required"] is True
    finally:
        handle.stop()


def test_local_bridge_install_request_status(monkeypatch):
    from hermes_cli.local_bridge import approve_install_request, start_local_bridge

    handle = start_local_bridge({"marketplace": {"local_bridge": {"enabled": True, "port": 0}}})
    assert handle is not None
    try:
        status, _headers, payload = _request(
            handle.port,
            "POST",
            "/api/skills/marketplace/install",
            body={"name": "alpha"},
        )
        assert status == 202
        request_id = payload["requestId"]

        status, _headers, request_payload = _request(
            handle.port,
            "GET",
            f"/api/skills/marketplace/install-requests/{request_id}",
        )
        assert status == 200
        assert request_payload["request"]["status"] == "pending"

        approve_install_request(request_id)
        status, _headers, request_payload = _request(
            handle.port,
            "GET",
            f"/api/skills/marketplace/install-requests/{request_id}",
        )
        assert status == 200
        assert request_payload["request"]["status"] == "approved"
    finally:
        handle.stop()


def test_local_bridge_install_request_persists_across_process_memory(monkeypatch):
    import hermes_cli.local_bridge as local_bridge

    local_bridge._PENDING_INSTALLS.clear()
    request = local_bridge.create_install_request({"name": "alpha"}, origin="test")
    local_bridge._PENDING_INSTALLS.clear()

    loaded = local_bridge.get_install_request(request["id"])
    assert loaded is not None
    assert loaded["id"] == request["id"]
    assert loaded["name"] == "alpha"

    approved = local_bridge.approve_install_request(request["id"])
    assert approved is not None
    assert approved["status"] == "approved"


def test_local_bridge_install_request_http_approve(monkeypatch):
    from hermes_cli.local_bridge import start_local_bridge

    handle = start_local_bridge({"marketplace": {"local_bridge": {"enabled": True, "port": 0}}})
    assert handle is not None
    try:
        status, _headers, payload = _request(
            handle.port,
            "POST",
            "/api/skills/marketplace/install",
            body={"name": "alpha"},
        )
        assert status == 202
        request_id = payload["requestId"]

        status, _headers, approve_payload = _request(
            handle.port,
            "POST",
            f"/api/skills/marketplace/install-requests/{request_id}/approve",
            body={},
        )
        assert status == 200
        assert approve_payload["request"]["status"] == "approved"
    finally:
        handle.stop()


def test_local_bridge_install_request_callback(monkeypatch):
    from hermes_cli.local_bridge import start_local_bridge

    seen = []
    event = threading.Event()

    def on_install_request(request):
        seen.append(request)
        event.set()

    handle = start_local_bridge(
        {"marketplace": {"local_bridge": {"enabled": True, "port": 0}}},
        on_install_request=on_install_request,
    )
    assert handle is not None
    try:
        status, _headers, payload = _request(
            handle.port,
            "POST",
            "/api/skills/marketplace/install",
            body={"name": "alpha"},
        )
        assert status == 202
        assert event.wait(2)
        assert seen[0]["id"] == payload["requestId"]
        assert seen[0]["name"] == "alpha"
    finally:
        handle.stop()


def test_local_bridge_install_requires_valid_wallet_signature_when_requested(monkeypatch):
    from hermes_cli.local_bridge import start_local_bridge

    handle = start_local_bridge({"marketplace": {"local_bridge": {"enabled": True, "port": 0}}})
    assert handle is not None
    try:
        status, _headers, payload = _request(
            handle.port,
            "POST",
            "/api/skills/marketplace/install",
            body={"name": "alpha", "walletAddress": "EQwallet", "requireWalletSignature": True},
        )
        assert status == 400
        assert "signature" in payload["error"].lower()

        status, _headers, payload = _request(
            handle.port,
            "POST",
            "/api/skills/marketplace/install",
            body={
                "name": "alpha",
                "walletAddress": "EQwallet",
                "requireWalletSignature": True,
                "walletSignature": _install_signature(),
            },
        )
        assert status == 202
        assert payload["approval_required"] is True
        assert payload["requireWalletSignature"] is True

        status, _headers, payload = _request(
            handle.port,
            "POST",
            "/api/skills/marketplace/install",
            body={
                "name": "alpha",
                "walletAddress": "EQwallet",
                "requireWalletSignature": True,
                "walletSignature": _opaque_install_signature(),
            },
        )
        assert status == 202
        assert payload["approval_required"] is True
    finally:
        handle.stop()


def test_local_bridge_uninstall_marketplace_skill(monkeypatch):
    import hermes_cli.skill_marketplace as skill_marketplace
    from hermes_cli.local_bridge import approve_install_request, start_local_bridge

    calls = []
    monkeypatch.setattr(skill_marketplace, "uninstall_marketplace_skill", lambda name: calls.append(name) or {
        "name": name,
        "skillId": name,
        "path": name,
        "message": f"Uninstalled {name}",
    })
    monkeypatch.setattr(skill_marketplace, "list_installed_marketplace_skills", lambda name="": [])

    handle = start_local_bridge({"marketplace": {"local_bridge": {"enabled": True, "port": 0}}})
    assert handle is not None
    try:
        status, _headers, payload = _request(
            handle.port,
            "DELETE",
            "/api/skills/marketplace/install",
            body={
                "name": "alpha",
                "walletAddress": "EQwallet",
                "requireWalletSignature": True,
                "walletSignature": _opaque_install_signature(mode="uninstall"),
            },
        )
        assert status == 202
        assert payload["approval_required"] is True
        request_id = payload["requestId"]
        assert approve_install_request(request_id)["status"] == "approved"

        status, _headers, payload = _request(
            handle.port,
            "DELETE",
            "/api/skills/marketplace/install",
            body={
                "name": "alpha",
                "requestId": request_id,
                "walletAddress": "EQwallet",
                "requireWalletSignature": True,
                "walletSignature": _opaque_install_signature(mode="uninstall"),
            },
        )
        assert status == 200
        assert payload["mode"] == "uninstall"
        assert payload["uninstalled"]["skillId"] == "alpha"
        assert payload["status"] is None
        assert calls == ["alpha"]
    finally:
        handle.stop()


def test_local_bridge_config_defaults_and_sanitizes_host():
    from hermes_cli.local_bridge import local_bridge_config

    assert local_bridge_config({}) == {
        "enabled": True,
        "host": "127.0.0.1",
        "port": 9119,
        "secure_tunnel": False,
    }
    assert local_bridge_config({
        "marketplace": {
            "local_bridge": {
                "enabled": False,
                "host": "0.0.0.0",
                "port": 9120,
                "secure_tunnel": True,
            }
        }
    }) == {
        "enabled": False,
        "host": "127.0.0.1",
        "port": 9120,
        "secure_tunnel": True,
    }
