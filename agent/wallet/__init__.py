"""TON Wallet + NFT Access module for NOTPUNKS Agent.

Provides TON Connect wallet integration, NFT scanning, and skill mode
unlocking based on NFT holdings.

Public API:
    WalletConnector     — TON Connect wallet connection
    NFTScanner          — TON blockchain NFT scanner
    SkillMapper         — NFT → skill mode mapping
    build_wallet_context — Build wallet context for prompt injection
    check_wallet_on_startup — Startup wallet status check
    format_context_for_prompt — Format wallet context for system prompt
"""

from .callback_server import CallbackServer
from .connector import WalletConnector, generate_qr_terminal, run_async
from .context import build_wallet_context, check_wallet_on_startup, format_context_for_prompt
from .models import NFTItem, SkillMode, WalletConnection, WalletContext
from .nft_scanner import NFTScanner
from .skill_mapper import SkillMapper
from .storage import FileStorage

__all__ = [
    "CallbackServer",
    "WalletConnector",
    "NFTScanner",
    "SkillMapper",
    "FileStorage",
    "WalletConnection",
    "NFTItem",
    "SkillMode",
    "WalletContext",
    "build_wallet_context",
    "check_wallet_on_startup",
    "format_context_for_prompt",
    "generate_qr_terminal",
    "run_async",
]
