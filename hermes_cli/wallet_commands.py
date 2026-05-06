"""CLI command handlers for ``notpunks wallet ...`` subcommands."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

from rich.console import Console
from hermes_constants import get_hermes_home
from hermes_cli.config import load_config
from hermes_cli.skin_engine import get_active_skin

from agent.wallet.connector import (
    TonConnectError,
    WalletConnector,
    generate_qr_terminal,
    run_async,
)
from agent.wallet.context import build_wallet_context
from agent.wallet.models import WalletConnection
from agent.wallet.nft_scanner import NFTScanner
from agent.wallet.skill_mapper import SkillMapper


def _skin_color(key: str, fallback: str = "") -> str:
    try:
        skin = get_active_skin()
        # SkinConfig uses .colors dict with .get_color() helper
        if hasattr(skin, "get_color"):
            return skin.get_color(key, fallback)
        return getattr(skin, "colors", {}).get(key, fallback)
    except Exception:
        return fallback


def _get_console(console=None):
    """Return the console to use — either the passed-in one or a fresh Rich Console."""
    if console is not None:
        return console
    return Console()


def cmd_wallet(args, _console=None) -> int:
    """Dispatch wallet subcommands.

    Args:
        args: argparse namespace with ``wallet_action``.
        _console: Optional Rich Console (or ChatConsole) for output.
                Used by slash commands so colours render inside prompt_toolkit.
    """
    action = getattr(args, "wallet_action", None)
    web = getattr(args, "web", False)
    console = _get_console(_console)

    if action == "connect":
        return _wallet_connect(console=console, web=web)
    elif action == "status":
        return _wallet_status(console=console)
    elif action == "disconnect":
        return _wallet_disconnect(console=console)
    elif action == "nft":
        return _wallet_nft(console=console)
    elif action == "access":
        return _wallet_access(console=console)
    else:
        console.print("[bold]Usage:[/bold] notpunks wallet <connect|status|disconnect|nft|access>")
        return 1


def _print_nft_status(console, ctx, config) -> None:
    """Print unlocked/locked skill modes and skill creation status."""
    from agent.wallet.skill_mapper import SkillMapper
    from agent.wallet.context import NOT_PUNKS_ADDR, wallet_context_not_punks_count

    color = _skin_color("wallet_connected", "green")
    collections_cfg = config.get("nft", {}).get("collections", {})
    not_punks_count = wallet_context_not_punks_count(ctx)

    console.print("")
    if not_punks_count > 0:
        console.print(f"[{color}]✓ Agent access UNLOCKED[/] — {not_punks_count} NOT Punks NFT(s) detected")
    else:
        console.print(
            "[yellow]⚠ Agent access LOCKED[/] — requires at least one verified "
            f"NOT Punks NFT from collection {NOT_PUNKS_ADDR}"
        )
        console.print("[dim]Skill NFT licenses unlock individual skills, but they do not unlock the closed agent gate.[/dim]")

    # Categorise modes
    basic_modes = [m for m in ctx.active_modes if not m.mode_id.startswith("premium_")]
    premium_modes = [m for m in ctx.active_modes if m.mode_id.startswith("premium_")]

    # Build full list of unique skill modes from config
    all_basic: dict[str, str] = {}
    all_premium: dict[str, str] = {}
    for key, cfg in collections_cfg.items():
        mode_id = cfg.get("skill_mode", key)
        desc = cfg.get("description", f"{key} mode")
        if cfg.get("premium", False):
            all_premium[mode_id] = desc
        else:
            all_basic[mode_id] = desc

    unlocked_basic_ids = {m.mode_id for m in basic_modes}
    unlocked_premium_ids = {m.mode_id for m in premium_modes}
    locked_basic_ids = set(all_basic.keys()) - unlocked_basic_ids
    locked_premium_ids = set(all_premium.keys()) - unlocked_premium_ids

    # Access tiers
    tiers = list(getattr(ctx, "access_tiers", []) or [])
    if tiers:
        console.print(f"[{color}]✓ Access tiers:[/] {', '.join(tiers)}")

    # Skill creation / memory / publishing
    if ctx.can_create_skills:
        console.print(f"[{color}]✓ Local skill creation & memory saving UNLOCKED[/] — NOT Punks beta holder")
    else:
        console.print("[yellow]⚠ Local skill creation & memory saving LOCKED[/] — requires NOT Punks beta access")

    if getattr(ctx, "can_publish_skills", False):
        suffix = ""
        if getattr(ctx, "matching_pair_numbers", None):
            suffix = " (" + ", ".join(f"#{n}" for n in ctx.matching_pair_numbers) + ")"
        console.print(f"[{color}]✓ Marketplace publishing UNLOCKED[/] — matching NOT Punks creator pair found{suffix}")
    else:
        console.print("[yellow]⚠ Marketplace publishing LOCKED[/] — requires a matching pair of NOT Punks ♂️💎 + NOT Punks Girls ♀️💎 with the same number")

    paid_slots = int(getattr(ctx, "paid_skill_slots", 0) or 0)
    if paid_slots:
        console.print(f"[{color}]✓ Paid skill slots:[/] {paid_slots} Elemental Kid indulgence slot(s)")

    if getattr(ctx, "can_use_custom_skills", False):
        roles = ", ".join(getattr(ctx, "access_roles", []) or ["holder"])
        console.print(f"[{color}]✓ Custom marketplace skills UNLOCKED[/] — {roles}")
    else:
        console.print("[yellow]⚠ Custom marketplace skills LOCKED[/] — requires NOT Punks or Skill Pass NFT")

    licenses = getattr(ctx, "skill_licenses", {}) or {}
    if licenses:
        console.print(f"[{color}]✓ Skill NFT licenses:[/] {', '.join(sorted(licenses))}")

    # Basic modes
    if basic_modes:
        console.print("")
        console.print(f"[{color}]✓ Basic Skill Modes ({len(basic_modes)}):[/]")
        for mode in basic_modes:
            console.print(f"  → [bold]{mode.name}[/bold]: {mode.description}")

    # Premium modes
    if premium_modes:
        console.print("")
        src = " (matching pair bonus)" if getattr(ctx, "can_publish_skills", False) else ""
        console.print(f"[{color}]✓ Premium Skill Modes ({len(premium_modes)}):{src}[/]")
        for mode in premium_modes:
            console.print(f"  ★ [bold]{mode.name}[/bold]: {mode.description}")

    # Locked basic modes
    if locked_basic_ids:
        console.print("")
        console.print("[dim]⚷ Basic modes — unlock with NOT Punks ♂️ or ♀️:[/dim]")
        for mode_id in sorted(locked_basic_ids):
            console.print(f"  ○ [dim]{mode_id.replace('_', ' ').title()}[/dim]: {all_basic[mode_id]}")

    # Locked premium modes
    if locked_premium_ids:
        console.print("")
        console.print("[dim]⚷ Premium modes — unlock with Elemental Kids or matching pair:[/dim]")
        for mode_id in sorted(locked_premium_ids):
            console.print(f"  ○ [dim]{mode_id.replace('_', ' ').title()}[/dim]: {all_premium[mode_id]}")

    # NFT holdings detail
    if ctx.nfts:
        console.print("")
        console.print(f"[bold]NFT Holdings:[/bold] {len(ctx.nfts)} item(s)")
        for nft in ctx.nfts[:10]:
            rarity_color = _skin_color(f"nft_rarity_{nft.rarity}", "white")
            console.print(
                f"  • [{rarity_color}]{nft.name or 'Unnamed NFT'}[/]"
                f" ({nft.collection_address[:6]}...{nft.collection_address[-4:]})")
        if len(ctx.nfts) > 10:
            console.print(f"  ... and {len(ctx.nfts) - 10} more")
    else:
        console.print("")
        console.print("[dim]No configured NFTs found in this wallet.[/dim]")


def _wallet_connect(console=None, blocking: bool = True, web: bool = False) -> int:
    """Interactive TON wallet connection flow.

    Args:
        console: Output console (Rich Console or ChatConsole).
        blocking: When True (CLI mode), blocks until wallet connects or times out.
                  When False (slash command), shows QR+link and returns immediately.
        web: When True, opens a browser page with TON Connect UI instead of QR.
    """
    if web:
        return _wallet_connect_web(console=console, blocking=blocking)

    console = _get_console(console)
    config = load_config()
    network = config.get("wallet", {}).get("network", "mainnet")

    connector = WalletConnector(network=network)

    # Check if already connected
    existing = connector.get_connected_wallet()
    if existing and existing.address:
        console.print(
            f"[{_skin_color('wallet_connected')}]"
            f"Wallet already connected: {existing.address}"
            f"[/{_skin_color('wallet_connected')}]"
        )
        return 0

    console.print("[bold]◆ NOTPUNKS TON Wallet Connect[/bold]")
    console.print("Scan the QR code with Tonkeeper or MyTonWallet to connect.\n")

    try:
        wallets = run_async(connector.get_wallets())
    except Exception as e:
        console.print(f"[red]Failed to fetch wallet list: {e}[/red]")
        return 1

    if not wallets:
        console.print("[red]No wallet apps available.[/red]")
        return 1

    # Use Tonkeeper as default wallet (first in preferred list)
    wallet_app = wallets[0]
    # Only show interactive picker when we have a real terminal stdin
    if len(wallets) > 1 and blocking and hasattr(sys.stdin, "isatty") and sys.stdin.isatty():
        console.print("Available wallets:")
        for i, w in enumerate(wallets[:5], 1):
            marker = " (default)" if i == 1 else ""
            console.print(f"  {i}. {w.get('name', 'Unknown')}{marker}")
        try:
            choice = console.input("Select wallet [1]: ").strip()
            if choice:
                idx = int(choice) - 1
                if 0 <= idx < len(wallets):
                    wallet_app = wallets[idx]
        except (ValueError, IndexError, EOFError):
            pass

    try:
        universal_url, payload = run_async(
            connector.generate_connect_url(wallet_app=wallet_app, with_proof=True)
        )
    except TonConnectError as e:
        console.print(f"[red]Failed to generate connect URL: {e}[/red]")
        return 1
    except Exception as e:
        console.print(f"[red]Unexpected error: {e}[/red]")
        return 1

    # Display QR code
    qr_str = generate_qr_terminal(universal_url)
    console.print("[bold]Scan QR with your wallet app:[/bold]")
    console.print(qr_str)
    console.print("")

    # Also show the link (plain text — avoids OSC 8 escape garbage in ChatConsole)
    console.print(f"[dim]Or open this link:[/dim]")
    console.print(universal_url)
    console.print("")

    if not blocking:
        # Non-blocking mode (slash command): return immediately
        console.print(
            "[yellow]⏳ Waiting for connection in background...\n"
            "   Open the link above in your browser, connect your wallet,\n"
            "   then type /wallet status to verify.[/yellow]"
        )
        # Start background thread to pick up the connection
        import threading

        def _bg_wait():
            try:
                bg_connector = WalletConnector(network=network)
                wallet = run_async(bg_connector.wait_for_connection(timeout=300.0))
                if wallet and wallet.address:
                    # Refresh context to pick up NFTs
                    build_wallet_context()
            except Exception:
                pass

        threading.Thread(target=_bg_wait, daemon=True, name="wallet-connect-wait").start()
        return 0

    # Blocking mode (CLI): wait for connection
    console.print("[yellow]Waiting for wallet connection... (timeout: 5 min)[/yellow]")

    try:
        wallet = run_async(connector.wait_for_connection(timeout=300.0))
    except KeyboardInterrupt:
        console.print("\n[red]Connection cancelled.[/red]")
        return 130
    except TonConnectError as e:
        console.print(f"[red]Connection error: {e}[/red]")
        return 1

    if wallet is None:
        console.print("[red]Connection timed out.[/red]")
        return 1

    # Success
    color = _skin_color("wallet_connected", "green")
    console.print(
        f"[{color}]✓ Wallet connected: {wallet.address}[/{color}]"
    )
    console.print(f"  Network: {wallet.network}")
    console.print(f"  Proof verified: {'yes' if wallet.verified else 'no (NFT scan will verify)'}")

    # Auto-scan NFTs
    console.print("[yellow]Scanning NFTs...[/yellow]")
    ctx = build_wallet_context()
    if ctx and ctx.nfts:
        console.print(f"[{color}]✓ Found {len(ctx.nfts)} NFT(s)[/{color}]")
        for mode in ctx.active_modes:
            console.print(f"  → [bold]{mode.name}[/bold] unlocked: {mode.description}")
    else:
        console.print("[dim]No configured NFTs found in this wallet.[/dim]")

    return 0


def _is_url_reachable(url: str, timeout: float = 3.0) -> bool:
    """Quick GET check to see if a remote URL is reachable."""
    try:
        import urllib.request

        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= int(getattr(resp, "status", 200)) < 500
    except Exception:
        return False


def _wait_for_tunnel_url(url: str, proc, timeout: float = 60.0) -> bool:
    """Wait until cloudflared has fully registered the quick tunnel URL."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        if _is_url_reachable(url, timeout=2.0):
            return True
        time.sleep(0.5)
    return False


def _drain_process_output(proc, max_lines: int = 4) -> str:
    """Read a small amount of cloudflared output for diagnostics."""
    lines: list[str] = []
    try:
        stdout = getattr(proc, "stdout", None)
        while stdout and len(lines) < max_lines:
            line = stdout.readline()
            if not line:
                break
            lines.append(line.strip())
    except Exception:
        pass
    return "\n".join(line for line in lines if line)


def _ensure_cloudflared() -> str:
    """Return path to cloudflared binary, downloading it if missing."""
    bin_dir = get_hermes_home() / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    cf_path = bin_dir / "cloudflared"
    if not cf_path.exists():
        import urllib.request

        url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"
        urllib.request.urlretrieve(url, str(cf_path))
        cf_path.chmod(0o755)
    return str(cf_path)


def _start_cloudflare_tunnel(local_port: int, timeout: float = 90.0) -> str:
    """Create a public HTTPS tunnel to the local callback server.

    Runs ``cloudflared tunnel --url http://127.0.0.1:<local_port>``,
    waits for the public URL to appear in stdout, and returns it.
    """
    import re
    import subprocess

    cf = _ensure_cloudflared()
    proc = subprocess.Popen(
        [cf, "tunnel", "--url", f"http://127.0.0.1:{local_port}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    deadline = time.time() + timeout
    url_pattern = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")
    while time.time() < deadline:
        if proc.poll() is not None:
            break
        line = proc.stdout.readline() if proc.stdout else ""
        if line:
            m = url_pattern.search(line)
            if m:
                url = m.group(0)
                if _wait_for_tunnel_url(url, proc, timeout=min(60.0, max(1.0, deadline - time.time()))):
                    return url, proc
                details = _drain_process_output(proc)
                proc.kill()
                raise RuntimeError(
                    "Cloudflare tunnel URL was issued but did not become reachable"
                    + (f": {details}" if details else "")
                )
        time.sleep(0.2)
    proc.kill()
    raise RuntimeError("Cloudflare tunnel did not start in time")


def _save_wallet_from_callback(data: dict, network: str) -> WalletConnection | None:
    """Persist wallet data received from the web callback page."""
    address = data.get("address", "")
    if not address:
        return None

    # Convert to user-friendly form if raw workchain:address
    try:
        from pytoniq_core import Address

        addr_obj = Address(address)
        address = addr_obj.to_str(is_user_friendly=True)
    except Exception:
        pass

    verified = bool(data.get("proof"))
    wallet = WalletConnection(
        address=address,
        network=network,
        verified=verified,
        public_key=data.get("public_key", ""),
        device_info=data.get("device_info", {}),
        connected_at=time.time(),
    )

    config = load_config()
    config.setdefault("wallet", {})
    config["wallet"]["address"] = wallet.address
    config["wallet"]["network"] = wallet.network
    config["wallet"]["verified"] = wallet.verified
    config["wallet"]["public_key"] = wallet.public_key
    config["wallet"]["device_info"] = wallet.device_info
    config["wallet"]["connected_at"] = wallet.connected_at
    from hermes_cli.config import save_config

    save_config(config)
    return wallet


def _hosted_wallet_session_url(session_id: str, payload: str) -> str:
    import urllib.parse

    params = {
        "session_id": session_id,
        "payload": payload,
    }
    return f"https://agent.notpunks.com/wallet-connect?{urllib.parse.urlencode(params)}"


def _poll_hosted_wallet_session(session_id: str, timeout: float = 300.0) -> dict | None:
    import urllib.error
    import urllib.request

    deadline = time.time() + timeout
    url = f"https://agent.notpunks.com/api/wallet-connect/sessions/{session_id}"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                data = json.loads(response.read().decode("utf-8"))
            if isinstance(data, dict) and data.get("status") == "connected":
                wallet = data.get("wallet")
                return wallet if isinstance(wallet, dict) else None
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            pass
        time.sleep(2)
    return None


def _wallet_connect_web(console=None, blocking: bool = True, force: bool = False) -> int:
    """Web-based TON wallet connection flow.

    Uses the hosted agent.notpunks.com wallet relay so remote servers do not
    depend on Cloudflare quick tunnels or localhost browser callbacks.

    Args:
        console: Output console.
        blocking: When True (CLI mode), blocks until wallet connects or times out.
                  When False (slash command), shows link and returns immediately.
    """
    console = _get_console(console)
    config = load_config()
    network = config.get("wallet", {}).get("network", "mainnet")

    # Check if already connected
    connector = WalletConnector(network=network)
    existing = connector.get_connected_wallet()
    if existing and existing.address and not force:
        console.print(
            f"[{_skin_color('wallet_connected')}]"
            f"Wallet already connected: {existing.address}"
            f"[/{_skin_color('wallet_connected')}]"
        )
        return 0

    console.print("[bold]◆ NOTPUNKS TON Wallet Connect (Web)[/bold]")
    console.print("Creating hosted wallet session on agent.notpunks.com...\n")

    import uuid as _uuid

    payload = f"notpunks-{_uuid.uuid4().hex[:16]}"
    session_id = _uuid.uuid4().hex

    web_url = _hosted_wallet_session_url(session_id, payload)

    # Show link
    console.print(f"[dim]Open this link in your browser:[/dim]")
    console.print(web_url)
    console.print("")

    # Try to open browser automatically
    try:
        import webbrowser

        webbrowser.open(web_url)
    except Exception:
        pass

    if not blocking:
        # Non-blocking mode (slash command): return immediately
        console.print(
            "[yellow]⏳ Waiting for connection in browser...\n"
            "   After connecting your wallet, type /wallet status to verify.[/yellow]"
        )
        import threading

        def _bg_wait():
            try:
                data = _poll_hosted_wallet_session(session_id, timeout=300.0)
                if data:
                    wallet = _save_wallet_from_callback(data, network)
                    if wallet:
                        build_wallet_context()
                        console.print(f"\n✓ Wallet connected: {wallet.address}", markup=False, highlight=False)
                        console.print("Run /wallet status to view NFT unlocks.", markup=False, highlight=False)
                    else:
                        console.print("\n[yellow]Wallet callback arrived without an address.[/yellow]")
                else:
                    console.print("\n[yellow]Wallet connection timed out. Run /wallet connect to try again.[/yellow]")
            except Exception as e:
                console.print(f"\n[red]Wallet connection failed: {e}[/red]")

        threading.Thread(target=_bg_wait, daemon=True, name="wallet-web-connect-wait").start()
        return 0

    # Blocking mode (CLI): wait for callback
    console.print("[yellow]⏳ Waiting for wallet connection in browser... (timeout: 5 min)[/yellow]")
    data = _poll_hosted_wallet_session(session_id, timeout=300.0)

    if data is None:
        console.print("[red]Connection timed out.[/red]")
        return 1

    wallet = _save_wallet_from_callback(data, network)
    if wallet is None:
        console.print("[red]No wallet address received from browser.[/red]")
        return 1

    # Success
    color = _skin_color("wallet_connected", "green")
    console.print(f"[{color}]✓ Wallet connected: {wallet.address}[/{color}]")
    console.print(f"  Proof verified: {'yes' if wallet.verified else 'no'}")

    # Auto-scan NFTs
    console.print("[yellow]Scanning NFTs...[/yellow]")
    ctx = build_wallet_context()
    if ctx:
        _print_nft_status(console, ctx, load_config())
    else:
        console.print("[dim]No wallet context available.[/dim]")

    return 0


def _wallet_status(console=None) -> int:
    """Show wallet and NFT status.

    Also tries to restore a connection from TON Connect storage
    (in case the user connected via QR/link in a previous slash command).
    """
    console = _get_console(console)
    config = load_config()
    wallet_cfg = config.get("wallet", {})
    address = wallet_cfg.get("address")

    # Try to restore connection from pytonconnect storage if no address in config
    if not address:
        try:
            from agent.wallet.connector import WalletConnector
            connector = WalletConnector(network=wallet_cfg.get("network", "mainnet"))
            restored = run_async(connector._ton_connect.restore_connection())
            if restored:
                wallet = connector.get_connected_wallet()
                if wallet and wallet.address:
                    address = wallet.address
                    # Save to config so it's persisted
                    connector._save_to_config()
        except Exception:
            pass

    if not address:
        console.print(
            f"[{_skin_color('wallet_disconnected', 'red')}]"
            "TON Wallet: not connected"
            f"[/{_skin_color('wallet_disconnected', 'red')}]"
        )
        console.print("Run [bold]notpunks wallet connect[/bold] to connect your wallet.")
        return 0

    # Show wallet info
    color = _skin_color("wallet_connected", "green")
    console.print(f"[{color}]◆ TON Wallet[/{color}]")
    console.print(f"  Address: [bold]{address}[/bold]")
    console.print(f"  Network: {wallet_cfg.get('network', 'mainnet')}")
    console.print(f"  Verified: {'yes' if wallet_cfg.get('verified') else 'no'}")

    connected_at = wallet_cfg.get("connected_at", 0)
    if connected_at:
        age = time.time() - connected_at
        if age < 3600:
            ago = f"{int(age // 60)} min ago"
        elif age < 86400:
            ago = f"{int(age // 3600)} hours ago"
        else:
            ago = f"{int(age // 86400)} days ago"
        console.print(f"  Connected: {ago}")

    # Show NFTs + skill status
    ctx = build_wallet_context(config)
    if ctx:
        _print_nft_status(console, ctx, config)
    else:
        console.print("")
        console.print("[dim]No wallet context available.[/dim]")

    return 0


def _wallet_disconnect(console=None) -> int:
    """Disconnect wallet and clear state."""
    console = _get_console(console)
    config = load_config()
    network = config.get("wallet", {}).get("network", "mainnet")

    connector = WalletConnector(network=network)
    run_async(connector.disconnect())

    console.print(
        f"[{_skin_color('wallet_disconnected', 'yellow')}]"
        "Wallet disconnected. All NFT-based modes are now locked."
        f"[/{_skin_color('wallet_disconnected', 'yellow')}]"
    )
    return 0


def _wallet_nft(console=None) -> int:
    """Force NFT re-scan and show results."""
    console = _get_console(console)
    config = load_config()
    wallet_cfg = config.get("wallet", {})
    address = wallet_cfg.get("address")

    if not address:
        console.print("[red]No wallet connected. Run [bold]notpunks wallet connect[/bold] first.[/red]")
        return 1

    network = wallet_cfg.get("network", "mainnet")
    from agent.wallet.context import _wallet_secret

    api_key = _wallet_secret(wallet_cfg, "toncenter_api_key", "TONCENTER_API_KEY", "TONCENTER_TOKEN")
    tonapi_key = _wallet_secret(wallet_cfg, "tonapi_key", "TONAPI_API_KEY", "TONAPI_TOKEN")

    console.print(f"[yellow]Scanning NFTs for {address[:8]}...{address[-4:]} on {network}...[/yellow]")

    scanner = NFTScanner(network=network, api_key=api_key, tonapi_key=tonapi_key)
    nfts = scanner.scan_address(address, force_refresh=True)

    mapper = SkillMapper()
    modes = mapper.map_nfts_to_modes(nfts)

    # Check for matching pairs (same logic as build_wallet_context)
    from agent.wallet.context import build_nft_access
    access = build_nft_access(nfts, config)
    has_pairs = bool(access["matching_pair_numbers"])
    if has_pairs:
        premium_modes = mapper.get_premium_modes()
        existing_ids = {m.mode_id for m in modes}
        for pm in premium_modes:
            if pm.mode_id not in existing_ids:
                modes.append(pm)

    if nfts:
        color = _skin_color("wallet_connected", "green")
        console.print(f"[{color}]✓ Found {len(nfts)} NFT(s)[/{color}]")
        for nft in nfts:
            rarity_color = _skin_color(f"nft_rarity_{nft.rarity}", "white")
            console.print(
                f"  • [{rarity_color}]{nft.name or 'Unnamed'}[/]"
                f" — collection {nft.collection_address[:8]}..., "
                f"index={nft.index}, rarity={nft.rarity}, level={nft.level}"
            )

        basic = [m for m in modes if not m.mode_id.startswith("premium_")]
        premium = [m for m in modes if m.mode_id.startswith("premium_")]

        if basic:
            console.print("")
            console.print("[bold]Basic Modes:[/bold]")
            for mode in basic:
                console.print(f"  ✓ {mode.name}: {mode.description}")

        if premium:
            console.print("")
            src = " (matching pair bonus)" if has_pairs else ""
            console.print(f"[bold]Premium Modes:{src}[/bold]")
            for mode in premium:
                console.print(f"  ★ {mode.name}: {mode.description}")

        if not basic and not premium:
            console.print("")
            console.print("[dim]NFTs found but none match configured collections.[/dim]")

        if has_pairs:
            console.print("")
            nums = ", ".join(f"#{n}" for n in access["matching_pair_numbers"])
            console.print(f"[{color}]✓ Matching pair detected ({nums}) — marketplace publishing UNLOCKED[/]")
        if access.get("can_create_skills"):
            console.print(f"[{color}]✓ Local skill creation & memory saving UNLOCKED[/] — NOT Punks beta holder")
        if access.get("paid_skill_slots"):
            console.print(f"[{color}]✓ Paid skill slots:[/] {access['paid_skill_slots']}")
        if access["can_use_custom_skills"]:
            console.print(f"[{color}]✓ Custom marketplace skills UNLOCKED[/] — {', '.join(access['access_roles'])}")
    else:
        console.print("[dim]No NFTs found for configured collections.[/dim]")

    return 0


def _wallet_access(console=None) -> int:
    """Show wallet roles used by Skill NFT marketplace access checks."""
    console = _get_console(console)
    ctx = build_wallet_context(load_config())
    if not ctx:
        console.print("[red]No wallet connected. Run [bold]notpunks wallet connect[/bold] first.[/red]")
        return 1

    color = _skin_color("wallet_connected", "green")
    console.print(f"[{color}]◆ Skill NFT Access[/{color}]")
    console.print(f"  Wallet: [bold]{ctx.wallet_address}[/bold]")
    console.print(f"  Network: {ctx.network}")
    console.print(f"  Custom skills: {'unlocked' if ctx.can_use_custom_skills else 'locked'}")
    console.print(f"  Local skill creation: {'unlocked' if ctx.can_create_skills else 'locked'}")
    console.print(f"  Marketplace publishing: {'unlocked' if getattr(ctx, 'can_publish_skills', False) else 'locked'}")
    console.print(f"  Skill Pass: {'yes' if ctx.skill_pass else 'no'}")
    if getattr(ctx, "paid_skill_slots", 0):
        console.print(f"  Paid skill slots: {ctx.paid_skill_slots}")
    if ctx.matching_pair_numbers:
        console.print(f"  Matching pairs: {', '.join(f'#{n}' for n in ctx.matching_pair_numbers)}")
    if getattr(ctx, "access_tiers", None):
        console.print(f"  Tiers: {', '.join(ctx.access_tiers)}")
    if ctx.access_roles:
        console.print(f"  Roles: {', '.join(ctx.access_roles)}")
    if ctx.skill_licenses:
        console.print("  Skill NFT licenses:")
        for skill_id, license_data in sorted(ctx.skill_licenses.items()):
            tier = license_data.get("tier", "standard")
            name = license_data.get("name", "")
            console.print(f"    • {skill_id} ({tier}) {name}")
    else:
        console.print("  Skill NFT licenses: none")
    return 0
