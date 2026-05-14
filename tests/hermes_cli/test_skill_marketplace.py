from types import SimpleNamespace
import base64
import hashlib
import json

from hermes_cli.skill_marketplace import (
    _agent_build_hash,
    build_listing,
    bundles_dir,
    backfill_sales_from_mint_intents,
    check_skill_access,
    create_purchase_intent,
    create_purchase_intent_from_listing,
    delete_listing,
    fetch_skill_mint_status,
    install_marketplace_skill,
    install_snft_from_metadata_url,
    list_installed_marketplace_skills,
    load_encrypted_snft_skill_file,
    load_listing,
    normalize_marketplace_listing,
    _snft_browser_signer_config,
    _snft_evm_typed_data,
    _snft_solana_message,
    _snft_agent_unlock_headers,
    _snft_unlock_endpoint,
    _runtime_package_hash,
    publish_proof_payload,
    publish_listing_remote,
    nft_metadata_dir,
    record_marketplace_sale,
    save_listing,
    sales_dir,
    uninstall_marketplace_skill,
)
from hermes_cli.skills_hub import (
    do_marketplace_buy,
    do_marketplace_earnings,
    do_marketplace_install,
    do_marketplace_list,
    do_marketplace_status,
)
from io import StringIO
from rich.console import Console
import pytest


@pytest.fixture(autouse=True)
def _allow_example_urls(monkeypatch):
    monkeypatch.setattr("tools.url_safety.is_safe_url", lambda _url: True)


def test_snft_locked_secret_zeroize():
    from hermes_cli.snft_memory import lock_secret, unlock_secret

    secret = lock_secret(b"top-secret")
    assert bytes(secret.buffer) == b"top-secret"

    secret.zeroize()
    unlock_secret(secret)

    assert bytes(secret.buffer) == b"\x00" * len("top-secret")


def test_listing_defaults_to_holder_access(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    skill_dir = tmp_path / "skills" / "alpha"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: Alpha skill\n---\n# Alpha\n",
        encoding="utf-8",
    )

    listing = build_listing(skill_dir, creator_wallet="wallet", price_ton=1.5)
    path = save_listing(listing)

    assert path.exists()
    loaded = load_listing("alpha")
    assert loaded["access"]["policy"] == "holders"
    assert loaded["price_ton"] == 1.5
    assert loaded["bundle"]["bundle_hash"].startswith("sha256:")
    assert loaded["nft_metadata"]["type"] == "notpunks_skill_nft"
    assert loaded["security"]["verdict"] in {"safe", "caution", "dangerous"}
    assert (bundles_dir() / "alpha.tar.gz").exists()
    assert (nft_metadata_dir() / "alpha.json").exists()


def test_snft_unlock_endpoint_can_use_unlock_node_url():
    snft = {
        "unlock": {
            "type": "notpunks_unlock_network",
            "scheme": "direct_key_v1",
            "threshold": 1,
            "nodes": [
                {
                    "id": "notpunks-backend-1",
                    "url": "unlock",
                }
            ],
        }
    }

    assert _snft_unlock_endpoint(snft, "https://issuer.example/snft/alpha/metadata.json") == "https://issuer.example/snft/alpha/unlock"


def test_snft_unlock_endpoint_can_use_registry_url(monkeypatch):
    snft = {
        "skill_id": "alpha",
        "unlock": {
            "type": "notpunks_unlock_network",
            "scheme": "direct_key_v1",
            "threshold": 1,
            "registry": "unlock-networks/notpunks-mainnet-1",
        }
    }

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "success": True,
                "data": {
                    "nodes": [
                        {
                            "id": "notpunks-backend-1",
                            "url_template": "https://issuer.example/snft/{skill_id}/unlock",
                        }
                    ]
                },
            }

    seen = {}

    def fake_get(url, timeout=30):
        seen["url"] = url
        return _Response()

    monkeypatch.setattr("httpx.get", fake_get)

    assert _snft_unlock_endpoint(snft, "https://issuer.example/snft/alpha/metadata.json") == "https://issuer.example/snft/alpha/unlock"
    assert seen["url"] == "https://issuer.example/snft/alpha/unlock-networks/notpunks-mainnet-1"


def test_snft_agent_unlock_headers_include_build_hash(monkeypatch):
    monkeypatch.setenv("NOTPUNKS_AGENT_BUILD_HASH", "sha256:official-build")
    monkeypatch.setenv("NOTPUNKS_AGENT_ATTESTATION_SECRET", "secret")
    monkeypatch.setattr("time.time", lambda: 1234567890)

    headers = _snft_agent_unlock_headers("alpha", "sha256:encrypted")

    assert headers["X-NOTPUNKS-Agent-Version"]
    assert headers["X-NOTPUNKS-Agent-Build-Hash"] == "sha256:official-build"
    assert headers["X-NOTPUNKS-Agent-Timestamp"] == "1234567890"
    assert headers["X-NOTPUNKS-Agent-Attestation"].startswith("sha256=")


def test_runtime_package_hash_changes_when_runtime_file_changes(tmp_path):
    package_dir = tmp_path / "hermes_cli"
    package_dir.mkdir()
    runtime_file = package_dir / "skill_marketplace.py"
    runtime_file.write_text("print('official')\n", encoding="utf-8")

    first_hash = _runtime_package_hash(package_dir)
    runtime_file.write_text("print('modified')\n", encoding="utf-8")

    assert _runtime_package_hash(package_dir) != first_hash


def test_agent_build_hash_verifies_runtime_hash_from_build_info(tmp_path, monkeypatch):
    monkeypatch.delenv("NOTPUNKS_AGENT_BUILD_HASH", raising=False)
    package_dir = tmp_path / "hermes_cli"
    package_dir.mkdir()
    runtime_file = package_dir / "skill_marketplace.py"
    runtime_file.write_text("print('official')\n", encoding="utf-8")
    runtime_hash = _runtime_package_hash(package_dir)
    info_path = package_dir / "build-info.json"
    info_path.write_text(
        '{"build_hash": "%s", "runtime_hash": "%s"}\n' % (runtime_hash, runtime_hash),
        encoding="utf-8",
    )

    assert _agent_build_hash(info_path) == runtime_hash

    runtime_file.write_text("print('modified')\n", encoding="utf-8")

    assert _agent_build_hash(info_path) == "dev"


def test_check_skill_access_accepts_holder_and_license():
    holder_ctx = SimpleNamespace(
        can_use_custom_skills=True,
        can_create_skills=False,
        skill_licenses={},
        access_roles=["not_punks_holder"],
    )
    allowed, reason = check_skill_access(holder_ctx, {
        "skill_id": "alpha",
        "name": "alpha",
        "access": {"policy": "holders"},
    })
    assert allowed is True
    assert "holder" in reason

    licensed_ctx = SimpleNamespace(
        can_use_custom_skills=False,
        can_create_skills=False,
        skill_licenses={"alpha": {"tier": "standard"}},
        access_roles=[],
    )
    allowed, reason = check_skill_access(licensed_ctx, {
        "skill_id": "alpha",
        "name": "alpha",
        "access": {"policy": "skill_nft"},
    })
    assert allowed is True
    assert "license" in reason.lower()


def test_check_skill_access_accepts_public_api_listing_shape():
    licensed_ctx = SimpleNamespace(
        can_use_custom_skills=False,
        can_create_skills=False,
        skill_licenses={"alpha": {"tier": "standard"}},
        access_roles=[],
    )

    allowed, reason = check_skill_access(licensed_ctx, {
        "skillId": "alpha",
        "name": "alpha",
        "accessPolicy": "skill_nft",
    })

    assert allowed is True
    assert "license" in reason.lower()


def test_purchase_intent_records_pending_contract(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    save_listing({
        "skill_id": "alpha",
        "name": "alpha",
        "price_ton": 2,
        "currency": "TON",
        "access": {"policy": "skill_nft"},
    })

    intent = create_purchase_intent("alpha", wallet_address="wallet")

    assert intent["status"] == "pending_mint"
    assert intent["wallet_address"] == "wallet"


def test_purchase_intent_from_remote_listing_shape(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    intent = create_purchase_intent_from_listing({
        "skillId": "alpha",
        "name": "alpha",
        "priceTon": 2,
        "currency": "TON",
        "bundleHash": "sha256:abc",
        "nftMetadataUrl": "https://app.notpunks.com/api/skills/marketplace/alpha/metadata",
    }, wallet_address="wallet")

    assert intent["skill_id"] == "alpha"
    assert intent["bundle_hash"] == "sha256:abc"
    assert intent["nft_metadata_url"].endswith("/alpha/metadata")


def test_record_marketplace_sale_writes_creator_amount(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    intent = {
        "skill_id": "alpha",
        "name": "alpha",
        "wallet_address": "wallet",
        "price_ton": 2,
        "currency": "TON",
        "bundle_hash": "sha256:abc",
    }

    record = record_marketplace_sale(intent, mint={"mint": {"status": "submitted"}})

    assert record["creator_amount_ton"] == 1.8
    assert record["platform_fee_ton"] == 0.2
    assert record["mint_status"] == "submitted"
    assert list(sales_dir().glob("alpha-*.json"))


def test_record_marketplace_sale_preserves_confirmed_mint_status(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    intent = {
        "skill_id": "alpha",
        "name": "alpha",
        "wallet_address": "wallet",
        "price_ton": 2,
    }

    record = record_marketplace_sale(intent, mint={"mint": {"status": "confirmed", "nftAddress": "EQnft"}})

    assert record["status"] == "confirmed"
    assert record["nft_address"] == "EQnft"


def test_fetch_skill_mint_status_calls_remote_endpoint(monkeypatch):
    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"success": True, "data": {"confirmed": 1, "pending": 0}}

    seen = {}

    def fake_get(url, params=None, timeout=45):
        seen["url"] = url
        seen["params"] = params
        seen["timeout"] = timeout
        return _Response()

    monkeypatch.setattr("httpx.get", fake_get)

    data = fetch_skill_mint_status("Alpha Skill", wallet_address="wallet", purchase_id="p1", base_url="https://app.notpunks.com")

    assert data["confirmed"] == 1
    assert seen["url"].endswith("/alpha-skill/mint/status")
    assert seen["params"] == {"walletAddress": "wallet", "purchaseId": "p1"}


def test_backfill_sales_from_mint_intents_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    intents = tmp_path / "home" / "skills_marketplace" / "mint_intents"
    intents.mkdir(parents=True)
    (intents / "alpha-wallet-1.json").write_text(
        """{
  "skillId": "alpha",
  "walletAddress": "wallet",
  "priceTon": 2,
  "currency": "TON",
  "bundleHash": "sha256:abc",
  "mint": {
    "status": "submitted",
    "itemIndex": "42"
  }
}
""",
        encoding="utf-8",
    )

    assert backfill_sales_from_mint_intents() == 1
    assert backfill_sales_from_mint_intents() == 0
    records = list(sales_dir().glob("alpha-*.json"))
    assert len(records) == 1


def test_marketplace_earnings_backfills_existing_mint_intents(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    intents = tmp_path / "home" / "skills_marketplace" / "mint_intents"
    intents.mkdir(parents=True)
    (intents / "alpha-wallet-1.json").write_text(
        """{
  "skillId": "alpha",
  "walletAddress": "wallet",
  "priceTon": 2,
  "currency": "TON",
  "bundleHash": "sha256:abc",
  "mint": {
    "status": "submitted",
    "itemIndex": "42"
  }
}
""",
        encoding="utf-8",
    )
    sink = StringIO()
    console = Console(file=sink, force_terminal=False, color_system=None)

    do_marketplace_earnings(console=console)

    output = sink.getvalue()
    assert "Sales records: 1" in output
    assert "Backfilled mint intents: 1" in output
    assert "Creator earnings: 1.8 TON" in output


def test_normalize_marketplace_listing_maps_public_shape():
    listing = normalize_marketplace_listing({
        "skillId": "alpha",
        "priceTon": 3,
        "accessPolicy": "skill_nft",
        "bundleHash": "sha256:abc",
        "bundleUrl": "https://app.notpunks.com/bundle",
        "creatorWallet": "wallet",
    })

    assert listing["skill_id"] == "alpha"
    assert listing["price_ton"] == 3
    assert listing["access"]["policy"] == "skill_nft"
    assert listing["bundle"]["bundle_hash"] == "sha256:abc"
    assert listing["creator_wallet"] == "wallet"


def test_marketplace_list_prints_saved_listing(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    save_listing({
        "skill_id": "alpha",
        "name": "alpha",
        "price_ton": 2,
        "currency": "TON",
        "access": {"policy": "holders"},
        "status": "local_published",
    })
    sink = StringIO()
    console = Console(file=sink, force_terminal=False, color_system=None)

    do_marketplace_list(console=console)

    output = sink.getvalue()
    assert "Skill Marketplace" in output
    assert "alpha" in output
    assert "2 TON" in output


def test_delete_listing_removes_marketplace_entry(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    save_listing({
        "skill_id": "alpha",
        "name": "alpha",
        "price_ton": 2,
        "currency": "TON",
        "access": {"policy": "holders"},
    })

    deleted = delete_listing("alpha")

    assert deleted is not None
    assert load_listing("alpha") is None


def test_install_marketplace_skill_downloads_verifies_and_installs(tmp_path, monkeypatch):
    import tools.skills_hub as hub

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    skills_dir = tmp_path / "installed-skills"
    hub_dir = skills_dir / ".hub"
    monkeypatch.setattr(hub, "SKILLS_DIR", skills_dir)
    monkeypatch.setattr(hub, "HUB_DIR", hub_dir)
    monkeypatch.setattr(hub, "LOCK_FILE", hub_dir / "lock.json")
    monkeypatch.setattr(hub, "QUARANTINE_DIR", hub_dir / "quarantine")
    monkeypatch.setattr(hub, "AUDIT_LOG", hub_dir / "audit.log")
    monkeypatch.setattr(hub, "TAPS_FILE", hub_dir / "taps.json")
    monkeypatch.setattr(hub, "INDEX_CACHE_DIR", hub_dir / "index-cache")

    source_dir = tmp_path / "source" / "alpha"
    source_dir.mkdir(parents=True)
    (source_dir / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: Alpha skill\n---\n# Alpha\n",
        encoding="utf-8",
    )
    listing = build_listing(source_dir, creator_wallet="wallet", price_ton=1)
    save_listing(listing)
    bundle_bytes = (bundles_dir() / "alpha.tar.gz").read_bytes()

    class _Response:
        def __init__(self, payload=None, content=b""):
            self._payload = payload
            self.content = content

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    def fake_get(url, timeout=30):
        if url.endswith("/api/skills/marketplace"):
            return _Response({"success": True, "data": {"listings": [{
                "skillId": "alpha",
                "name": "alpha",
                "bundleHash": listing["bundle"]["bundle_hash"],
                "bundleUrl": "https://app.notpunks.com/api/skills/marketplace/alpha/bundle",
                "nftMetadataUrl": listing["nft_metadata"]["url"],
                "priceTon": 1,
                "creatorWallet": "wallet",
            }]}})
        if url.endswith("/alpha/bundle"):
            return _Response(content=bundle_bytes)
        if url.endswith("/alpha/metadata"):
            return _Response({"name": "alpha"})
        raise AssertionError(url)

    monkeypatch.setattr("httpx.get", fake_get)

    result = install_marketplace_skill("alpha")

    assert result["name"] == "alpha"
    assert result["bundle_hash"] == listing["bundle"]["bundle_hash"]
    assert (skills_dir / "alpha" / "SKILL.md").exists()

    rows = list_installed_marketplace_skills("alpha")
    assert rows[0]["skillId"] == "alpha"
    assert rows[0]["status"] == "installed"
    assert rows[0]["path"] == "alpha"
    assert rows[0]["bundleHash"] == listing["bundle"]["bundle_hash"]
    assert rows[0]["listingBundleHash"] == listing["bundle"]["bundle_hash"]
    assert rows[0]["currentBundleHash"] == listing["bundle"]["bundle_hash"]

    buf = StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    do_marketplace_status("alpha", console=console)
    output = buf.getvalue()
    assert "Marketplace Skill Install Status" in output
    assert "alpha" in output
    assert "installed" in output

    uninstalled = uninstall_marketplace_skill("alpha")
    assert uninstalled["skillId"] == "alpha"
    assert not (skills_dir / "alpha").exists()
    assert list_installed_marketplace_skills("alpha") == []


def test_install_marketplace_skill_prefers_encrypted_snft_cartridge(tmp_path, monkeypatch):
    import tools.skills_hub as hub
    import tools.skills_tool as skills_tool
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("NOTPUNKS_MARKETPLACE_UNLOCK_TOKEN", "unlock-token")
    skills_dir = tmp_path / "installed-skills"
    hub_dir = skills_dir / ".hub"
    monkeypatch.setattr(hub, "SKILLS_DIR", skills_dir)
    monkeypatch.setattr(hub, "HUB_DIR", hub_dir)
    monkeypatch.setattr(hub, "LOCK_FILE", hub_dir / "lock.json")
    monkeypatch.setattr(hub, "QUARANTINE_DIR", hub_dir / "quarantine")
    monkeypatch.setattr(hub, "AUDIT_LOG", hub_dir / "audit.log")
    monkeypatch.setattr(hub, "TAPS_FILE", hub_dir / "taps.json")
    monkeypatch.setattr(hub, "INDEX_CACHE_DIR", hub_dir / "index-cache")
    monkeypatch.setattr(skills_tool, "SKILLS_DIR", skills_dir)

    source_dir = tmp_path / "source" / "alpha"
    source_dir.mkdir(parents=True)
    (source_dir / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: Alpha skill\n---\n# Alpha\n",
        encoding="utf-8",
    )
    listing = build_listing(source_dir, creator_wallet="wallet", price_ton=1)
    save_listing(listing)
    bundle_bytes = (bundles_dir() / "alpha.tar.gz").read_bytes()
    plaintext_hash = f"sha256:{hashlib.sha256(bundle_bytes).hexdigest()}"
    key = b"1" * 32
    nonce = b"2" * 12
    aad = f"snft:v1:alpha:{plaintext_hash}".encode()
    encrypted = AESGCM(key).encrypt(nonce, bundle_bytes, aad)
    encrypted_hash = f"sha256:{hashlib.sha256(encrypted).hexdigest()}"

    class _Response:
        def __init__(self, payload=None, content=b""):
            self._payload = payload
            self.content = content

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    def fake_get(url, timeout=30):
        if url.endswith("/api/skills/marketplace"):
            return _Response({"success": True, "data": {"listings": [{
                "skillId": "alpha",
                "name": "alpha",
                "bundleHash": listing["bundle"]["bundle_hash"],
                "bundleUrl": "https://app.notpunks.com/api/skills/marketplace/alpha/bundle",
                "nftMetadataUrl": "https://app.notpunks.com/api/skills/marketplace/alpha/metadata",
                "priceTon": 1,
                "creatorWallet": "wallet",
            }]}})
        if url.endswith("/alpha/metadata"):
            return _Response({
                "name": "alpha",
                "snft": {
                    "protocol": "snft",
                    "version": "1.0",
                    "content": {
                        "mode": "encrypted_external",
                        "encryption": "aes-256-gcm",
                        "encrypted_sha256": encrypted_hash,
                        "plaintext_sha256": plaintext_hash,
                        "uris": ["https://app.notpunks.com/api/skills/marketplace/alpha/cartridge"],
                    },
                    "unlock": {
                        "challenge": f"snft-unlock:alpha:{encrypted_hash}",
                        "endpoint": "https://app.notpunks.com/api/skills/marketplace/alpha/unlock",
                    },
                },
            })
        if url.endswith("/alpha/cartridge"):
            return _Response(content=encrypted)
        if url.endswith("/alpha/bundle"):
            raise AssertionError("legacy bundle should not be downloaded for sNFT")
        raise AssertionError(url)

    def fake_post(url, json=None, headers=None, timeout=60):
        assert url.endswith("/alpha/unlock")
        assert headers["X-NOTPUNKS-Publish-Token"] == "unlock-token"
        assert headers["X-NOTPUNKS-Agent-Version"]
        return _Response({"success": True, "data": {
            "encryption": "aes-256-gcm",
            "key": base64.b64encode(key).decode(),
            "nonce": base64.b64encode(nonce).decode(),
            "aad": aad.decode(),
            "encryptedSha256": encrypted_hash,
            "plaintextSha256": plaintext_hash,
        }})

    monkeypatch.setattr("httpx.get", fake_get)
    monkeypatch.setattr("httpx.post", fake_post)

    result = install_marketplace_skill("alpha")

    assert result["name"] == "alpha"
    assert result["source_kind"] == "snft_encrypted_runtime"
    assert result["bundle_hash"] == plaintext_hash
    assert (skills_dir / "alpha" / "SKILL.md").exists()
    assert "# Alpha" not in (skills_dir / "alpha" / "SKILL.md").read_text(encoding="utf-8")
    assert (skills_dir / "alpha" / ".notpunks-snft" / "cartridge.enc").exists()
    assert (skills_dir / "alpha" / ".notpunks-snft" / "manifest.json").exists()

    from tools.skills_tool import skill_view

    viewed = skill_view("alpha", preprocess=False)
    viewed_payload = json.loads(viewed)
    assert viewed_payload["success"] is True
    assert viewed_payload["protected_runtime"] is True
    assert viewed_payload["runtime_mode"] == "local_protected"
    assert viewed_payload["source_export"] is False
    assert "Protected sNFT Skill" in viewed_payload["content"]
    assert "skill_run_protected" in viewed_payload["content"]
    assert "# Alpha" not in viewed_payload["content"]

    direct_source = json.loads(skill_view("alpha", file_path="SKILL.md", preprocess=False))
    assert direct_source["success"] is False
    assert "source export is disabled" in direct_source["error"]
    assert direct_source["protected_runtime"] is True
    assert "# Alpha" not in json.dumps(direct_source)

    monkeypatch.setenv("NOTPUNKS_SNFT_ALLOW_SOURCE_EXPORT", "1")
    direct_source_with_override = json.loads(skill_view("alpha", file_path="SKILL.md", preprocess=False))
    assert direct_source_with_override["success"] is False
    assert "source export is disabled" in direct_source_with_override["error"]
    assert "# Alpha" not in json.dumps(direct_source_with_override)
    monkeypatch.delenv("NOTPUNKS_SNFT_ALLOW_SOURCE_EXPORT", raising=False)

    unlocked = load_encrypted_snft_skill_file(skills_dir / "alpha", "")
    assert unlocked["ok"] is True
    assert unlocked["memory_hardening"]["core_dumps_disabled"] in {True, False}

    captured_messages = {}

    def fake_call_llm(**kwargs):
        captured_messages["messages"] = kwargs["messages"]
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="protected runtime answer")
                )
            ]
        )

    monkeypatch.setattr("agent.auxiliary_client.call_llm", fake_call_llm)

    from tools.skills_tool import skill_run_protected

    protected_run = json.loads(skill_run_protected(
        "alpha",
        "Use the skill for this task",
        conversation_context="User context stays outside the public skill view.",
    ))
    assert protected_run["success"] is True
    assert protected_run["result"] == "protected runtime answer"
    assert "# Alpha" in captured_messages["messages"][0]["content"]
    assert "User context stays outside" in captured_messages["messages"][1]["content"]


def test_install_snft_from_metadata_url_installs_without_marketplace_listing(tmp_path, monkeypatch):
    import tools.skills_hub as hub
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("NOTPUNKS_MARKETPLACE_UNLOCK_TOKEN", "unlock-token")
    skills_dir = tmp_path / "installed-skills"
    hub_dir = skills_dir / ".hub"
    monkeypatch.setattr(hub, "SKILLS_DIR", skills_dir)
    monkeypatch.setattr(hub, "HUB_DIR", hub_dir)
    monkeypatch.setattr(hub, "LOCK_FILE", hub_dir / "lock.json")
    monkeypatch.setattr(hub, "QUARANTINE_DIR", hub_dir / "quarantine")
    monkeypatch.setattr(hub, "AUDIT_LOG", hub_dir / "audit.log")
    monkeypatch.setattr(hub, "TAPS_FILE", hub_dir / "taps.json")
    monkeypatch.setattr(hub, "INDEX_CACHE_DIR", hub_dir / "index-cache")

    source_dir = tmp_path / "source" / "alpha"
    source_dir.mkdir(parents=True)
    (source_dir / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: Alpha skill\n---\n# Alpha\n",
        encoding="utf-8",
    )
    listing = build_listing(source_dir, creator_wallet="wallet", price_ton=1)
    save_listing(listing)
    bundle_bytes = (bundles_dir() / "alpha.tar.gz").read_bytes()
    plaintext_hash = f"sha256:{hashlib.sha256(bundle_bytes).hexdigest()}"
    key = b"3" * 32
    nonce = b"4" * 12
    aad = f"snft:v1:alpha:{plaintext_hash}".encode()
    encrypted = AESGCM(key).encrypt(nonce, bundle_bytes, aad)
    encrypted_hash = f"sha256:{hashlib.sha256(encrypted).hexdigest()}"
    metadata_url = "https://issuer.example/snft/alpha/metadata.json"

    class _Response:
        def __init__(self, payload=None, content=b""):
            self._payload = payload
            self.content = content

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    def fake_get(url, timeout=30):
        if url == metadata_url:
            return _Response({
                "name": "Alpha Cartridge",
                "skill_id": "alpha",
                "bundle_hash": listing["bundle"]["bundle_hash"],
                "snft": {
                    "protocol": "snft",
                    "version": "1.0",
                    "content": {
                        "mode": "encrypted_external",
                        "encryption": "aes-256-gcm",
                        "encrypted_sha256": encrypted_hash,
                        "plaintext_sha256": plaintext_hash,
                        "uris": ["cartridge.bin"],
                    },
                    "unlock": {
                        "challenge": f"snft-unlock:alpha:{encrypted_hash}",
                        "endpoint": "unlock",
                    },
                },
            })
        if url == "https://issuer.example/snft/alpha/cartridge.bin":
            return _Response(content=encrypted)
        raise AssertionError(url)

    def fake_post(url, json=None, headers=None, timeout=60):
        assert url == "https://issuer.example/snft/alpha/unlock"
        assert headers["X-NOTPUNKS-Publish-Token"] == "unlock-token"
        assert headers["X-NOTPUNKS-Agent-Version"]
        return _Response({"success": True, "data": {
            "encryption": "aes-256-gcm",
            "key": base64.b64encode(key).decode(),
            "nonce": base64.b64encode(nonce).decode(),
            "aad": aad.decode(),
            "plaintextSha256": plaintext_hash,
        }})

    monkeypatch.setattr("httpx.get", fake_get)
    monkeypatch.setattr("httpx.post", fake_post)

    result = install_snft_from_metadata_url(metadata_url)

    assert result["name"] == "alpha"
    assert result["source_kind"] == "snft_encrypted_runtime"
    assert result["bundle_hash"] == plaintext_hash
    assert (skills_dir / "alpha" / "SKILL.md").exists()
    assert "# Alpha" not in (skills_dir / "alpha" / "SKILL.md").read_text(encoding="utf-8")
    assert (skills_dir / "alpha" / ".notpunks-snft" / "cartridge.enc").exists()


def test_install_snft_blocks_private_metadata_url(monkeypatch):
    monkeypatch.setattr("tools.url_safety.is_safe_url", lambda _url: False)

    try:
        install_snft_from_metadata_url("http://127.0.0.1:8080/snft/metadata.json")
    except ValueError as exc:
        assert "unsafe address" in str(exc)
    else:
        raise AssertionError("private metadata URL was not blocked")


def test_snft_unlock_endpoint_blocks_private_url(monkeypatch):
    monkeypatch.setattr("tools.url_safety.is_safe_url", lambda url: not url.startswith("http://127.0.0.1"))

    try:
        _snft_unlock_endpoint(
            {"unlock": {"endpoint": "http://127.0.0.1:9119/api/secret"}},
            "https://issuer.example/snft/alpha/metadata.json",
        )
    except ValueError as exc:
        assert "unsafe address" in str(exc)
    else:
        raise AssertionError("private unlock endpoint was not blocked")


def test_install_snft_posts_normalized_ton_unlock_request(tmp_path, monkeypatch):
    import tools.skills_hub as hub
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("NOTPUNKS_MARKETPLACE_UNLOCK_TOKEN", raising=False)
    monkeypatch.delenv("NOTPUNKS_MARKETPLACE_PUBLISH_TOKEN", raising=False)
    monkeypatch.delenv("SKILL_MARKETPLACE_PUBLISH_SECRET", raising=False)
    skills_dir = tmp_path / "installed-skills"
    hub_dir = skills_dir / ".hub"
    monkeypatch.setattr(hub, "SKILLS_DIR", skills_dir)
    monkeypatch.setattr(hub, "HUB_DIR", hub_dir)
    monkeypatch.setattr(hub, "LOCK_FILE", hub_dir / "lock.json")
    monkeypatch.setattr(hub, "QUARANTINE_DIR", hub_dir / "quarantine")
    monkeypatch.setattr(hub, "AUDIT_LOG", hub_dir / "audit.log")
    monkeypatch.setattr(hub, "TAPS_FILE", hub_dir / "taps.json")
    monkeypatch.setattr(hub, "INDEX_CACHE_DIR", hub_dir / "index-cache")

    source_dir = tmp_path / "source" / "alpha"
    source_dir.mkdir(parents=True)
    (source_dir / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: Alpha skill\n---\n# Alpha\n",
        encoding="utf-8",
    )
    listing = build_listing(source_dir, creator_wallet="wallet", price_ton=1)
    save_listing(listing)
    bundle_bytes = (bundles_dir() / "alpha.tar.gz").read_bytes()
    plaintext_hash = f"sha256:{hashlib.sha256(bundle_bytes).hexdigest()}"
    key = b"5" * 32
    nonce = b"6" * 12
    aad = f"snft:v1:alpha:{plaintext_hash}".encode()
    encrypted = AESGCM(key).encrypt(nonce, bundle_bytes, aad)
    encrypted_hash = f"sha256:{hashlib.sha256(encrypted).hexdigest()}"
    challenge = f"snft-unlock:alpha:{encrypted_hash}"
    wallet_proof = {
        "walletAddress": "EQwallet",
        "publicKey": "pubkey",
        "walletStateInit": "state",
        "proof": {"payload": challenge},
    }

    class _Response:
        def __init__(self, payload=None, content=b""):
            self._payload = payload
            self.content = content

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    def fake_get(url, timeout=30):
        if url == "https://issuer.example/snft/alpha/metadata.json":
            return _Response({
                "name": "Alpha Cartridge",
                "skill_id": "alpha",
                "bundle_hash": listing["bundle"]["bundle_hash"],
                "snft": {
                    "protocol": "snft",
                    "version": "1.0",
                    "content": {
                        "mode": "encrypted_external",
                        "encryption": "aes-256-gcm",
                        "encrypted_sha256": encrypted_hash,
                        "plaintext_sha256": plaintext_hash,
                        "uris": ["cartridge.bin"],
                    },
                    "unlock": {
                        "challenge": challenge,
                        "endpoint": "unlock",
                    },
                    "chains": [{
                        "chain": "ton",
                        "standard": "TEP-62",
                        "metadata_standard": "TEP-64",
                        "proof": {
                            "method": "ton_proof",
                            "collection_address": "EQcollection",
                            "challenge": challenge,
                        },
                    }],
                },
            })
        if url == "https://issuer.example/snft/alpha/cartridge.bin":
            return _Response(content=encrypted)
        raise AssertionError(url)

    seen = {}

    def fake_post(url, json=None, headers=None, timeout=60):
        seen["url"] = url
        seen["json"] = json
        seen["headers"] = headers
        return _Response({"success": True, "data": {
            "encryption": "aes-256-gcm",
            "key": base64.b64encode(key).decode(),
            "nonce": base64.b64encode(nonce).decode(),
            "aad": aad.decode(),
            "plaintextSha256": plaintext_hash,
        }})

    monkeypatch.setattr("httpx.get", fake_get)
    monkeypatch.setattr("httpx.post", fake_post)
    monkeypatch.setattr("hermes_cli.skill_marketplace.request_publish_wallet_proof", lambda *args, **kwargs: wallet_proof)

    result = install_snft_from_metadata_url("https://issuer.example/snft/alpha/metadata.json")

    assert result["name"] == "alpha"
    assert seen["headers"]["X-NOTPUNKS-Agent-Version"]
    assert seen["url"] == "https://issuer.example/snft/alpha/unlock"
    assert seen["json"]["walletProof"] == wallet_proof
    assert seen["json"]["unlockRequest"]["protocol"] == "snft"
    assert seen["json"]["unlockRequest"]["chain"] == "ton"
    assert seen["json"]["unlockRequest"]["wallet"] == "EQwallet"
    assert seen["json"]["unlockRequest"]["challenge"] == challenge
    assert seen["json"]["unlockRequest"]["nft"]["collection_address"] == "EQcollection"
    assert seen["json"]["unlockRequest"]["proof"]["method"] == "ton_proof"


def test_install_snft_posts_external_evm_unlock_request(tmp_path, monkeypatch):
    import json
    import tools.skills_hub as hub
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("NOTPUNKS_MARKETPLACE_UNLOCK_TOKEN", raising=False)
    monkeypatch.setenv("NOTPUNKS_SNFT_CHAIN", "evm")
    skills_dir = tmp_path / "installed-skills"
    hub_dir = skills_dir / ".hub"
    monkeypatch.setattr(hub, "SKILLS_DIR", skills_dir)
    monkeypatch.setattr(hub, "HUB_DIR", hub_dir)
    monkeypatch.setattr(hub, "LOCK_FILE", hub_dir / "lock.json")
    monkeypatch.setattr(hub, "QUARANTINE_DIR", hub_dir / "quarantine")
    monkeypatch.setattr(hub, "AUDIT_LOG", hub_dir / "audit.log")
    monkeypatch.setattr(hub, "TAPS_FILE", hub_dir / "taps.json")
    monkeypatch.setattr(hub, "INDEX_CACHE_DIR", hub_dir / "index-cache")

    source_dir = tmp_path / "source" / "alpha"
    source_dir.mkdir(parents=True)
    (source_dir / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: Alpha skill\n---\n# Alpha\n",
        encoding="utf-8",
    )
    listing = build_listing(source_dir, creator_wallet="wallet", price_ton=1)
    save_listing(listing)
    bundle_bytes = (bundles_dir() / "alpha.tar.gz").read_bytes()
    plaintext_hash = f"sha256:{hashlib.sha256(bundle_bytes).hexdigest()}"
    key = b"7" * 32
    nonce = b"8" * 12
    aad = f"snft:v1:alpha:{plaintext_hash}".encode()
    encrypted = AESGCM(key).encrypt(nonce, bundle_bytes, aad)
    encrypted_hash = f"sha256:{hashlib.sha256(encrypted).hexdigest()}"
    challenge = f"snft-unlock:alpha:{encrypted_hash}"
    unlock_request = {
        "protocol": "snft",
        "version": "1.0",
        "skill_id": "alpha",
        "chain": "evm",
        "wallet": "0x0000000000000000000000000000000000000001",
        "challenge": challenge,
        "nft": {
            "chain_id": 1,
            "contract": "0x0000000000000000000000000000000000000002",
            "token_id": "1",
            "standard": "ERC-721",
        },
        "proof": {
            "method": "eip712",
            "wallet": "0x0000000000000000000000000000000000000001",
            "signature": "0xsig",
            "typedData": {"domain": {}, "types": {}, "message": {"challenge": challenge}},
        },
    }
    monkeypatch.setenv("NOTPUNKS_SNFT_UNLOCK_REQUEST", json.dumps(unlock_request))

    class _Response:
        def __init__(self, payload=None, content=b""):
            self._payload = payload
            self.content = content

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    def fake_get(url, timeout=30):
        if url == "https://issuer.example/snft/alpha/metadata.json":
            return _Response({
                "name": "Alpha Cartridge",
                "skill_id": "alpha",
                "bundle_hash": listing["bundle"]["bundle_hash"],
                "snft": {
                    "protocol": "snft",
                    "version": "1.0",
                    "content": {
                        "mode": "encrypted_external",
                        "encryption": "aes-256-gcm",
                        "encrypted_sha256": encrypted_hash,
                        "plaintext_sha256": plaintext_hash,
                        "uris": ["cartridge.bin"],
                    },
                    "unlock": {
                        "challenge": challenge,
                        "endpoint": "unlock",
                    },
                    "chains": [{
                        "chain": "evm",
                        "standard": "ERC-721",
                        "metadata_standard": "ERC-721 Metadata JSON",
                        "proof": {
                            "method": "eip712",
                            "chain_id": 1,
                            "contract": "0x0000000000000000000000000000000000000002",
                            "token_id": "1",
                            "challenge": challenge,
                        },
                    }],
                },
            })
        if url == "https://issuer.example/snft/alpha/cartridge.bin":
            return _Response(content=encrypted)
        raise AssertionError(url)

    seen = {}

    def fake_post(url, json=None, headers=None, timeout=60):
        seen["json"] = json
        return _Response({"success": True, "data": {
            "encryption": "aes-256-gcm",
            "key": base64.b64encode(key).decode(),
            "nonce": base64.b64encode(nonce).decode(),
            "aad": aad.decode(),
            "plaintextSha256": plaintext_hash,
        }})

    monkeypatch.setattr("httpx.get", fake_get)
    monkeypatch.setattr("httpx.post", fake_post)

    result = install_snft_from_metadata_url("https://issuer.example/snft/alpha/metadata.json")

    assert result["name"] == "alpha"
    assert "walletProof" not in seen["json"]
    assert seen["json"]["unlockRequest"] == unlock_request
    assert seen["json"]["chain"] == "evm"
    assert seen["json"]["proof"]["method"] == "eip712"


def test_snft_browser_signer_builds_evm_and_solana_payloads():
    snft = {
        "version": "1.0",
        "unlock": {"challenge": "fallback"},
    }
    evm_chain = {
        "chain": "evm",
        "standard": "ERC-721",
        "proof": {
            "method": "eip712",
            "chain_id": 8453,
            "contract": "0x0000000000000000000000000000000000000002",
            "token_id": "7",
            "challenge": "snft-unlock:alpha:hash",
        },
    }
    solana_chain = {
        "chain": "solana",
        "standard": "Metaplex NFT",
        "proof": {
            "method": "solana_sign_message",
            "mint": "So11111111111111111111111111111111111111112",
            "challenge": "snft-unlock:alpha:hash",
        },
    }

    evm_config = _snft_browser_signer_config(skill_id="alpha", snft=snft, chain=evm_chain)
    typed_data = _snft_evm_typed_data(
        skill_id="alpha",
        challenge=evm_config["challenge"],
        chain=evm_chain,
        wallet="0x0000000000000000000000000000000000000001",
    )
    solana_message = _snft_solana_message(
        skill_id="alpha",
        challenge="snft-unlock:alpha:hash",
        chain=solana_chain,
        wallet="wallet",
    )

    assert evm_config["chain"] == "evm"
    assert evm_config["nft"]["chain_id"] == 8453
    assert evm_config["nft"]["token_id"] == "7"
    assert typed_data["domain"]["chainId"] == 8453
    assert typed_data["message"]["skill_id"] == "alpha"
    assert typed_data["message"]["challenge"] == "snft-unlock:alpha:hash"
    assert "mint=So11111111111111111111111111111111111111112" in solana_message
    assert "wallet=wallet" in solana_message


def test_install_snft_uses_browser_signer_for_evm_unlock_request(tmp_path, monkeypatch):
    import tools.skills_hub as hub
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("NOTPUNKS_MARKETPLACE_UNLOCK_TOKEN", raising=False)
    monkeypatch.delenv("NOTPUNKS_SNFT_UNLOCK_REQUEST", raising=False)
    monkeypatch.delenv("NOTPUNKS_SNFT_UNLOCK_REQUEST_FILE", raising=False)
    monkeypatch.setenv("NOTPUNKS_SNFT_CHAIN", "evm")
    skills_dir = tmp_path / "installed-skills"
    hub_dir = skills_dir / ".hub"
    monkeypatch.setattr(hub, "SKILLS_DIR", skills_dir)
    monkeypatch.setattr(hub, "HUB_DIR", hub_dir)
    monkeypatch.setattr(hub, "LOCK_FILE", hub_dir / "lock.json")
    monkeypatch.setattr(hub, "QUARANTINE_DIR", hub_dir / "quarantine")
    monkeypatch.setattr(hub, "AUDIT_LOG", hub_dir / "audit.log")
    monkeypatch.setattr(hub, "TAPS_FILE", hub_dir / "taps.json")
    monkeypatch.setattr(hub, "INDEX_CACHE_DIR", hub_dir / "index-cache")

    source_dir = tmp_path / "source" / "alpha"
    source_dir.mkdir(parents=True)
    (source_dir / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: Alpha skill\n---\n# Alpha\n",
        encoding="utf-8",
    )
    listing = build_listing(source_dir, creator_wallet="wallet", price_ton=1)
    save_listing(listing)
    bundle_bytes = (bundles_dir() / "alpha.tar.gz").read_bytes()
    plaintext_hash = f"sha256:{hashlib.sha256(bundle_bytes).hexdigest()}"
    key = b"9" * 32
    nonce = b"a" * 12
    aad = f"snft:v1:alpha:{plaintext_hash}".encode()
    encrypted = AESGCM(key).encrypt(nonce, bundle_bytes, aad)
    encrypted_hash = f"sha256:{hashlib.sha256(encrypted).hexdigest()}"
    challenge = f"snft-unlock:alpha:{encrypted_hash}"
    unlock_request = {
        "protocol": "snft",
        "version": "1.0",
        "skill_id": "alpha",
        "chain": "evm",
        "wallet": "0x0000000000000000000000000000000000000001",
        "challenge": challenge,
        "nft": {
            "chain_id": 1,
            "contract": "0x0000000000000000000000000000000000000002",
            "token_id": "1",
        },
        "proof": {
            "method": "eip712",
            "wallet": "0x0000000000000000000000000000000000000001",
            "signature": "0xsig",
            "typedData": {"message": {"challenge": challenge}},
        },
    }

    class _Response:
        def __init__(self, payload=None, content=b""):
            self._payload = payload
            self.content = content

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    def fake_get(url, timeout=30):
        if url == "https://issuer.example/snft/alpha/metadata.json":
            return _Response({
                "name": "Alpha Cartridge",
                "skill_id": "alpha",
                "bundle_hash": listing["bundle"]["bundle_hash"],
                "snft": {
                    "protocol": "snft",
                    "version": "1.0",
                    "content": {
                        "mode": "encrypted_external",
                        "encryption": "aes-256-gcm",
                        "encrypted_sha256": encrypted_hash,
                        "plaintext_sha256": plaintext_hash,
                        "uris": ["cartridge.bin"],
                    },
                    "unlock": {
                        "challenge": challenge,
                        "endpoint": "unlock",
                    },
                    "chains": [{
                        "chain": "evm",
                        "standard": "ERC-721",
                        "metadata_standard": "ERC-721 Metadata JSON",
                        "proof": {
                            "method": "eip712",
                            "chain_id": 1,
                            "contract": "0x0000000000000000000000000000000000000002",
                            "token_id": "1",
                            "challenge": challenge,
                        },
                    }],
                },
            })
        if url == "https://issuer.example/snft/alpha/cartridge.bin":
            return _Response(content=encrypted)
        raise AssertionError(url)

    seen = {}

    def fake_post(url, json=None, headers=None, timeout=60):
        seen["json"] = json
        return _Response({"success": True, "data": {
            "encryption": "aes-256-gcm",
            "key": base64.b64encode(key).decode(),
            "nonce": base64.b64encode(nonce).decode(),
            "aad": aad.decode(),
            "plaintextSha256": plaintext_hash,
        }})

    monkeypatch.setattr("httpx.get", fake_get)
    monkeypatch.setattr("httpx.post", fake_post)
    monkeypatch.setattr(
        "hermes_cli.skill_marketplace._request_snft_browser_unlock_request",
        lambda **kwargs: unlock_request,
    )

    result = install_snft_from_metadata_url("https://issuer.example/snft/alpha/metadata.json")

    assert result["name"] == "alpha"
    assert seen["json"]["unlockRequest"] == unlock_request
    assert seen["json"]["proof"]["method"] == "eip712"


def test_marketplace_install_command_requires_skill_license(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    sink = StringIO()
    console = Console(file=sink, force_terminal=False, color_system=None)
    ctx = SimpleNamespace(
        wallet_address="wallet",
        can_use_custom_skills=False,
        can_create_skills=False,
        skill_licenses={},
        access_roles=[],
    )

    monkeypatch.setattr("agent.wallet.context.build_wallet_context", lambda: ctx)
    monkeypatch.setattr("hermes_cli.skill_marketplace.fetch_remote_listing", lambda name: {
        "skillId": "alpha",
        "name": "alpha",
        "accessPolicy": "skill_nft",
    })

    do_marketplace_install("alpha", console=console)

    assert "Access denied" in sink.getvalue()


def test_marketplace_install_command_suggests_force_for_scan_block(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    sink = StringIO()
    console = Console(file=sink, force_terminal=False, color_system=None)
    ctx = SimpleNamespace(
        wallet_address="wallet",
        can_use_custom_skills=False,
        can_create_skills=False,
        skill_licenses={"alpha": {"tier": "standard"}},
        access_roles=[],
    )

    monkeypatch.setattr("agent.wallet.context.build_wallet_context", lambda: ctx)
    monkeypatch.setattr("hermes_cli.skill_marketplace.fetch_remote_listing", lambda name: {
        "skillId": "alpha",
        "name": "alpha",
        "accessPolicy": "skill_nft",
    })

    def blocked_install(*args, **kwargs):
        raise ValueError("Security scan blocked install (Blocked):\nScan: alpha")

    monkeypatch.setattr("hermes_cli.skill_marketplace.install_marketplace_skill", blocked_install)

    do_marketplace_install("alpha", console=console)

    output = sink.getvalue()
    assert "/skills install-marketplace alpha --force" in output


def test_marketplace_buy_skips_when_license_already_owned(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    sink = StringIO()
    console = Console(file=sink, force_terminal=False, color_system=None)
    ctx = SimpleNamespace(
        wallet_address="wallet",
        skill_licenses={"alpha": {"tier": "standard"}},
    )

    monkeypatch.setattr("agent.wallet.context.build_wallet_context", lambda: ctx)

    do_marketplace_buy("alpha", console=console)

    assert "Already owned" in sink.getvalue()
    assert not list((tmp_path / "home" / "skills_marketplace" / "purchase_intents").glob("*.json"))


def test_marketplace_publish_admin_token_bypasses_creator_pair_gate(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    sink = StringIO()
    console = Console(file=sink, force_terminal=False, color_system=None)
    source_dir = tmp_path / "source" / "alpha"
    source_dir.mkdir(parents=True)
    (source_dir / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: Alpha skill\n---\n# Alpha\n",
        encoding="utf-8",
    )
    ctx = SimpleNamespace(
        wallet_address="wallet",
        can_create_skills=False,
        nfts=[],
        access_roles=[],
        matching_pair_numbers=[],
    )

    monkeypatch.setattr("agent.wallet.context.build_wallet_context", lambda: ctx)
    monkeypatch.setattr("hermes_cli.skill_marketplace.publish_listing_remote", lambda *args, **kwargs: {
        "listing": {"bundleUrl": "https://app.notpunks.com/api/skills/marketplace/alpha/bundle"}
    })

    from hermes_cli.skills_hub import do_marketplace_publish

    do_marketplace_publish(str(source_dir), remote=True, use_admin_token=True, console=console)

    output = sink.getvalue()
    assert "Publishing is locked" not in output
    assert "Remote marketplace published" in output


def test_publish_listing_remote_requires_token(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("NOTPUNKS_MARKETPLACE_PUBLISH_TOKEN", raising=False)
    monkeypatch.delenv("SKILL_MARKETPLACE_PUBLISH_SECRET", raising=False)
    source_dir = tmp_path / "source" / "alpha"
    source_dir.mkdir(parents=True)
    (source_dir / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: Alpha skill\n---\n# Alpha\n",
        encoding="utf-8",
    )
    listing = build_listing(source_dir, creator_wallet="wallet", price_ton=1)
    save_listing(listing)

    try:
        publish_listing_remote(listing)
    except RuntimeError as exc:
        assert "Remote publish token is not configured" in str(exc)
    else:
        raise AssertionError("publish_listing_remote should require token")


def test_publish_listing_remote_accepts_wallet_proof(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("NOTPUNKS_MARKETPLACE_PUBLISH_TOKEN", raising=False)
    monkeypatch.delenv("SKILL_MARKETPLACE_PUBLISH_SECRET", raising=False)
    source_dir = tmp_path / "source" / "alpha"
    source_dir.mkdir(parents=True)
    (source_dir / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: Alpha skill\n---\n# Alpha\n",
        encoding="utf-8",
    )
    listing = build_listing(source_dir, creator_wallet="wallet", price_ton=1)
    save_listing(listing)
    wallet_proof = {
        "walletAddress": "wallet",
        "publicKey": "pubkey",
        "proof": {"payload": publish_proof_payload(listing)},
    }

    class _Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"success": True, "data": {"listing": {"skillId": "alpha"}}}

    seen = {}

    def fake_post(url, json=None, headers=None, timeout=90):
        seen["url"] = url
        seen["json"] = json
        seen["headers"] = headers
        seen["timeout"] = timeout
        return _Response()

    monkeypatch.setattr("httpx.post", fake_post)

    data = publish_listing_remote(listing, wallet_proof=wallet_proof)

    assert data["listing"]["skillId"] == "alpha"
    assert seen["headers"] == {}
    assert seen["json"]["walletProof"] == wallet_proof
    assert seen["json"]["listing"]["bundle"]["archive_hash"].startswith("archive-sha256:")
