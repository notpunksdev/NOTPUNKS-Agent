"""Wallet context builder — assembles wallet + NFT state for agent consumption.

Integrates connector, NFT scanner, and skill mapper to produce a
``WalletContext`` that is injected into the agent's system prompt.
"""

from __future__ import annotations

import time
import json
import os
from typing import Any

from hermes_constants import get_hermes_home
from hermes_cli.config import load_config

from .connector import WalletConnector
from .models import NFTItem, WalletContext
from .nft_scanner import NFTScanner
from .skill_mapper import SkillMapper
from .storage import FileStorage

NOT_PUNKS_ADDR = "EQDP9nGW2Ho0V0_pbW8qpx2q3VJVd9n0BtbQjts2XqZIrfgF"
NOT_PUNKS_GIRLS_ADDR = "EQDJAXZJDQkBP6vCGn27vW00GIlUk_AQXnVtqNV0Tq2nFUyB"
ELEMENTAL_KIDS_ADDR = "EQCGbQyAJxxMsYQWLCklkXQq4fkIBK3kz3GA1TkFJyUR9nTH"
SKILL_NFT_COLLECTION_ADDR = "EQDwJtSrCsItP-B-XmhQkdnIrp-OTYluTeJNYGseyq-eR2Gd"
PREVIOUS_SKILL_NFT_COLLECTION_ADDR = "EQBzpBuYa5vGjSHumlC6rUsZKlErDN6eJ0V4mPc4KzC1SbQC"
LEGACY_SKILL_NFT_COLLECTION_ADDR = "EQCjjG07O4-FPUMvrD7W4otr0rq-Hcpuqk-u7EaRK2Ul6rKA"
SKILL_NFT_COLLECTION_ADDRS = {SKILL_NFT_COLLECTION_ADDR, PREVIOUS_SKILL_NFT_COLLECTION_ADDR, LEGACY_SKILL_NFT_COLLECTION_ADDR}


def build_wallet_context(config: dict | None = None, *, force_refresh: bool = False) -> WalletContext | None:
    """Build the full wallet + NFT context for the current user.

    Reads wallet state from config, scans NFTs if needed, and maps them
    to skill modes. Returns None if no wallet is connected.
    """
    if config is None:
        config = load_config()

    wallet_cfg = config.get("wallet", {})
    address = wallet_cfg.get("address")
    if not address:
        return None

    network = wallet_cfg.get("network", "mainnet")
    verified = wallet_cfg.get("verified", False)

    # Scan NFTs (uses cache unless stale)
    api_key = _wallet_secret(wallet_cfg, "toncenter_api_key", "TONCENTER_API_KEY", "TONCENTER_TOKEN")
    tonapi_key = _wallet_secret(wallet_cfg, "tonapi_key", "TONAPI_API_KEY", "TONAPI_TOKEN")
    scanner = NFTScanner(network=network, api_key=api_key, tonapi_key=tonapi_key)
    try:
        nfts = scanner.scan_address(address, force_refresh=force_refresh)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"NFT scan failed: {e}")
        nfts = []

    # NOT Punks holder collections live on mainnet. If a stale config says the
    # wallet network is testnet, still include the mainnet holder scan so the
    # closed agent gate does not false-lock a valid holder.
    if network != "mainnet":
        try:
            mainnet_scanner = NFTScanner(network="mainnet", api_key=api_key, tonapi_key=tonapi_key)
            holder_nfts = mainnet_scanner.scan_collections(
                address,
                [NOT_PUNKS_ADDR, NOT_PUNKS_GIRLS_ADDR, ELEMENTAL_KIDS_ADDR],
            )
            existing = {_nft_identity(nft) for nft in nfts}
            for nft in holder_nfts:
                ident = _nft_identity(nft)
                if ident not in existing:
                    existing.add(ident)
                    nfts.append(nft)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"NOT Punks mainnet holder scan failed: {e}")

    # During the Skill NFT rollout the license collection is deployed on
    # testnet while legacy holder collections remain on mainnet. Scan the
    # testnet license collection as an additive source so minted licenses are
    # visible without changing the user's main wallet network.
    if network != "testnet":
        try:
            testnet_scanner = NFTScanner(network="testnet", api_key=api_key, tonapi_key=tonapi_key)
            testnet_nfts = testnet_scanner.scan_address(address, force_refresh=True)
            nfts.extend(nft for nft in testnet_nfts if _is_skill_nft_collection(nft, config))
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"Skill NFT testnet scan failed: {e}")

    # Map to skill modes
    mapper = SkillMapper()
    modes = mapper.map_nfts_to_modes(nfts)

    # Check holder roles and matching NOT Punks + NOT Punks Girls pairs.
    access = build_nft_access(nfts, config)
    has_pairs = bool(access["matching_pair_numbers"])
    can_create_skills = bool(access["can_create_skills"])
    can_publish_skills = bool(access["can_publish_skills"])

    # Pairs unlock ALL skills (including premium) + marketplace publishing
    if has_pairs:
        premium_modes = mapper.get_premium_modes()
        existing_ids = {m.mode_id for m in modes}
        for pm in premium_modes:
            if pm.mode_id not in existing_ids:
                modes.append(pm)

    # Update verified flag based on NFT presence
    verified = verified or len(nfts) > 0

    return WalletContext(
        wallet_address=address,
        verified=verified,
        network=network,
        active_modes=modes,
        nfts=nfts,
        can_create_skills=can_create_skills,
        can_publish_skills=can_publish_skills,
        can_use_custom_skills=bool(access["can_use_custom_skills"]),
        skill_pass=bool(access["skill_pass"]),
        paid_skill_slots=int(access.get("paid_skill_slots") or 0),
        matching_pair_numbers=list(access["matching_pair_numbers"]),
        access_roles=list(access["access_roles"]),
        access_tiers=list(access.get("access_tiers") or []),
        skill_licenses=dict(access["skill_licenses"]),
    )


def build_not_punks_holder_context(config: dict | None = None, *, force_refresh: bool = False) -> WalletContext | None:
    """Build a lightweight wallet context containing only NOT Punks holder NFTs."""
    if config is None:
        config = load_config()

    wallet_cfg = config.get("wallet", {})
    address = wallet_cfg.get("address")
    if not address:
        return None

    wallet_network = wallet_cfg.get("network", "mainnet")
    api_key = _wallet_secret(wallet_cfg, "toncenter_api_key", "TONCENTER_API_KEY", "TONCENTER_TOKEN")
    tonapi_key = _wallet_secret(wallet_cfg, "tonapi_key", "TONAPI_API_KEY", "TONAPI_TOKEN")
    scanner = NFTScanner(network="mainnet", api_key=api_key, tonapi_key=tonapi_key)
    try:
        nfts = scanner.scan_collections(address, [NOT_PUNKS_ADDR])
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Fast NOT Punks scan failed: {e}")
        nfts = []

    access = build_nft_access(nfts, config)
    return WalletContext(
        wallet_address=address,
        verified=wallet_cfg.get("verified", False) or len(nfts) > 0,
        network=wallet_network,
        active_modes=[],
        nfts=nfts,
        can_create_skills=bool(access["can_create_skills"]),
        can_publish_skills=False,
        can_use_custom_skills=bool(access["can_use_custom_skills"]),
        skill_pass=False,
        paid_skill_slots=0,
        matching_pair_numbers=[],
        access_roles=list(access["access_roles"]),
        access_tiers=list(access.get("access_tiers") or []),
        skill_licenses={},
    )


def _nft_identity(nft: NFTItem) -> str:
    return str(getattr(nft, "address", "") or f"{getattr(nft, 'collection_address', '')}:{getattr(nft, 'index', '')}")


def wallet_context_has_not_punks(ctx: WalletContext | None) -> bool:
    """Return True when the wallet context proves ownership of a NOT Punks NFT."""
    return wallet_context_not_punks_count(ctx) > 0


def wallet_context_not_punks_count(ctx: WalletContext | None) -> int:
    """Return the number of verified NOT Punks NFTs detected in the wallet context."""
    if not ctx:
        return 0
    config = load_config()
    return sum(1 for nft in getattr(ctx, "nfts", []) or [] if _is_not_punks(nft, config))


def check_wallet_on_startup(
    console=None,
    prompt_connect: bool = False,
    require_not_punks: bool = False,
    reset_on_start: bool = False,
    force_refresh: bool = False,
    quiet: bool = False,
) -> WalletContext | None:
    """Check wallet status on agent startup and print status to console.

    Called from cli.py during HermesCLI.run() after the banner is shown.
    """
    config = load_config()
    wallet_cfg = config.get("wallet", {})
    if reset_on_start:
        had_wallet = _clear_persistent_wallet_session(config)
        wallet_cfg = config.get("wallet", {})
        if had_wallet and not quiet:
            _print_status(console, "wallet_disconnected", "◆ TON Wallet session reset for this startup. Reconnect to continue.")
    address = wallet_cfg.get("address")

    if not address:
        if not prompt_connect:
            if not quiet:
                _print_status(console, "wallet_disconnected",
                              "◆ TON Wallet: not connected. Run /wallet connect to unlock NOTPUNKS Agent.")
        else:
            if not quiet:
                _print_status(console, "wallet_disconnected",
                              "◆ TON Wallet: not connected. Starting wallet connect request...")
            try:
                from hermes_cli.wallet_commands import _wallet_connect_web

                _wallet_connect_web(console=console, blocking=False, force=True)
            except Exception:
                if not quiet:
                    _print_status(console, "wallet_disconnected",
                                  "Run /wallet connect to unlock NFT skills.")
        return None

    short_address = f"{address[:8]}...{address[-4:]}" if len(str(address)) > 12 else str(address)
    if not quiet:
        _print_status(
            console,
            "wallet_connected",
            f"◆ TON Wallet detected: {short_address}. To reconnect or change wallet, run /wallet connect.",
        )

    if require_not_punks:
        if not quiet:
            _print_status(console, "wallet_connected", "◆ Verifying NOT Punks NFT access...")
        ctx = build_not_punks_holder_context(config, force_refresh=force_refresh)
        not_punks_count = wallet_context_not_punks_count(ctx)
        if not_punks_count > 0:
            if not quiet:
                _print_status(console, "wallet_connected", f"◆ Access granted: verified NOT Punks holder ({not_punks_count} NOT Punks NFT(s) detected).")
            return ctx
        scanned_count = len(getattr(ctx, "nfts", []) or []) if ctx else 0
        if not quiet:
            _print_status(
                console,
                "wallet_disconnected",
                f"◆ Access locked: NOTPUNKS Agent requires at least one NOT Punks NFT from collection {NOT_PUNKS_ADDR}.",
            )
            _print_status(console, "wallet_disconnected", f"  NFT scan returned {scanned_count} configured NFT(s), but 0 verified NOT Punks.")
            _print_status(console, "wallet_disconnected", "  Skill NFT licenses do not unlock the agent gate. Connect a NOT Punks holder wallet with /wallet connect.")
        return ctx

    return WalletContext(
        wallet_address=address,
        verified=wallet_cfg.get("verified", False),
        network=wallet_cfg.get("network", "mainnet"),
        active_modes=[],
        nfts=[],
    )


def _clear_persistent_wallet_session(config: dict) -> bool:
    """Clear saved wallet config and TON Connect storage for a fresh startup session."""
    wallet_cfg = config.setdefault("wallet", {})
    had_wallet = bool(
        wallet_cfg.get("address")
        or wallet_cfg.get("public_key")
        or wallet_cfg.get("device_info")
        or wallet_cfg.get("connected_at")
    )

    try:
        storage = FileStorage()
        had_wallet = had_wallet or storage._path.exists()
        storage.clear()
    except Exception:
        pass

    wallet_cfg["address"] = None
    wallet_cfg["verified"] = False
    wallet_cfg["public_key"] = ""
    wallet_cfg["device_info"] = {}
    wallet_cfg["connected_at"] = 0.0
    config["wallet"] = wallet_cfg

    try:
        from hermes_cli.config import save_config

        save_config(config)
    except Exception:
        pass
    return had_wallet


def _wallet_secret(wallet_cfg: dict[str, Any], config_key: str, *env_names: str) -> str:
    """Resolve wallet API secrets from config fallback or environment."""
    value = str(wallet_cfg.get(config_key) or "").strip()
    if value:
        return value
    for env_name in env_names:
        value = str(os.getenv(env_name) or "").strip()
        if value:
            return value
    return ""


def _print_status(console, style_key: str, message: str) -> None:
    """Print a wallet status line using console if available, else plain print."""
    if console is not None and hasattr(console, "print"):
        try:
            from hermes_cli.skin_engine import get_active_skin
            skin = get_active_skin()
            color = skin.get(style_key, "")
            if color:
                console.print(f"[{color}]{message}[/{color}]")
                return
        except Exception:
            pass
        console.print(message)
    else:
        print(message)


def _nft_collection(nft: NFTItem) -> str:
    return (getattr(nft, "collection_address", "") or "").strip()


def _nft_name(nft: NFTItem) -> str:
    return (getattr(nft, "name", "") or "").strip()


def _metadata(nft: NFTItem) -> dict[str, Any]:
    data = getattr(nft, "metadata", {}) or {}
    return data if isinstance(data, dict) else {}


def _configured_collection_keys(collection_address: str, config: dict) -> set[str]:
    keys: set[str] = set()
    collections_cfg = config.get("nft", {}).get("collections", {})
    for key, cfg in collections_cfg.items():
        if (cfg.get("address", "") or "").strip() == collection_address:
            keys.add(str(key).lower())
    return keys


def _same_address(left: str, right: str) -> bool:
    left = (left or "").strip()
    right = (right or "").strip()
    if not left or not right:
        return False
    if left == right:
        return True
    try:
        from pytoniq_core import Address
        return Address(left).to_str(is_user_friendly=True) == Address(right).to_str(is_user_friendly=True)
    except Exception:
        return False


def _collection_key_has(collection_address: str, config: dict, *needles: str) -> bool:
    keys = _configured_collection_keys(collection_address, config)
    return any(any(needle in key for needle in needles) for key in keys)


def _is_not_punks(nft: NFTItem, config: dict) -> bool:
    coll = _nft_collection(nft)
    if _same_address(coll, NOT_PUNKS_ADDR):
        return True
    keys = _configured_collection_keys(coll, config)
    return any(key.startswith("not_punks_") and not key.startswith("not_punks_girls_") for key in keys)


def _is_not_punks_girl(nft: NFTItem, config: dict) -> bool:
    return _same_address(_nft_collection(nft), NOT_PUNKS_GIRLS_ADDR) or _collection_key_has(
        _nft_collection(nft), config, "not_punks_girls"
    )


def _is_elemental_kid(nft: NFTItem, config: dict) -> bool:
    coll = _nft_collection(nft)
    if coll == ELEMENTAL_KIDS_ADDR:
        return True
    if _collection_key_has(coll, config, "kid", "elemental", "skill_pass"):
        return True
    text = f"{_nft_name(nft)} {_metadata(nft).get('collection', '')}".lower()
    return "elemental" in text and "kid" in text


def _is_skill_nft_collection(nft: NFTItem, config: dict) -> bool:
    coll = _nft_collection(nft)
    if any(_same_address(coll, addr) for addr in SKILL_NFT_COLLECTION_ADDRS):
        return True
    return _collection_key_has(coll, config, "skill_nft", "notpunks_skill")


def _extract_nft_number(nft: NFTItem) -> int | None:
    import re

    for value in (_nft_name(nft), str(_metadata(nft).get("name", ""))):
        m = re.search(r"#\s*(\d+)", value or "")
        if m:
            return int(m.group(1))
    index = getattr(nft, "index", 0)
    return int(index) if index else None


def _item_index_matches(nft_index: int, mint_index: str) -> bool:
    try:
        nft_int = int(nft_index)
        mint_int = int(mint_index)
    except (TypeError, ValueError):
        return False
    if nft_int == mint_int:
        return True
    # Some indexers expose uint64 values as signed int64.
    return nft_int < 0 and nft_int + (1 << 64) == mint_int


def _license_from_local_mint_intent(nft: NFTItem) -> tuple[str, dict[str, Any]] | None:
    if os.getenv("SKILL_MARKETPLACE_DEV_LICENSE_FALLBACK") != "1" and os.getenv("SKILL_MARKETPLACE_DEV_MINT") != "1":
        return None
    intents_dir = get_hermes_home() / "skills_marketplace" / "mint_intents"
    if not intents_dir.exists():
        return None
    for path in sorted(intents_dir.glob("*.json"), reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        mint = data.get("mint") if isinstance(data.get("mint"), dict) else {}
        skill_id = str(data.get("skillId") or data.get("skill_id") or "").strip()
        if not skill_id:
            continue
        if not _same_address(str(mint.get("collectionAddress", "")), _nft_collection(nft)):
            continue
        if not _item_index_matches(getattr(nft, "index", 0), str(mint.get("itemIndex", ""))):
            continue
        return skill_id, {
            "nft_address": getattr(nft, "address", ""),
            "name": _nft_name(nft) or f"Skill NFT: {skill_id}",
            "collection_address": _nft_collection(nft),
            "tier": "standard",
            "source": "local_mint_intent",
        }
    return None


def _extract_skill_license(nft: NFTItem, config: dict | None = None) -> tuple[str, dict[str, Any]] | None:
    meta = _metadata(nft)
    skill_id = (
        meta.get("notpunks_skill_id")
        or meta.get("skill_id")
        or meta.get("skill")
        or meta.get("license_skill")
    )
    if not skill_id:
        if config and _is_skill_nft_collection(nft, config):
            return _license_from_local_mint_intent(nft)
        return None
    skill_id = str(skill_id).strip()
    if not skill_id:
        return None
    return skill_id, {
        "nft_address": getattr(nft, "address", ""),
        "name": _nft_name(nft),
        "collection_address": _nft_collection(nft),
        "tier": meta.get("tier") or meta.get("license_tier") or "standard",
    }


def _get_matching_pair_numbers(nfts: list[NFTItem], config: dict | None = None) -> list[int]:
    """Return matching NOT Punks + NOT Punks Girls numbers."""
    config = config or load_config()

    punk_nums: set[int] = set()
    girl_nums: set[int] = set()

    for nft in nfts:
        num = _extract_nft_number(nft)
        if num is None:
            continue
        if _is_not_punks_girl(nft, config):
            girl_nums.add(num)
        elif _is_not_punks(nft, config):
            punk_nums.add(num)

    return sorted(punk_nums & girl_nums)


def build_nft_access(nfts: list[NFTItem], config: dict | None = None) -> dict[str, Any]:
    """Derive marketplace access roles from wallet NFTs."""
    config = config or load_config()
    has_not_punks = any(_is_not_punks(nft, config) for nft in nfts)
    has_not_punks_girl = any(_is_not_punks_girl(nft, config) for nft in nfts)
    elemental_kid_count = sum(1 for nft in nfts if _is_elemental_kid(nft, config))
    has_elemental_kid = elemental_kid_count > 0
    matching_pair_numbers = _get_matching_pair_numbers(nfts, config)
    can_create_skills = has_not_punks
    can_publish_skills = bool(matching_pair_numbers)

    roles: list[str] = []
    tiers: list[str] = []
    if has_not_punks or has_not_punks_girl:
        roles.append("not_punks_holder")
    if has_not_punks:
        tiers.append("beta")
    if has_elemental_kid:
        roles.append("skill_pass_holder")
        tiers.append("skill_pass")
    if matching_pair_numbers:
        roles.append("skill_creator")
        tiers.append("creator")

    licenses: dict[str, dict[str, Any]] = {}
    for nft in nfts:
        license_item = _extract_skill_license(nft, config)
        if license_item:
            skill_id, payload = license_item
            licenses[skill_id] = payload
    if licenses:
        roles.append("skill_license_holder")
        tiers.append("licensed_skills")

    return {
        "can_create_skills": can_create_skills,
        "can_publish_skills": can_publish_skills,
        "can_use_custom_skills": bool(has_not_punks or has_not_punks_girl or has_elemental_kid),
        "skill_pass": has_elemental_kid,
        "paid_skill_slots": elemental_kid_count,
        "matching_pair_numbers": matching_pair_numbers,
        "access_roles": roles,
        "access_tiers": tiers,
        "skill_licenses": licenses,
    }


def _check_punk_pairs(nfts: list) -> bool:
    """Return True if user holds matching-number pairs of NOT Punks + NOT Punks Girls."""
    return bool(_get_matching_pair_numbers(nfts))


def format_context_for_prompt(ctx: WalletContext) -> str:
    """Format wallet context as a markdown section for the system prompt.

    This text is injected into the system prompt so the LLM knows which
    NFT-based modes are active and can adapt its behaviour.
    """
    lines: list[str] = []
    lines.append("## TON Wallet & NFT Access")
    lines.append(f"- Connected wallet: `{ctx.wallet_address}`")
    lines.append(f"- Network: {ctx.network}")
    lines.append(f"- Verified: {'yes' if ctx.verified else 'no'}")

    # Skill tier locks
    if getattr(ctx, "access_tiers", None):
        lines.append(f"- Access tiers: {', '.join(ctx.access_tiers)}")
    if ctx.can_create_skills:
        lines.append("- Local skill creation: **UNLOCKED** (NOT Punks beta holder)")
        lines.append("- Memory saving: **UNLOCKED**")
        lines.append("- You MAY offer to save complex workflows as skills and memories after difficult tasks.")
    else:
        lines.append("- Local skill creation: **LOCKED** (requires NOT Punks beta access)")
        lines.append("- Memory saving: **LOCKED**")
        lines.append("- IMPORTANT: Do NOT offer to create or save skills/memories.")
    if getattr(ctx, "can_publish_skills", False):
        lines.append("- Marketplace publishing: **UNLOCKED** (matching NOT Punks ♂️💎 + ♀️💎 creator pair found)")
        if ctx.matching_pair_numbers:
            nums = ", ".join(f"#{n}" for n in ctx.matching_pair_numbers)
            lines.append(f"- Matching creator pair(s): {nums}")
    else:
        lines.append("- Marketplace publishing: **LOCKED** (requires matching NOT Punks ♂️💎 + ♀️💎 pair with same number)")
    if getattr(ctx, "paid_skill_slots", 0):
        lines.append(f"- Paid Skill NFT indulgence slots: {ctx.paid_skill_slots} (1 Elemental Kid = 1 paid skill slot)")

    if ctx.can_use_custom_skills:
        roles = ", ".join(ctx.access_roles) if ctx.access_roles else "holder"
        lines.append(f"- Custom marketplace skills: **UNLOCKED** ({roles})")
    else:
        lines.append("- Custom marketplace skills: **LOCKED** (requires NOT Punks or Skill Pass NFT)")
    if ctx.skill_licenses:
        lines.append(f"- Skill NFT licenses owned: {', '.join(sorted(ctx.skill_licenses))}")

    # Categorise modes
    basic_modes = [m for m in ctx.active_modes if not m.mode_id.startswith("premium_")]
    premium_modes = [m for m in ctx.active_modes if m.mode_id.startswith("premium_")]

    if basic_modes:
        lines.append("")
        lines.append("### Basic Agent Modes (NOT Punks / NOT Punks Girls)")
        for mode in basic_modes:
            rarity = mode.nft_item.rarity if mode.nft_item else "common"
            level = mode.nft_item.level if mode.nft_item else 1
            lines.append(
                f"- **{mode.name}** ({mode.collection_key}, rarity={rarity}, level={level}): "
                f"{mode.description}"
            )

    if premium_modes:
        lines.append("")
        lines.append("### Premium Agent Modes (Elemental Kids / matching pair bonus)")
        for mode in premium_modes:
            rarity = mode.nft_item.rarity if mode.nft_item else "legendary"
            level = mode.nft_item.level if mode.nft_item else 1
            lines.append(
                f"- **{mode.name}** ({mode.collection_key}, rarity={rarity}, level={level}): "
                f"{mode.description}"
            )

    if not basic_modes and not premium_modes:
        lines.append("")
        lines.append("_No NFT-based modes are currently unlocked._")
    else:
        # Add prompt modifier instructions
        mapper = SkillMapper()
        modifier = mapper.get_prompt_modifier(ctx.active_modes)
        if modifier:
            lines.append("")
            lines.append("### Mode Instructions")
            lines.append(modifier)

    lines.append("")
    lines.append(
        "When responding, automatically apply ALL unlocked modes above. "
        "Do not ask the user for permission to use a mode that is already unlocked."
    )

    return "\n".join(lines)
