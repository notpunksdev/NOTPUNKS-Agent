"""NFT scanner for TON blockchain using TON Center API.

Supports both mainnet and testnet. Caches results locally with TTL.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

import requests

from hermes_cli.config import get_hermes_home, load_config

from .models import NFTItem


# TON Center API endpoints
TONCENTER_MAINNET = "https://toncenter.com/api/v3"
TONCENTER_TESTNET = "https://testnet.toncenter.com/api/v3"

# TonAPI fallback endpoints
TONAPI_MAINNET = "https://tonapi.io/v2"
TONAPI_TESTNET = "https://testnet.tonapi.io/v2"

# Cache TTL in seconds
CACHE_TTL = 600  # 10 minutes

# Skill NFT collection migrations should not break users with an older
# config.yaml; scanner keeps both the current and first rollout collection.
SKILL_NFT_COLLECTION_ADDRS = {
    "EQDwJtSrCsItP-B-XmhQkdnIrp-OTYluTeJNYGseyq-eR2Gd",
    "EQBzpBuYa5vGjSHumlC6rUsZKlErDN6eJ0V4mPc4KzC1SbQC",
    "EQCjjG07O4-FPUMvrD7W4otr0rq-Hcpuqk-u7EaRK2Ul6rKA",
}


class NFTScanner:
    """Scans TON blockchain for NFTs owned by a wallet address.

    Uses TON Center API v3 as primary source and falls back to TonAPI v2
    when rate-limited or unavailable.  Supports optional API keys for both.
    """

    def __init__(self, network: str = "mainnet", api_key: str = "", tonapi_key: str = ""):
        self.network = network
        self.api_key = api_key
        self.tonapi_key = tonapi_key
        self.base_url = TONCENTER_MAINNET if network == "mainnet" else TONCENTER_TESTNET
        self.tonapi_url = TONAPI_MAINNET if network == "mainnet" else TONAPI_TESTNET
        self._init_cache()

    def _init_cache(self) -> None:
        """Initialize SQLite cache for NFT scan results."""
        home = get_hermes_home()
        db_path = home / "state.db"
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS wallet_nfts (
                address TEXT PRIMARY KEY,
                collection_address TEXT,
                owner_address TEXT,
                index_value INTEGER,
                metadata TEXT,
                name TEXT,
                description TEXT,
                image TEXT,
                rarity TEXT,
                level INTEGER,
                scanned_at REAL
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS wallet_nft_scans (
                owner_address TEXT PRIMARY KEY,
                scanned_at REAL,
                count INTEGER
            )
        """)
        self._conn.commit()

    def _api_get(self, endpoint: str, params: dict | None = None, retries: int = 3) -> dict:
        """Make a GET request to TON Center API with exponential-backoff retry."""
        url = f"{self.base_url}{endpoint}"
        headers = {}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        last_exc: Exception | None = None
        for attempt in range(retries):
            try:
                resp = requests.get(url, params=params, headers=headers, timeout=30)
                resp.raise_for_status()
                return resp.json()
            except requests.HTTPError as e:
                last_exc = e
                if e.response.status_code in (429, 503, 502, 500):
                    delay = 2 ** attempt
                    time.sleep(delay)
                    continue
                raise
            except requests.RequestException as e:
                last_exc = e
                delay = 2 ** attempt
                time.sleep(delay)
                continue
        if last_exc:
            raise last_exc
        return {}

    def _normalize_address(self, addr: str) -> str:
        """Convert TON address to user-friendly bounceable format for comparison."""
        addr = addr.strip()
        if addr.startswith(("EQ", "UQ", "0:")):
            try:
                from pytoniq_core import Address
                return Address(addr).to_str(is_user_friendly=True)
            except Exception:
                pass
        return addr

    def scan_address(self, owner_address: str, force_refresh: bool = False) -> list[NFTItem]:
        """Scan all NFTs owned by the given wallet address.

        Uses cache unless force_refresh=True or cache is stale.
        """
        cache_owner = f"{self.network}:{owner_address}"
        # Check cache freshness
        if not force_refresh:
            cached = self._get_cached(cache_owner)
            if cached is not None:
                return cached

        # Filter by configured collections (normalize addresses for comparison)
        config = load_config()
        collections_cfg = config.get("nft", {}).get("collections", {})
        target_collections = {
            self._normalize_address(cfg.get("address", "")): key
            for key, cfg in collections_cfg.items()
        }
        for addr in SKILL_NFT_COLLECTION_ADDRS:
            target_collections[self._normalize_address(addr)] = "notpunks_skill_nft"

        # Fetch from API. In addition to the broad account scan, query target
        # collections directly through TonAPI. This avoids false locks when a
        # gateway/proxy truncates account NFT pagination or when TON Center
        # rejects unauthenticated owner scans.
        items = self._fetch_nfts(owner_address)
        items.extend(self._fetch_target_collection_nfts(owner_address, target_collections.keys()))

        filtered: list[NFTItem] = []
        seen_addresses: set[str] = set()
        for item in items:
            collection = self._normalize_address(item.collection_address)
            if collection in target_collections:
                item_key = self._normalize_address(getattr(item, "address", ""))
                if item_key in seen_addresses:
                    continue
                seen_addresses.add(item_key)
                # Enrich with metadata
                self._enrich_metadata(item)
                filtered.append(item)

        # Save to cache
        self._save_cache(cache_owner, filtered)
        return filtered

    def scan_collections(self, owner_address: str, collections: Any) -> list[NFTItem]:
        """Scan only the provided collections for an owner.

        This is the fast path for startup access gates where we only need to
        prove holder status and do not need the full marketplace license list.
        """
        normalized_targets = {
            self._normalize_address(str(value or "").strip())
            for value in collections
            if str(value or "").strip()
        }
        if not normalized_targets:
            return []

        filtered: list[NFTItem] = []
        seen_addresses: set[str] = set()
        items = self._fetch_target_collection_nfts(owner_address, normalized_targets)
        if not items:
            items = self._fetch_nfts(owner_address)
        for item in items:
            collection = self._normalize_address(item.collection_address)
            if collection not in normalized_targets:
                continue
            item_key = self._normalize_address(getattr(item, "address", ""))
            if item_key in seen_addresses:
                continue
            seen_addresses.add(item_key)
            self._enrich_metadata(item)
            filtered.append(item)
        return filtered

    def _fetch_nfts(self, owner_address: str) -> list[NFTItem]:
        """Fetch raw NFT items.

        Priority:
        1. TonAPI (fastest & most reliable when API key is present).
        2. TON Center API (fallback when TonAPI fails).
        """
        # Fast path: TonAPI is usually quickest and supports unauthenticated
        # public reads.  Fall back to TON Center when TonAPI is unavailable.
        try:
            return self._fetch_nfts_tonapi(owner_address)
        except Exception:
            pass  # fall through to TON Center

        # Fallback: TON Center API
        items: list[NFTItem] = []
        offset = 0
        limit = 256

        while True:
            try:
                data = self._api_get("/nft/items", {
                    "owner_address": owner_address,
                    "limit": limit,
                    "offset": offset,
                })
            except requests.HTTPError as e:
                code = e.response.status_code if e.response else 0
                if code in (429, 422, 500, 502, 503):
                    return []
                raise
            except requests.RequestException:
                return []

            nft_list = data.get("nft_items", [])
            if not nft_list:
                break

            for raw in nft_list:
                item = self._parse_nft_raw(raw)
                items.append(item)

            if len(nft_list) < limit:
                break
            offset += limit

        return items

    def _fetch_nfts_tonapi(self, owner_address: str) -> list[NFTItem]:
        """Fetch NFTs using TonAPI v2 (primary when key is available)."""
        return self._fetch_nfts_tonapi_page(owner_address)

    def _fetch_nfts_tonapi_page(self, owner_address: str, collection: str | None = None) -> list[NFTItem]:
        """Fetch NFTs using TonAPI v2, optionally narrowed to one collection."""
        url = f"{self.tonapi_url}/accounts/{owner_address}/nfts"
        headers = {}
        if self.tonapi_key:
            headers["Authorization"] = f"Bearer {self.tonapi_key}"
        items: list[NFTItem] = []
        offset = 0
        limit = 1000  # TonAPI supports up to 1000 per page

        while True:
            params = {"limit": limit, "offset": offset}
            if collection:
                params["collection"] = collection
            resp = requests.get(
                url,
                params=params,
                headers=headers,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            nft_list = data.get("nft_items", [])
            if not nft_list:
                break

            for raw in nft_list:
                item = self._parse_nft_raw(raw)
                items.append(item)

            if len(nft_list) < limit:
                break
            offset += limit

        return items

    def _fetch_target_collection_nfts(self, owner_address: str, collections: Any) -> list[NFTItem]:
        """Fetch configured collection NFTs directly when TonAPI is available."""
        if self.network != "mainnet":
            return []
        items: list[NFTItem] = []
        for collection in sorted({str(value or "").strip() for value in collections if str(value or "").strip()}):
            try:
                items.extend(self._fetch_nfts_tonapi_page(owner_address, collection=collection))
            except Exception:
                continue
        return items

    def _parse_nft_raw(self, raw: dict) -> NFTItem:
        """Parse a raw NFT dict (from TON Center or TonAPI) into NFTItem."""
        # TON Center uses flat strings; TonAPI nests collection/owner as objects
        collection = raw.get("collection", {})
        collection_address = (
            collection.get("address", "") if isinstance(collection, dict)
            else raw.get("collection_address", "")
        )
        owner = raw.get("owner", {})
        owner_address = (
            owner.get("address", "") if isinstance(owner, dict)
            else raw.get("owner_address", "")
        )
        item = NFTItem(
            address=self._normalize_address(raw.get("address", "")),
            collection_address=self._normalize_address(collection_address),
            owner_address=self._normalize_address(owner_address),
            index=raw.get("index", 0),
            metadata=raw.get("metadata", {}),
        )

        # TON Center API often returns metadata empty — fetch from content URI
        if not item.metadata:
            content = raw.get("content", {})
            uri = content.get("uri", "")
            if uri:
                item.metadata = self._fetch_metadata_from_uri(uri)

        # Pre-fill from metadata
        meta = item.metadata or {}
        item.name = meta.get("name", "")
        item.description = meta.get("description", "")
        item.image = meta.get("image", "")

        # Try to extract rarity/level/element from metadata attributes
        attrs = meta.get("attributes", [])
        for attr in attrs:
            trait = attr.get("trait_type", "").lower()
            value = attr.get("value", "")
            if trait in ("rarity", "rare"):
                item.rarity = str(value).lower()
            elif trait in ("level", "lvl"):
                try:
                    item.level = int(value)
                except (ValueError, TypeError):
                    pass
            elif trait == "element":
                # Store element in metadata for skill mapper
                item.metadata["_element"] = str(value).lower()

        return item

    def _fetch_metadata_from_uri(self, uri: str) -> dict:
        """Fetch NFT metadata from its content URI (IPFS or HTTP)."""
        try:
            resp = requests.get(uri, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except Exception:
            return {}

    def _enrich_metadata(self, item: NFTItem) -> None:
        """Fetch additional metadata for an NFT if needed."""
        if item.metadata:
            return
        try:
            data = self._api_get("/nft/items", {"address": item.address, "limit": 1})
            nft_list = data.get("nft_items", [])
            if nft_list:
                meta = nft_list[0].get("metadata", {})
                item.metadata = meta
                item.name = meta.get("name", "")
                item.description = meta.get("description", "")
                item.image = meta.get("image", "")
        except Exception:
            pass

    def _get_cached(self, owner_address: str) -> list[NFTItem] | None:
        """Return cached NFTs if they haven't expired."""
        cursor = self._conn.execute(
            "SELECT scanned_at FROM wallet_nft_scans WHERE owner_address = ?",
            (owner_address,),
        )
        row = cursor.fetchone()
        if row is None:
            return None

        scanned_at = row[0]
        if time.time() - scanned_at > CACHE_TTL:
            return None

        cursor = self._conn.execute(
            "SELECT * FROM wallet_nfts WHERE owner_address = ?",
            (owner_address,),
        )
        items = []
        for row in cursor.fetchall():
            item_address = row[0]
            prefix = f"{self.network}:"
            if item_address.startswith(prefix):
                item_address = item_address[len(prefix):]
            items.append(NFTItem(
                address=item_address,
                collection_address=row[1],
                owner_address=row[2],
                index=row[3],
                metadata=json.loads(row[4]) if row[4] else {},
                name=row[5],
                description=row[6],
                image=row[7],
                rarity=row[8],
                level=row[9],
            ))
        return items

    def _save_cache(self, owner_address: str, items: list[NFTItem]) -> None:
        """Save NFT scan results to cache."""
        # Clear old entries for this owner
        self._conn.execute("DELETE FROM wallet_nfts WHERE owner_address = ?", (owner_address,))
        self._conn.execute(
            "DELETE FROM wallet_nft_scans WHERE owner_address = ?",
            (owner_address,),
        )

        for item in items:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO wallet_nfts
                (address, collection_address, owner_address, index_value, metadata,
                 name, description, image, rarity, level, scanned_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"{self.network}:{item.address}",
                    item.collection_address,
                    item.owner_address,
                    item.index,
                    json.dumps(item.metadata),
                    item.name,
                    item.description,
                    item.image,
                    item.rarity,
                    item.level,
                    time.time(),
                ),
            )

        self._conn.execute(
            "INSERT INTO wallet_nft_scans (owner_address, scanned_at, count) VALUES (?, ?, ?)",
            (owner_address, time.time(), len(items)),
        )
        self._conn.commit()

    def clear_cache(self, owner_address: str | None = None) -> None:
        """Clear NFT cache for an owner or all owners."""
        if owner_address:
            self._conn.execute("DELETE FROM wallet_nfts WHERE owner_address = ?", (owner_address,))
            self._conn.execute(
                "DELETE FROM wallet_nft_scans WHERE owner_address = ?",
                (owner_address,),
            )
        else:
            self._conn.execute("DELETE FROM wallet_nfts")
            self._conn.execute("DELETE FROM wallet_nft_scans")
        self._conn.commit()
