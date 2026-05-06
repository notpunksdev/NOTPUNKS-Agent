from agent.wallet.context import (
    ELEMENTAL_KIDS_ADDR,
    LEGACY_SKILL_NFT_COLLECTION_ADDR,
    NOT_PUNKS_ADDR,
    NOT_PUNKS_GIRLS_ADDR,
    SKILL_NFT_COLLECTION_ADDR,
    build_nft_access,
    _wallet_secret,
)
from agent.wallet.models import NFTItem
from agent.wallet import nft_scanner
from agent.wallet.nft_scanner import NFTScanner


def _nft(name, collection, metadata=None, index=0):
    return NFTItem(
        address=f"addr-{name}",
        collection_address=collection,
        owner_address="owner",
        name=name,
        metadata=metadata or {},
        index=index,
    )


def test_nft_scanner_normalizes_bounceable_and_non_bounceable_addresses():
    from pytoniq_core import Address

    scanner = NFTScanner.__new__(NFTScanner)
    non_bounceable = Address(NOT_PUNKS_ADDR).to_str(is_user_friendly=True, is_bounceable=False)

    assert non_bounceable.startswith("UQ")
    assert scanner._normalize_address(non_bounceable) == NOT_PUNKS_ADDR


def test_nft_scanner_uses_public_tonapi_without_api_key(monkeypatch):
    scanner = NFTScanner.__new__(NFTScanner)
    calls = []
    expected = [_nft("NOT Punks #1", NOT_PUNKS_ADDR)]

    def fetch_tonapi(owner_address):
        calls.append(("tonapi", owner_address))
        return expected

    monkeypatch.setattr(scanner, "_fetch_nfts_tonapi", fetch_tonapi)

    assert scanner._fetch_nfts("owner") == expected
    assert calls == [("tonapi", "owner")]


def test_nft_scanner_adds_target_collection_fetch(monkeypatch):
    scanner = NFTScanner.__new__(NFTScanner)
    scanner.network = "mainnet"

    monkeypatch.setattr(nft_scanner, "load_config", lambda: {
        "nft": {"collections": {"not_punks_legal": {"address": NOT_PUNKS_ADDR}}}
    })
    monkeypatch.setattr(scanner, "_get_cached", lambda owner: None)
    monkeypatch.setattr(scanner, "_fetch_nfts", lambda owner: [])
    monkeypatch.setattr(scanner, "_fetch_target_collection_nfts", lambda owner, collections: [
        _nft("NOT Punks #1", "0:cff67196d87a34574fe96d6f2aa71daadd525577d9f406d6d08edb365ea648ad")
    ])
    monkeypatch.setattr(scanner, "_enrich_metadata", lambda item: None)
    monkeypatch.setattr(scanner, "_save_cache", lambda owner, items: None)

    result = scanner.scan_address("owner", force_refresh=True)

    assert len(result) == 1
    assert result[0].name == "NOT Punks #1"


def test_nft_scanner_scan_collections_uses_only_targeted_fetch(monkeypatch):
    scanner = NFTScanner.__new__(NFTScanner)
    scanner.network = "mainnet"
    calls = []

    def target_fetch(owner, collections):
        calls.append((owner, tuple(collections)))
        return [_nft("NOT Punks #1", "0:cff67196d87a34574fe96d6f2aa71daadd525577d9f406d6d08edb365ea648ad")]

    monkeypatch.setattr(scanner, "_fetch_nfts", lambda owner: (_ for _ in ()).throw(RuntimeError("broad scan should not run")))
    monkeypatch.setattr(scanner, "_fetch_target_collection_nfts", target_fetch)
    monkeypatch.setattr(scanner, "_enrich_metadata", lambda item: None)

    result = scanner.scan_collections("owner", [NOT_PUNKS_ADDR])

    assert len(result) == 1
    assert result[0].name == "NOT Punks #1"
    assert calls


def test_wallet_secret_prefers_config_then_env(monkeypatch):
    monkeypatch.setenv("TONAPI_API_KEY", "from-env")

    assert _wallet_secret({}, "tonapi_key", "TONAPI_API_KEY") == "from-env"
    assert _wallet_secret({"tonapi_key": "from-config"}, "tonapi_key", "TONAPI_API_KEY") == "from-config"


def test_matching_not_punks_pair_unlocks_creator_role():
    access = build_nft_access([
        _nft("NOT Punk #42", NOT_PUNKS_ADDR),
        _nft("NOT Punk Girl #42", NOT_PUNKS_GIRLS_ADDR),
    ], config={"nft": {"collections": {}}})

    assert access["can_use_custom_skills"] is True
    assert access["can_create_skills"] is True
    assert access["can_publish_skills"] is True
    assert access["matching_pair_numbers"] == [42]
    assert "skill_creator" in access["access_roles"]
    assert "creator" in access["access_tiers"]


def test_not_punks_raw_collection_address_unlocks_holder_role():
    access = build_nft_access([
        _nft("NOT Punks #42", "0:cff67196d87a34574fe96d6f2aa71daadd525577d9f406d6d08edb365ea648ad"),
    ], config={"nft": {"collections": {}}})

    assert access["can_use_custom_skills"] is True
    assert access["can_create_skills"] is True
    assert access["can_publish_skills"] is False
    assert "not_punks_holder" in access["access_roles"]
    assert "beta" in access["access_tiers"]


def test_matching_pair_uses_girls_config_key_as_girl_collection():
    config = {"nft": {"collections": {
        "not_punks_legal": {"address": "punk-collection"},
        "not_punks_girls_legal": {"address": "girl-collection"},
    }}}
    access = build_nft_access([
        _nft("NOT Punk #9", "punk-collection"),
        _nft("NOT Punk Girl #9", "girl-collection"),
    ], config=config)

    assert access["matching_pair_numbers"] == [9]
    assert access["can_publish_skills"] is True
    assert "skill_creator" in access["access_roles"]


def test_elemental_kid_unlocks_skill_pass_role():
    access = build_nft_access([
        _nft("TNO Elemental Kids #7", ELEMENTAL_KIDS_ADDR),
    ], config={"nft": {"collections": {}}})

    assert access["can_use_custom_skills"] is True
    assert access["can_create_skills"] is False
    assert access["can_publish_skills"] is False
    assert access["skill_pass"] is True
    assert access["paid_skill_slots"] == 1
    assert "skill_pass_holder" in access["access_roles"]
    assert "skill_pass" in access["access_tiers"]


def test_skill_license_metadata_is_exposed_by_skill_id():
    access = build_nft_access([
        _nft("Skill NFT: sales-pro", "collection", {"skill_id": "sales-pro", "tier": "pro"}),
    ], config={"nft": {"collections": {}}})

    assert "skill_license_holder" in access["access_roles"]
    assert access["skill_licenses"]["sales-pro"]["tier"] == "pro"


def test_skill_license_can_be_recovered_from_local_mint_intent(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("SKILL_MARKETPLACE_DEV_LICENSE_FALLBACK", "1")
    intents = tmp_path / "home" / "skills_marketplace" / "mint_intents"
    intents.mkdir(parents=True)
    (intents / "deploy-bot.json").write_text(
        f"""{{
  "skillId": "deploy-bot",
  "mint": {{
    "collectionAddress": "{LEGACY_SKILL_NFT_COLLECTION_ADDR}",
    "itemIndex": "10085262536751077822"
  }}
}}
""",
        encoding="utf-8",
    )

    access = build_nft_access([
        _nft("Skill NFT", LEGACY_SKILL_NFT_COLLECTION_ADDR, {}, index=-8361481536958473794),
    ], config={"nft": {"collections": {
        "notpunks_skill_nft": {"address": SKILL_NFT_COLLECTION_ADDR},
    }}})

    assert "skill_license_holder" in access["access_roles"]
    assert access["skill_licenses"]["deploy-bot"]["source"] == "local_mint_intent"


def test_local_mint_intent_license_requires_dev_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("SKILL_MARKETPLACE_DEV_LICENSE_FALLBACK", raising=False)
    monkeypatch.delenv("SKILL_MARKETPLACE_DEV_MINT", raising=False)
    intents = tmp_path / "home" / "skills_marketplace" / "mint_intents"
    intents.mkdir(parents=True)
    (intents / "deploy-bot.json").write_text(
        f"""{{
  "skillId": "deploy-bot",
  "mint": {{
    "collectionAddress": "{LEGACY_SKILL_NFT_COLLECTION_ADDR}",
    "itemIndex": "10085262536751077822"
  }}
}}
""",
        encoding="utf-8",
    )

    access = build_nft_access([
        _nft("Skill NFT", LEGACY_SKILL_NFT_COLLECTION_ADDR, {}, index=-8361481536958473794),
    ], config={"nft": {"collections": {
        "notpunks_skill_nft": {"address": SKILL_NFT_COLLECTION_ADDR},
    }}})

    assert "skill_license_holder" not in access["access_roles"]
    assert "deploy-bot" not in access["skill_licenses"]
