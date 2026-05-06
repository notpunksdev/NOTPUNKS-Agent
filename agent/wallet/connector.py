"""TON Connect wallet connector using pytonconnect.

Implements full TON Connect v2 protocol with:
- Universal link generation for wallet apps
- HTTP bridge (SSE polling) for wallet events
- ton_proof cryptographic verification
- Persistent session storage
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

import qrcode
from pytonconnect import TonConnect, WalletsListManager
from pytonconnect.crypto import SessionCrypto
from pytonconnect.exceptions import TonConnectError

from hermes_cli.config import get_hermes_home, load_config, save_config

from .models import WalletConnection
from .storage import FileStorage


# TON Connect manifest for the NOTPUNKS Agent dApp
MANIFEST_URL = "https://app.notpunks.com/tonconnect-manifest.json"

# Fallback inline manifest (used when remote manifest unavailable)
MANIFEST_INLINE = {
    "url": "https://app.notpunks.com",
    "name": "NOTPUNKS Agent",
    "iconUrl": "https://app.notpunks.com/favicon.ico",
}

# Supported wallet apps (ordered by preference)
PREFERRED_WALLETS = ["tonkeeper", "mytonwallet", "telegram-wallet"]


class WalletConnector:
    """Handles TON Connect wallet connection, proof verification, and session management."""

    def __init__(self, network: str = "mainnet"):
        self.network = network
        self._storage = FileStorage()
        self._ton_connect: TonConnect | None = None
        self._wallet_info: WalletConnection | None = None
        self._connect_payload: str = ""

    def _get_manifest_url(self) -> str:
        """Return the manifest URL for TON Connect.

        Creates a local manifest file in the user's NOTPUNKS home directory
        if it doesn't exist, so TON Connect works offline.
        """
        home = get_hermes_home()
        local_manifest = home / "tonconnect-manifest.json"
        if not local_manifest.exists():
            import json
            local_manifest.write_text(
                json.dumps(MANIFEST_INLINE, indent=2),
                encoding="utf-8",
            )
        return local_manifest.as_uri()

    def _init_ton_connect(self) -> TonConnect:
        """Initialize pytonconnect TonConnect instance."""
        manifest = self._get_manifest_url()
        api_tokens = {}
        if self.network == "mainnet":
            api_tokens = {"tonapi": "", "toncenter": ""}
        return TonConnect(
            manifest_url=manifest,
            storage=self._storage,
            api_tokens=api_tokens,
        )

    async def get_wallets(self) -> list[dict]:
        """Return list of supported wallet apps."""
        manager = WalletsListManager()
        wallets = manager.get_wallets()
        # Sort by preference
        ordered = []
        for preferred in PREFERRED_WALLETS:
            for w in wallets:
                if w.get("app_name") == preferred:
                    ordered.append(w)
                    break
        # Append remaining wallets
        for w in wallets:
            if w not in ordered:
                ordered.append(w)
        return ordered

    async def generate_connect_url(
        self,
        wallet_app: dict | None = None,
        with_proof: bool = True,
    ) -> tuple[str, str]:
        """Generate a TON Connect universal URL and QR payload.

        Returns:
            (universal_url, connect_payload) — the payload is the string that
            should be embedded in the QR code.
        """
        self._ton_connect = self._init_ton_connect()

        # Generate a unique payload for ton_proof
        self._connect_payload = f"notpunks-{uuid.uuid4().hex[:16]}"
        request = None
        if with_proof:
            request = {
                "tonProof": self._connect_payload,
            }

        if wallet_app is None:
            wallets = await self.get_wallets()
            if not wallets:
                raise TonConnectError("No wallet apps available")
            wallet_app = wallets[0]

        universal_url = await self._ton_connect.connect(wallet_app, request)
        return universal_url, self._connect_payload

    async def wait_for_connection(self, timeout: float = 300.0) -> WalletConnection | None:
        """Wait for the wallet to connect via the TON Connect bridge.

        Uses pytonconnect's event listener and asyncio timeout.
        """
        if self._ton_connect is None:
            raise TonConnectError("TonConnect not initialized. Call generate_connect_url first.")

        try:
            wallet_info = await asyncio.wait_for(
                self._ton_connect.wait_for_connection(),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            return None

        if isinstance(wallet_info, Exception):
            raise TonConnectError(f"Connection error: {wallet_info}")

        # Verify ton_proof if requested
        verified = False
        if wallet_info.ton_proof is not None:
            verified = wallet_info.check_proof(self._connect_payload)

        # Build our WalletConnection model
        account = wallet_info.account
        address = f"{account.workchain}:{account.address}"
        # Convert to user-friendly form if possible
        try:
            from pytoniq_core import Address
            addr_obj = Address(address)
            address = addr_obj.to_str(is_user_friendly=True)
        except Exception:
            pass

        self._wallet_info = WalletConnection(
            address=address,
            network=self.network,
            verified=verified,
            public_key=account.public_key or "",
            device_info={
                "platform": wallet_info.device.platform if wallet_info.device else "",
                "app_name": wallet_info.device.app_name if wallet_info.device else "",
                "app_version": wallet_info.device.app_version if wallet_info.device else "",
            },
            connected_at=time.time(),
        )

        # Persist to config
        self._save_to_config()

        return self._wallet_info

    def _save_to_config(self) -> None:
        """Save wallet connection to user config."""
        if self._wallet_info is None:
            return
        config = load_config()
        config.setdefault("wallet", {})
        config["wallet"]["address"] = self._wallet_info.address
        config["wallet"]["network"] = self._wallet_info.network
        config["wallet"]["verified"] = self._wallet_info.verified
        config["wallet"]["public_key"] = self._wallet_info.public_key
        config["wallet"]["device_info"] = self._wallet_info.device_info
        config["wallet"]["connected_at"] = self._wallet_info.connected_at
        save_config(config)

    def load_from_config(self) -> WalletConnection | None:
        """Load wallet connection from user config."""
        config = load_config()
        wallet_cfg = config.get("wallet", {})
        address = wallet_cfg.get("address")
        if not address:
            return None

        self._wallet_info = WalletConnection(
            address=address,
            network=wallet_cfg.get("network", "mainnet"),
            verified=wallet_cfg.get("verified", False),
            public_key=wallet_cfg.get("public_key", ""),
            device_info=wallet_cfg.get("device_info", {}),
            connected_at=wallet_cfg.get("connected_at", 0.0),
        )
        return self._wallet_info

    async def disconnect(self) -> None:
        """Disconnect wallet and clear all state."""
        if self._ton_connect is not None:
            try:
                await self._ton_connect.disconnect()
            except Exception:
                pass
            self._ton_connect = None

        self._storage.clear()
        self._wallet_info = None

        # Clear config
        config = load_config()
        if "wallet" in config:
            config["wallet"]["address"] = None
            config["wallet"]["verified"] = False
            config["wallet"]["public_key"] = ""
            config["wallet"]["device_info"] = {}
            config["wallet"]["connected_at"] = 0
        save_config(config)

    def get_connected_wallet(self) -> WalletConnection | None:
        """Return currently connected wallet (from memory or config)."""
        if self._wallet_info is not None:
            return self._wallet_info
        return self.load_from_config()

    @property
    def is_connected(self) -> bool:
        """Check if a wallet is connected."""
        wallet = self.get_connected_wallet()
        return wallet is not None and bool(wallet.address)


def generate_qr_terminal(data: str, style: str = "notpunks") -> str:
    """Generate a terminal-friendly QR code string.

    Uses Unicode block characters for high-density output.
    """
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=1,
        border=2,
    )
    qr.add_data(data)
    qr.make(fit=True)

    # Use Unicode half-blocks for compact terminal output
    lines = []
    module_count = qr.modules_count
    for row in range(0, module_count, 2):
        line = ""
        for col in range(module_count):
            top = qr.modules[row][col]
            bottom = qr.modules[row + 1][col] if row + 1 < module_count else False
            if top and bottom:
                line += "█"
            elif top and not bottom:
                line += "▀"
            elif not top and bottom:
                line += "▄"
            else:
                line += " "
        lines.append(line)
    return "\n".join(lines)


def run_async(coro):
    """Helper to run async code from sync context."""
    try:
        loop = asyncio.get_running_loop()
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)
