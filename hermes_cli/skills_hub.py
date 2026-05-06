#!/usr/bin/env python3
"""
Skills Hub CLI — Unified interface for the Hermes Skills Hub.

Powers both:
  - `notpunks skills <subcommand>` (CLI argparse entry point)
  - `/skills <subcommand>` (slash command in the interactive chat)

All logic lives in shared do_* functions. The CLI entry point and slash command
handler are thin wrappers that parse args and delegate.
"""

import json
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# Lazy imports to avoid circular dependencies and slow startup.
# tools.skills_hub and tools.skills_guard are imported inside functions.
from hermes_constants import display_hermes_home

_console = Console()


# ---------------------------------------------------------------------------
# Shared do_* functions
# ---------------------------------------------------------------------------

def _resolve_short_name(name: str, sources, console: Console) -> str:
    """
    Resolve a short skill name (e.g. 'pptx') to a full identifier by searching
    all sources. If exactly one match is found, returns its identifier. If multiple
    matches exist, shows them and asks the user to use the full identifier.
    Returns empty string if nothing found or ambiguous.
    """
    from tools.skills_hub import unified_search

    c = console or _console
    c.print(f"[dim]Resolving '{name}'...[/]")

    results = unified_search(name, sources, source_filter="all", limit=20)

    # Filter to exact name matches (case-insensitive)
    exact = [r for r in results if r.name.lower() == name.lower()]

    if len(exact) == 1:
        c.print(f"[dim]Resolved to: {exact[0].identifier}[/]")
        return exact[0].identifier

    if len(exact) > 1:
        c.print(f"\n[yellow]Multiple skills named '{name}' found:[/]")
        table = Table()
        table.add_column("Source", style="dim")
        table.add_column("Trust", style="dim")
        table.add_column("Identifier", style="bold cyan")
        for r in exact:
            trust_style = {"builtin": "bright_cyan", "trusted": "green", "community": "yellow"}.get(r.trust_level, "dim")
            trust_label = "official" if r.source == "official" else r.trust_level
            table.add_row(r.source, f"[{trust_style}]{trust_label}[/]", r.identifier)
        c.print(table)
        c.print("[bold]Use the full identifier to install a specific one.[/]\n")
        return ""

    # No exact match — check if there are partial matches to suggest
    if results:
        c.print(f"[yellow]No exact match for '{name}'. Did you mean one of these?[/]")
        for r in results[:5]:
            c.print(f"  [cyan]{r.name}[/] — {r.identifier}")
        c.print()
        return ""

    c.print(f"[bold red]Error:[/] No skill named '{name}' found in any source.\n")
    return ""


def _format_extra_metadata_lines(extra: Dict[str, Any]) -> list[str]:
    lines: list[str] = []
    if not extra:
        return lines

    if extra.get("repo_url"):
        lines.append(f"[bold]Repo:[/] {extra['repo_url']}")
    if extra.get("detail_url"):
        lines.append(f"[bold]Detail Page:[/] {extra['detail_url']}")
    if extra.get("index_url"):
        lines.append(f"[bold]Index:[/] {extra['index_url']}")
    if extra.get("endpoint"):
        lines.append(f"[bold]Endpoint:[/] {extra['endpoint']}")
    if extra.get("install_command"):
        lines.append(f"[bold]Install Command:[/] {extra['install_command']}")
    if extra.get("installs") is not None:
        lines.append(f"[bold]Installs:[/] {extra['installs']}")
    if extra.get("weekly_installs"):
        lines.append(f"[bold]Weekly Installs:[/] {extra['weekly_installs']}")

    security = extra.get("security_audits")
    if isinstance(security, dict) and security:
        ordered = ", ".join(f"{name}={status}" for name, status in sorted(security.items()))
        lines.append(f"[bold]Security:[/] {ordered}")

    return lines


def _resolve_source_meta_and_bundle(identifier: str, sources):
    """Resolve metadata and bundle for a specific identifier."""
    meta = None
    bundle = None
    matched_source = None

    for src in sources:
        if meta is None:
            try:
                meta = src.inspect(identifier)
                if meta:
                    matched_source = src
            except Exception:
                meta = None
        try:
            bundle = src.fetch(identifier)
        except Exception:
            bundle = None
        if bundle:
            matched_source = src
            if meta is None:
                try:
                    meta = src.inspect(identifier)
                except Exception:
                    meta = None
            break

    return meta, bundle, matched_source


def _derive_category_from_install_path(install_path: str) -> str:
    path = Path(install_path)
    parent = str(path.parent)
    return "" if parent == "." else parent


# ---------------------------------------------------------------------------
# Interactive name/category resolution for URL-installed skills
# ---------------------------------------------------------------------------

_VALID_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
_VALID_CATEGORY_RE = re.compile(r"^[a-z][a-z0-9_/-]*$")


def _is_valid_installed_skill_name(name: str) -> bool:
    """Accept identifier-shaped names, reject empty / sentinel-y values."""
    if not isinstance(name, str):
        return False
    candidate = name.strip().lower()
    if not candidate or candidate in {"skill", "readme", "index", "unnamed-skill"}:
        return False
    return bool(_VALID_NAME_RE.match(candidate))


def _existing_categories() -> List[str]:
    """Return sorted subdirectory names under ``~/.hermes/skills/`` that look
    like category buckets (contain at least one ``SKILL.md`` somewhere below).

    Used to suggest reusable categories when interactively installing from a
    URL. Hidden dirs (``.hub``, ``.trash``) are skipped.
    """
    from tools.skills_hub import SKILLS_DIR
    out: List[str] = []
    try:
        for entry in SKILLS_DIR.iterdir():
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            # Only count as a category if it contains skills, not if it IS a skill.
            # Heuristic: if ``<entry>/SKILL.md`` exists, it's a skill at the
            # top level (no category); otherwise treat as a category bucket.
            if (entry / "SKILL.md").exists():
                continue
            # Has at least one nested SKILL.md?
            try:
                if any(entry.rglob("SKILL.md")):
                    out.append(entry.name)
            except OSError:
                continue
    except (FileNotFoundError, OSError):
        return []
    return sorted(set(out))


def _prompt_for_skill_name(c: Console, url: str, default: str = "") -> Optional[str]:
    """Prompt interactively for a skill name. Returns None on cancel/EOF."""
    c.print()
    c.print(
        f"[yellow]The SKILL.md at {url} doesn't declare a `name:` in its "
        f"frontmatter,[/]\n[yellow]and the URL path doesn't produce a valid "
        f"identifier either.[/]"
    )
    default_hint = f" [{default}]" if default else ""
    c.print(
        f"[bold]Enter a skill name{default_hint}:[/] "
        f"[dim](lowercase letters, digits, hyphens, underscores; starts with a letter)[/]"
    )
    try:
        answer = input("Name: ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if not answer and default:
        answer = default
    if not _is_valid_installed_skill_name(answer):
        c.print(f"[bold red]Invalid name:[/] {answer!r}. Aborting install.\n")
        return None
    return answer


def _prompt_for_category(c: Console, existing: List[str]) -> str:
    """Prompt interactively for a category. Empty/None input means flat install."""
    c.print()
    if existing:
        c.print(
            "[bold]Pick a category[/] "
            "[dim](reuse an existing bucket, type a new one, or press Enter to install flat)[/]"
        )
        c.print(f"[dim]Existing: {', '.join(existing)}[/]")
    else:
        c.print(
            "[bold]Category[/] [dim](optional — press Enter to install flat at ~/.hermes/skills/<name>/)[/]"
        )
    try:
        answer = input("Category: ").strip()
    except (EOFError, KeyboardInterrupt):
        return ""
    if not answer:
        return ""
    if not _VALID_CATEGORY_RE.match(answer):
        c.print(f"[dim]Invalid category {answer!r} — installing flat.[/]")
        return ""
    return answer


def do_search(query: str, source: str = "all", limit: int = 10,
              console: Optional[Console] = None) -> None:
    """Search registries and display results as a Rich table."""
    from tools.skills_hub import GitHubAuth, create_source_router, unified_search

    c = console or _console
    c.print(f"\n[bold]Searching for:[/] {query}")

    auth = GitHubAuth()
    sources = create_source_router(auth)
    with c.status("[bold]Searching registries..."):
        results = unified_search(query, sources, source_filter=source, limit=limit)

    if not results:
        c.print("[dim]No skills found matching your query.[/]\n")
        return

    table = Table(title=f"Skills Hub — {len(results)} result(s)")
    table.add_column("Name", style="bold cyan")
    table.add_column("Description", max_width=60)
    table.add_column("Source", style="dim")
    table.add_column("Trust", style="dim")
    table.add_column("Identifier", style="dim")

    for r in results:
        trust_style = {"builtin": "bright_cyan", "trusted": "green", "community": "yellow"}.get(r.trust_level, "dim")
        trust_label = "official" if r.source == "official" else r.trust_level
        table.add_row(
            r.name,
            r.description[:60] + ("..." if len(r.description) > 60 else ""),
            r.source,
            f"[{trust_style}]{trust_label}[/]",
            r.identifier,
        )

    c.print(table)
    c.print("[dim]Use: notpunks skills inspect <identifier> to preview, "
            "notpunks skills install <identifier> to install[/]\n")


def do_browse(page: int = 1, page_size: int = 20, source: str = "all",
              console: Optional[Console] = None) -> None:
    """Browse all available skills across registries, paginated.

    Official skills are always shown first, regardless of source filter.
    """
    from tools.skills_hub import (
        GitHubAuth, create_source_router, parallel_search_sources,
    )

    # Clamp page_size to safe range
    page_size = max(1, min(page_size, 100))

    c = console or _console

    auth = GitHubAuth()
    sources = create_source_router(auth)

    # Collect results from all (or filtered) sources in parallel.
    # Per-source limits are generous — parallelism + 30s timeout cap prevents hangs.
    _TRUST_RANK = {"builtin": 3, "trusted": 2, "community": 1}
    _PER_SOURCE_LIMIT = {
        "official": 200, "skills-sh": 200, "well-known": 50,
        "github": 200, "clawhub": 500, "claude-marketplace": 100,
        "lobehub": 500,
    }

    with c.status("[bold]Fetching skills from registries..."):
        all_results, source_counts, timed_out = parallel_search_sources(
            sources,
            query="",
            per_source_limits=_PER_SOURCE_LIMIT,
            source_filter=source,
            overall_timeout=30,
        )

    if not all_results:
        c.print("[dim]No skills found in the Skills Hub.[/]\n")
        return

    # Deduplicate by name, preferring higher trust
    seen: dict = {}
    for r in all_results:
        rank = _TRUST_RANK.get(r.trust_level, 0)
        if r.name not in seen or rank > _TRUST_RANK.get(seen[r.name].trust_level, 0):
            seen[r.name] = r
    deduped = list(seen.values())

    # Sort: official first, then by trust level (desc), then alphabetically
    deduped.sort(key=lambda r: (
        -_TRUST_RANK.get(r.trust_level, 0),
        r.source != "official",
        r.name.lower(),
    ))

    # Paginate
    total = len(deduped)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = max(1, min(page, total_pages))
    start = (page - 1) * page_size
    end = min(start + page_size, total)
    page_items = deduped[start:end]

    # Count official vs other
    official_count = sum(1 for r in deduped if r.source == "official")

    # Build header
    source_label = f"— {source}" if source != "all" else "— all sources"
    loaded_label = f"{total} skills loaded"
    if timed_out:
        loaded_label += f", {len(timed_out)} source(s) still loading"
    c.print(f"\n[bold]Skills Hub — Browse {source_label}[/]"
            f"  [dim]({loaded_label}, page {page}/{total_pages})[/]")
    if official_count > 0 and page == 1:
        c.print(f"[bright_cyan]★ {official_count} official optional skill(s) from NOTPUNKS[/]")
    c.print()

    # Build table
    table = Table(show_header=True, header_style="bold")
    table.add_column("#", style="dim", width=4, justify="right")
    table.add_column("Name", style="bold cyan", max_width=25)
    table.add_column("Description", max_width=50)
    table.add_column("Source", style="dim", width=12)
    table.add_column("Trust", width=10)

    for i, r in enumerate(page_items, start=start + 1):
        trust_style = {"builtin": "bright_cyan", "trusted": "green",
                       "community": "yellow"}.get(r.trust_level, "dim")
        trust_label = "★ official" if r.source == "official" else r.trust_level

        desc = r.description[:50]
        if len(r.description) > 50:
            desc += "..."

        table.add_row(
            str(i),
            r.name,
            desc,
            r.source,
            f"[{trust_style}]{trust_label}[/]",
        )

    c.print(table)

    # Navigation hints
    nav_parts = []
    if page > 1:
        nav_parts.append(f"[cyan]--page {page - 1}[/] ← prev")
    if page < total_pages:
        nav_parts.append(f"[cyan]--page {page + 1}[/] → next")

    if nav_parts:
        c.print(f"  {' | '.join(nav_parts)}")

    # Source summary
    if source == "all" and source_counts:
        parts = [f"{sid}: {ct}" for sid, ct in sorted(source_counts.items())]
        c.print(f"  [dim]Sources: {', '.join(parts)}[/]")

    if timed_out:
        c.print(f"  [yellow]⚡ Slow sources skipped: {', '.join(timed_out)} "
                f"— run again for cached results[/]")

    c.print(
        "[dim]Tips: 'notpunks skills search <query>' searches deeper; "
        "'/skills create <name>' creates an NFT-gated starter skill in chat[/]\n"
    )


def do_install(identifier: str, category: str = "", force: bool = False,
               console: Optional[Console] = None, skip_confirm: bool = False,
               invalidate_cache: bool = True,
               name_override: str = "") -> None:
    """Fetch, quarantine, scan, confirm, and install a skill.

    ``name_override`` lets non-interactive callers (slash commands, gateway,
    scripts) supply a skill name when the upstream SKILL.md lacks a valid
    ``name:`` frontmatter field. On interactive TTY surfaces, a missing name
    triggers a prompt instead; ``skip_confirm=True`` means "non-interactive"
    (so pair it with ``name_override`` when installing from a URL that has
    no frontmatter).
    """
    from tools.skills_hub import (
        GitHubAuth, create_source_router, ensure_hub_dirs,
        quarantine_bundle, install_from_quarantine, HubLockFile,
    )
    from tools.skills_guard import scan_skill, should_allow_install, format_scan_report

    c = console or _console
    ensure_hub_dirs()

    # Resolve which source adapter handles this identifier
    auth = GitHubAuth()
    sources = create_source_router(auth)

    # If identifier looks like a short name (no slashes), resolve it via search
    if "/" not in identifier:
        identifier = _resolve_short_name(identifier, sources, c)
        if not identifier:
            return

    c.print(f"\n[bold]Fetching:[/] {identifier}")

    meta, bundle, _matched_source = _resolve_source_meta_and_bundle(identifier, sources)

    if not bundle:
        # Check if any source hit GitHub API rate limit
        rate_limited = any(
            getattr(src, "is_rate_limited", False)
            or getattr(getattr(src, "github", None), "is_rate_limited", False)
            for src in sources
        )
        c.print(f"[bold red]Error:[/] Could not fetch '{identifier}' from any source.")
        if rate_limited:
            c.print(
                "[yellow]Hint:[/] GitHub API rate limit exhausted "
                "(unauthenticated: 60 requests/hour).\n"
                "Set [bold]GITHUB_TOKEN[/] in your .env or install the "
                "[bold]gh[/] CLI and run [bold]gh auth login[/] "
                "to raise the limit to 5,000/hr.\n"
            )
        else:
            c.print()
        return

    # URL-sourced skills may arrive with an empty name when SKILL.md has no
    # ``name:`` in frontmatter AND the URL path doesn't yield a valid
    # identifier. Resolve by (1) --name override, (2) interactive prompt on
    # a TTY, (3) refuse with an actionable error on non-interactive surfaces.
    bundle_meta = getattr(bundle, "metadata", {}) or {}
    if bundle.source == "url" and (not bundle.name or bundle_meta.get("awaiting_name")):
        if name_override and _is_valid_installed_skill_name(name_override):
            bundle.name = name_override.strip()
            bundle_meta["awaiting_name"] = False
        elif name_override:
            c.print(
                f"[bold red]Invalid --name:[/] {name_override!r}. "
                "Must be a lowercase identifier (letters, digits, hyphens, "
                "underscores; starts with a letter).\n"
            )
            return
        elif skip_confirm:
            # Non-interactive surface (slash command / TUI / gateway). Can't
            # prompt — emit an actionable error.
            url = bundle_meta.get("url") or identifier
            c.print(
                f"[bold red]Cannot install from URL:[/] {url}\n"
                "[yellow]The SKILL.md has no `name:` in its frontmatter, "
                "and the URL path doesn't produce a valid identifier.[/]\n\n"
                "Retry with an explicit name:\n"
                f"  [bold]/skills install {url} --name <your-name>[/]\n"
                f"  [bold]notpunks skills install {url} --name <your-name>[/]\n\n"
                "[dim]Or ask the SKILL.md's author to add a `name:` field to "
                "its YAML frontmatter.[/]\n"
            )
            return
        else:
            # Interactive TTY — prompt.
            url = bundle_meta.get("url") or identifier
            chosen = _prompt_for_skill_name(c, url)
            if not chosen:
                c.print("[dim]Installation cancelled.[/]\n")
                return
            bundle.name = chosen
            bundle_meta["awaiting_name"] = False
        # Keep SkillMeta in sync so downstream "already installed" checks,
        # audit logs, and display all see the final name.
        if meta is not None:
            meta.name = bundle.name
            meta.path = bundle.name

    # URL-sourced skills: offer to pick a category interactively when the
    # caller didn't specify one (TTY only — non-interactive installs fall
    # through to flat install, matching all other sources).
    if bundle.source == "url" and not category and not skip_confirm:
        category = _prompt_for_category(c, _existing_categories())

    # Auto-detect category for official skills (e.g. "official/autonomous-ai-agents/blackbox")
    if bundle.source == "official" and not category:
        id_parts = bundle.identifier.split("/")  # ["official", "category", "skill"]
        if len(id_parts) >= 3:
            category = id_parts[1]

    # Check if already installed
    lock = HubLockFile()
    existing = lock.get_installed(bundle.name)
    if existing:
        c.print(f"[yellow]Warning:[/] '{bundle.name}' is already installed at {existing['install_path']}")
        if not force:
            c.print("Use --force to reinstall.\n")
            return

    extra_metadata = dict(getattr(meta, "extra", {}) or {})
    extra_metadata.update(getattr(bundle, "metadata", {}) or {})

    # Quarantine the bundle
    try:
        q_path = quarantine_bundle(bundle)
    except ValueError as exc:
        c.print(f"[bold red]Installation blocked:[/] {exc}\n")
        from tools.skills_hub import append_audit_log
        append_audit_log("BLOCKED", bundle.name, bundle.source,
                         bundle.trust_level, "invalid_path", str(exc))
        return
    c.print(f"[dim]Quarantined to {q_path.relative_to(q_path.parent.parent.parent)}[/]")

    # Scan
    c.print("[bold]Running security scan...[/]")
    scan_source = getattr(bundle, "identifier", "") or getattr(meta, "identifier", "") or identifier
    result = scan_skill(q_path, source=scan_source)
    c.print(format_scan_report(result))

    # Check install policy
    allowed, reason = should_allow_install(result, force=force)
    if not allowed:
        c.print(f"\n[bold red]Installation blocked:[/] {reason}")
        # Clean up quarantine
        shutil.rmtree(q_path, ignore_errors=True)
        from tools.skills_hub import append_audit_log
        append_audit_log("BLOCKED", bundle.name, bundle.source,
                         bundle.trust_level, result.verdict,
                         f"{len(result.findings)}_findings")
        return

    if extra_metadata:
        metadata_lines = _format_extra_metadata_lines(extra_metadata)
        if metadata_lines:
            c.print(Panel("\n".join(metadata_lines), title="Upstream Metadata", border_style="blue"))

    # Confirm with user — show appropriate warning based on source
    # skip_confirm bypasses the prompt (needed in TUI mode where input() hangs)
    if not force and not skip_confirm:
        c.print()
        if bundle.source == "official":
            c.print(Panel(
                "[bold bright_cyan]This is an official optional skill maintained by NOTPUNKS.[/]\n\n"
                "It ships with hermes-agent but is not activated by default.\n"
                "Installing will copy it to your skills directory where the agent can use it.\n\n"
                f"Files will be at: [cyan]{display_hermes_home()}/skills/{category + '/' if category else ''}{bundle.name}/[/]",
                title="Official Skill",
                border_style="bright_cyan",
            ))
        else:
            c.print(Panel(
                "[bold yellow]You are installing a third-party skill at your own risk.[/]\n\n"
                "External skills can contain instructions that influence agent behavior,\n"
                "shell commands, and scripts. Even after automated scanning, you should\n"
                "review the installed files before use.\n\n"
                f"Files will be at: [cyan]{display_hermes_home()}/skills/{category + '/' if category else ''}{bundle.name}/[/]",
                title="Disclaimer",
                border_style="yellow",
            ))
        c.print(f"[bold]Install '{bundle.name}'?[/]")
        try:
            answer = input("Confirm [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = "n"
        if answer not in ("y", "yes"):
            c.print("[dim]Installation cancelled.[/]\n")
            shutil.rmtree(q_path, ignore_errors=True)
            return

    # Install
    try:
        install_dir = install_from_quarantine(q_path, bundle.name, category, bundle, result)
    except ValueError as exc:
        c.print(f"[bold red]Installation blocked:[/] {exc}\n")
        shutil.rmtree(q_path, ignore_errors=True)
        from tools.skills_hub import append_audit_log
        append_audit_log("BLOCKED", bundle.name, bundle.source,
                         bundle.trust_level, "invalid_path", str(exc))
        return
    from tools.skills_hub import SKILLS_DIR
    c.print(f"[bold green]Installed:[/] {install_dir.relative_to(SKILLS_DIR)}")
    c.print(f"[dim]Files: {', '.join(bundle.files.keys())}[/]\n")

    if invalidate_cache:
        # Invalidate the skills prompt cache so the new skill appears immediately
        try:
            from agent.prompt_builder import clear_skills_system_prompt_cache
            clear_skills_system_prompt_cache(clear_snapshot=True)
        except Exception:
            pass
    else:
        c.print("[dim]Skill will be available in your next session.[/]")
        c.print("[dim]Use /reset to start a new session now, or --now to activate immediately (invalidates prompt cache).[/]\n")


def _interactive_skill_wizard(console: Console) -> tuple[str, str, str] | None:
    """Interactive wizard for creating a skill. Returns (name, category, content) or None."""
    c = console
    c.print("[bold cyan]📝 Skill Creation Wizard[/bold cyan]")
    c.print("[dim]Press Ctrl+C at any time to cancel.[/dim]\n")

    # Name
    while True:
        name = c.input("Skill name (lowercase, hyphens/underscores): ").strip()
        if not name:
            c.print("[red]Name is required.[/red]")
            continue
        from tools.skill_manager_tool import _validate_name
        err = _validate_name(name)
        if err:
            c.print(f"[red]{err}[/red]")
            continue
        break

    # Category
    category = c.input("Category (optional, e.g. devops, automation): ").strip()
    if not category:
        category = ""

    # Description
    description = c.input("One-line description: ").strip()
    if not description:
        description = f"Skill for {name}"

    # When to use
    c.print("\n[bold]When to use this skill?[/bold] [dim](empty line to finish)[/dim]")
    when_lines = []
    while True:
        line = c.input("• ").strip()
        if not line:
            break
        when_lines.append(line)
    if not when_lines:
        when_lines = [f"Use when you need to perform {name} tasks."]

    # Steps
    c.print("\n[bold]Step-by-step instructions:[/bold] [dim](empty line to finish)[/dim]")
    steps = []
    while True:
        line = c.input("  ").strip()
        if not line:
            break
        steps.append(line)
    if not steps:
        steps = ["Step 1: Execute the task"]

    # Pitfalls
    c.print("\n[bold]Common pitfalls / warnings:[/bold] [dim](empty line to finish)[/dim]")
    pitfalls = []
    while True:
        line = c.input("⚠ ").strip()
        if not line:
            break
        pitfalls.append(line)

    # Build SKILL.md
    content_lines = [
        "---",
        f"name: {name}",
        f'description: "{description}"',
        "---",
        "",
        f"# {name.replace('-', ' ').title()}",
        "",
        "## When to Use",
    ]
    for line in when_lines:
        content_lines.append(f"- {line}")

    content_lines.extend(["", "## Steps"])
    for i, step in enumerate(steps, 1):
        content_lines.append(f"{i}. {step}")

    if pitfalls:
        content_lines.extend(["", "## Pitfalls"])
        for p in pitfalls:
            content_lines.append(f"- **{p}**")

    content_lines.extend(["", "## Verification", "- [ ] Verify the result after applying this skill."])

    content = "\n".join(content_lines)
    return name, category, content


def _starter_skill_content(name: str, language: str = "en") -> str:
    """Build a valid starter SKILL.md for non-interactive slash surfaces."""
    title = name.replace("-", " ").replace("_", " ").replace(".", " ").title()
    topic = title.lower()
    if str(language).strip().lower() == "ru":
        return "\n".join([
            "---",
            f"name: {name}",
            f'description: "Используй этот навык, когда пользователь просит помочь с {topic}."',
            "---",
            "",
            f"# {title}",
            "",
            "## Когда Использовать",
            f"- Используй этот навык, когда задача связана с {topic}.",
            "",
            "## Шаги",
            "1. Уточни цель, входные данные и ограничения.",
            "2. Проверь релевантные файлы, инструменты или внешний контекст.",
            "3. Выполни workflow, сохраняя изменения в рамках запроса.",
            "4. Проверь результат конкретной проверкой.",
            "",
            "## Проверка",
            "- [ ] Подтверди результат через видимое поведение или целевой тест.",
            "",
        ])
    return "\n".join([
        "---",
        f"name: {name}",
        f'description: "Use this skill when the user asks for help with {topic}."',
        "---",
        "",
        f"# {title}",
        "",
        "## When to Use",
        f"- Use this skill when the task is about {topic}.",
        "",
        "## Steps",
        "1. Clarify the exact goal, inputs, and constraints.",
        "2. Inspect the relevant files, tools, or external context.",
        "3. Apply the workflow and keep changes scoped to the request.",
        "4. Verify the outcome with a concrete check.",
        "",
        "## Verification",
        "- [ ] Confirm the final result with the user-visible behavior or a targeted test.",
        "",
    ])


def do_create(
    name: str,
    from_file: str = "",
    category: str = "",
    console: Optional[Console] = None,
    interactive: bool = True,
) -> None:
    """Create a new skill from a SKILL.md file, stdin, or interactive wizard."""
    c = console or _console

    # NFT gate: local skill creation is open to current NOT Punks beta holders.
    try:
        from agent.wallet.context import build_wallet_context
        ctx = build_wallet_context()
        if not ctx or not ctx.can_create_skills:
            c.print(
                "[yellow]⛔ Skill creation is locked.[/yellow]\n"
                "Connect a wallet holding at least one NOT Punks NFT. "
                "Publishing still requires a matching NOT Punks + NOT Punks Girls creator pair."
            )
            return
    except Exception as e:
        c.print(
            "[yellow]⛔ Skill creation is locked.[/yellow]\n"
            f"[dim]Could not verify NFT access: {e}[/dim]"
        )
        return

    # Read SKILL.md content
    if from_file:
        path = Path(from_file)
        if not path.exists():
            c.print(f"[red]File not found: {from_file}[/red]")
            return
        try:
            content = path.read_text(encoding="utf-8")
        except Exception as e:
            c.print(f"[red]Failed to read file: {e}[/red]")
            return
    elif not name:
        # Interactive wizard when no name/file provided
        if not interactive:
            c.print(
                "[bold red]Usage:[/] /skills create <name> [--category <cat>]\n"
                "       /skills create <name> --from-file <path> [--category <cat>]\n"
                "[dim]TUI mode creates a starter SKILL.md when you pass a name.[/dim]\n"
                "[dim]For the full wizard, run: notpunks skills create[/dim]"
            )
            return
        result = _interactive_skill_wizard(c)
        if result is None:
            return
        name, category, content = result
    else:
        if interactive:
            c.print(
                "[dim]Paste your SKILL.md content below (including YAML frontmatter).[/dim]\n"
                "Press [bold]Ctrl+D[/bold] (Unix) or [bold]Ctrl+Z + Enter[/bold] (Windows) when done:"
            )
            try:
                content = sys.stdin.read()
            except Exception as e:
                c.print(f"[red]Failed to read stdin: {e}[/red]")
                return
        else:
            try:
                from hermes_cli.config import load_config
                from hermes_cli.language import get_config_language
                starter_language = get_config_language(load_config())
            except Exception:
                starter_language = "en"
            content = _starter_skill_content(name, starter_language)

    if not content.strip():
        c.print("[red]No content provided.[/red]")
        return

    from tools.skill_manager_tool import _create_skill

    result = _create_skill(name, content, category=category or None)
    if result.get("success"):
        c.print(f"[green]✓ Skill '{name}' created.[/green]")
        c.print(f"  Path: {result.get('path', '')}")
        if result.get("category"):
            c.print(f"  Category: {result['category']}")
        if not from_file and not interactive:
            c.print("[dim]Starter SKILL.md created. Edit the file before relying on it.[/dim]")
    else:
        c.print(f"[red]✗ {result.get('error', 'Unknown error')}[/red]")


def do_inspect(identifier: str, console: Optional[Console] = None) -> None:
    """Preview a skill's SKILL.md content without installing."""
    from tools.skills_hub import GitHubAuth, create_source_router

    c = console or _console
    auth = GitHubAuth()
    sources = create_source_router(auth)

    if "/" not in identifier:
        identifier = _resolve_short_name(identifier, sources, c)
        if not identifier:
            return

    meta, bundle, _matched_source = _resolve_source_meta_and_bundle(identifier, sources)

    if not meta:
        c.print(f"[bold red]Error:[/] Could not find '{identifier}' in any source.\n")
        return

    c.print()
    trust_style = {"builtin": "bright_cyan", "trusted": "green", "community": "yellow"}.get(meta.trust_level, "dim")
    trust_label = "official" if meta.source == "official" else meta.trust_level

    info_lines = [
        f"[bold]Name:[/] {meta.name}",
        f"[bold]Description:[/] {meta.description}",
        f"[bold]Source:[/] {meta.source}",
        f"[bold]Trust:[/] [{trust_style}]{trust_label}[/]",
        f"[bold]Identifier:[/] {meta.identifier}",
    ]
    if meta.tags:
        info_lines.append(f"[bold]Tags:[/] {', '.join(meta.tags)}")
    info_lines.extend(_format_extra_metadata_lines(meta.extra))

    c.print(Panel("\n".join(info_lines), title=f"Skill: {meta.name}"))

    if bundle and "SKILL.md" in bundle.files:
        content = bundle.files["SKILL.md"]
        if isinstance(content, bytes):
            content = content.decode("utf-8", errors="replace")
        # Show first 50 lines as preview
        lines = content.split("\n")
        preview = "\n".join(lines[:50])
        if len(lines) > 50:
            preview += f"\n\n... ({len(lines) - 50} more lines)"
        c.print(Panel(preview, title="SKILL.md Preview", subtitle="notpunks skills install <id> to install"))

    c.print()


def browse_skills(page: int = 1, page_size: int = 20, source: str = "all") -> dict:
    """Paginated hub browse for programmatic callers (e.g. TUI gateway).

    Returns ``{"items": [...], "page": int, "total_pages": int, "total": int}``.
    """
    from tools.skills_hub import GitHubAuth, create_source_router

    page_size = max(1, min(page_size, 100))
    _TRUST_RANK = {"builtin": 3, "trusted": 2, "community": 1}
    _PER_SOURCE_LIMIT = {"official": 100, "skills-sh": 100, "well-known": 25, "github": 100, "clawhub": 50,
                         "claude-marketplace": 50, "lobehub": 50}
    auth = GitHubAuth()
    sources = create_source_router(auth)
    all_results: list = []
    for src in sources:
        sid = src.source_id()
        if source != "all" and sid != source and sid != "official":
            continue
        try:
            limit = _PER_SOURCE_LIMIT.get(sid, 50)
            all_results.extend(src.search("", limit=limit))
        except Exception:
            continue
    if not all_results:
        return {"items": [], "page": 1, "total_pages": 1, "total": 0}
    seen: dict = {}
    for r in all_results:
        rank = _TRUST_RANK.get(r.trust_level, 0)
        if r.name not in seen or rank > _TRUST_RANK.get(seen[r.name].trust_level, 0):
            seen[r.name] = r
    deduped = list(seen.values())
    deduped.sort(key=lambda r: (-_TRUST_RANK.get(r.trust_level, 0), r.source != "official", r.name.lower()))
    total = len(deduped)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = max(1, min(page, total_pages))
    start = (page - 1) * page_size
    page_items = deduped[start : min(start + page_size, total)]
    return {
        "items": [{"name": r.name, "description": r.description, "source": r.source,
                    "trust": r.trust_level} for r in page_items],
        "page": page,
        "total_pages": total_pages,
        "total": total,
    }


def inspect_skill(identifier: str) -> Optional[dict]:
    """Skill metadata (+ SKILL.md preview) for programmatic callers."""
    from tools.skills_hub import GitHubAuth, create_source_router

    class _Q:
        def print(self, *a, **k):
            pass

    c = _Q()
    auth = GitHubAuth()
    sources = create_source_router(auth)
    ident = identifier
    if "/" not in ident:
        ident = _resolve_short_name(ident, sources, c)
        if not ident:
            return None
    meta, bundle, _ = _resolve_source_meta_and_bundle(ident, sources)
    if not meta:
        return None
    out: dict = {
        "name": meta.name,
        "description": meta.description,
        "source": meta.source,
        "identifier": meta.identifier,
        "tags": list(meta.tags) if meta.tags else [],
    }
    if bundle and "SKILL.md" in bundle.files:
        content = bundle.files["SKILL.md"]
        if isinstance(content, bytes):
            content = content.decode("utf-8", errors="replace")
        lines = content.split("\n")
        preview = "\n".join(lines[:50])
        if len(lines) > 50:
            preview += f"\n\n... ({len(lines) - 50} more lines)"
        out["skill_md_preview"] = preview
    return out


def do_list(source_filter: str = "all",
            enabled_only: bool = False,
            console: Optional[Console] = None) -> None:
    """List installed skills, distinguishing hub, builtin, and local skills.

    Args:
        source_filter: ``all`` | ``hub`` | ``builtin`` | ``local``.
        enabled_only: If True, hide disabled skills from the output.

    Enabled/disabled state is resolved against the currently active profile's
    config — ``hermes -p <profile> skills list`` reads that profile's
    ``skills.disabled`` list because ``-p`` swaps ``HERMES_HOME`` at process
    start.  No explicit profile flag needed here.
    """
    from tools.skills_hub import HubLockFile, ensure_hub_dirs
    from tools.skills_sync import _read_manifest
    from tools.skills_tool import _find_all_skills
    from agent.skill_utils import get_disabled_skill_names

    c = console or _console
    ensure_hub_dirs()
    lock = HubLockFile()
    hub_installed = {e["name"]: e for e in lock.list_installed()}
    builtin_names = set(_read_manifest())

    # Pull ALL skills (including disabled ones) so we can annotate status.
    all_skills = _find_all_skills(skip_disabled=True)
    disabled_names = get_disabled_skill_names()

    title = "Installed Skills"
    if enabled_only:
        title += " (enabled only)"

    table = Table(title=title)
    table.add_column("Name", style="bold cyan")
    table.add_column("Category", style="dim")
    table.add_column("Source", style="dim")
    table.add_column("Trust", style="dim")
    table.add_column("Status", style="dim")

    hub_count = 0
    builtin_count = 0
    local_count = 0
    enabled_count = 0
    disabled_count = 0

    for skill in sorted(all_skills, key=lambda s: (s.get("category") or "", s["name"])):
        name = skill["name"]
        category = skill.get("category", "")
        hub_entry = hub_installed.get(name)

        if hub_entry:
            source_type = "hub"
            source_display = hub_entry.get("source", "hub")
            trust = hub_entry.get("trust_level", "community")
        elif name in builtin_names:
            source_type = "builtin"
            source_display = "builtin"
            trust = "builtin"
        else:
            source_type = "local"
            source_display = "local"
            trust = "local"

        if source_filter != "all" and source_filter != source_type:
            continue

        is_enabled = name not in disabled_names
        if enabled_only and not is_enabled:
            continue

        if source_type == "hub":
            hub_count += 1
        elif source_type == "builtin":
            builtin_count += 1
        else:
            local_count += 1

        if is_enabled:
            enabled_count += 1
            status_cell = "[bold green]enabled[/]"
        else:
            disabled_count += 1
            status_cell = "[dim red]disabled[/]"

        trust_style = {"builtin": "bright_cyan", "trusted": "green", "community": "yellow", "local": "dim"}.get(trust, "dim")
        trust_label = "official" if source_display == "official" else trust
        table.add_row(name, category, source_display, f"[{trust_style}]{trust_label}[/]", status_cell)

    c.print(table)
    summary = f"[dim]{hub_count} hub-installed, {builtin_count} builtin, {local_count} local"
    if enabled_only:
        summary += f" — {enabled_count} enabled shown"
    else:
        summary += f" — {enabled_count} enabled, {disabled_count} disabled"
    summary += "[/]\n"
    c.print(summary)


def do_check(name: Optional[str] = None, console: Optional[Console] = None) -> None:
    """Check hub-installed skills for upstream updates."""
    from tools.skills_hub import check_for_skill_updates

    c = console or _console
    results = check_for_skill_updates(name=name)
    if not results:
        c.print("[dim]No hub-installed skills to check.[/]\n")
        return

    table = Table(title="Skill Updates")
    table.add_column("Name", style="bold cyan")
    table.add_column("Source", style="dim")
    table.add_column("Status", style="dim")

    for entry in results:
        table.add_row(entry.get("name", ""), entry.get("source", ""), entry.get("status", ""))

    c.print(table)
    update_count = sum(1 for entry in results if entry.get("status") == "update_available")
    c.print(f"[dim]{update_count} update(s) available across {len(results)} checked skill(s)[/]\n")


def do_update(name: Optional[str] = None, console: Optional[Console] = None) -> None:
    """Update hub-installed skills with upstream changes."""
    from tools.skills_hub import HubLockFile, check_for_skill_updates

    c = console or _console
    lock = HubLockFile()
    updates = [entry for entry in check_for_skill_updates(name=name) if entry.get("status") == "update_available"]
    if not updates:
        c.print("[dim]No updates available.[/]\n")
        return

    for entry in updates:
        installed = lock.get_installed(entry["name"])
        category = _derive_category_from_install_path(installed.get("install_path", "")) if installed else ""
        c.print(f"[bold]Updating:[/] {entry['name']}")
        do_install(entry["identifier"], category=category, force=True, console=c)

    c.print(f"[bold green]Updated {len(updates)} skill(s).[/]\n")


def do_audit(name: Optional[str] = None, console: Optional[Console] = None) -> None:
    """Re-run security scan on installed hub skills."""
    from tools.skills_hub import HubLockFile, SKILLS_DIR
    from tools.skills_guard import scan_skill, format_scan_report

    c = console or _console
    lock = HubLockFile()
    installed = lock.list_installed()

    if not installed:
        c.print("[dim]No hub-installed skills to audit.[/]\n")
        return

    targets = installed
    if name:
        targets = [e for e in installed if e["name"] == name]
        if not targets:
            c.print(f"[bold red]Error:[/] '{name}' is not a hub-installed skill.\n")
            return

    c.print(f"\n[bold]Auditing {len(targets)} skill(s)...[/]\n")

    for entry in targets:
        skill_path = SKILLS_DIR / entry["install_path"]
        if not skill_path.exists():
            c.print(f"[yellow]Warning:[/] {entry['name']} — path missing: {entry['install_path']}")
            continue

        result = scan_skill(skill_path, source=entry.get("identifier", entry["source"]))
        c.print(format_scan_report(result))
        c.print()


def do_uninstall(name: str, console: Optional[Console] = None,
                 skip_confirm: bool = False,
                 invalidate_cache: bool = True) -> None:
    """Remove a hub-installed skill with confirmation."""
    from tools.skills_hub import uninstall_skill

    c = console or _console

    # skip_confirm bypasses the prompt (needed in TUI mode where input() hangs)
    if not skip_confirm:
        c.print(f"\n[bold]Uninstall '{name}'?[/]")
        try:
            answer = input("Confirm [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = "n"
        if answer not in ("y", "yes"):
            c.print("[dim]Cancelled.[/]\n")
            return

    success, msg = uninstall_skill(name)
    if success:
        c.print(f"[bold green]{msg}[/]\n")
        if invalidate_cache:
            try:
                from agent.prompt_builder import clear_skills_system_prompt_cache
                clear_skills_system_prompt_cache(clear_snapshot=True)
            except Exception:
                pass
        else:
            c.print("[dim]Change will take effect in your next session.[/]")
            c.print("[dim]Use /reset to start a new session now, or --now to apply immediately (invalidates prompt cache).[/]\n")
    else:
        c.print(f"[bold red]Error:[/] {msg}\n")


def do_delete(name: str, console: Optional[Console] = None,
              skip_confirm: bool = False,
              invalidate_cache: bool = True) -> None:
    """Delete a local agent-created skill with confirmation."""
    from tools.skill_manager_tool import _delete_skill

    c = console or _console

    if not skip_confirm:
        c.print(f"\n[bold]Delete local skill '{name}'?[/]")
        c.print("[dim]This removes the local skill directory. Hub-installed skills still use uninstall.[/]")
        try:
            answer = input("Confirm [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = "n"
        if answer not in ("y", "yes"):
            c.print("[dim]Cancelled.[/]\n")
            return

    result = _delete_skill(name)
    if result.get("success"):
        c.print(f"[bold green]{result.get('message', f'Skill {name} deleted.')}[/]\n")
        if invalidate_cache:
            try:
                from agent.prompt_builder import clear_skills_system_prompt_cache
                clear_skills_system_prompt_cache(clear_snapshot=True)
            except Exception:
                pass
        else:
            c.print("[dim]Change will take effect in your next session.[/]")
            c.print("[dim]Use /reset to start a new session now, or --now to apply immediately (invalidates prompt cache).[/]\n")
    else:
        c.print(f"[bold red]Error:[/] {result.get('error', 'Unknown error')}[/]\n")


def do_reset(name: str, restore: bool = False,
             console: Optional[Console] = None,
             skip_confirm: bool = False,
             invalidate_cache: bool = True) -> None:
    """Reset a bundled skill's manifest tracking (+ optionally restore from bundled)."""
    from tools.skills_sync import reset_bundled_skill

    c = console or _console

    if not skip_confirm and restore:
        c.print(f"\n[bold]Restore '{name}' from bundled source?[/]")
        c.print("[dim]This will DELETE your current copy and re-copy the bundled version.[/]")
        try:
            answer = input("Confirm [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = "n"
        if answer not in ("y", "yes"):
            c.print("[dim]Cancelled.[/]\n")
            return

    result = reset_bundled_skill(name, restore=restore)

    if not result["ok"]:
        c.print(f"[bold red]Error:[/] {result['message']}\n")
        return

    c.print(f"[bold green]{result['message']}[/]")
    synced = result.get("synced") or {}
    if synced.get("copied"):
        c.print(f"[dim]Copied: {', '.join(synced['copied'])}[/]")
    if synced.get("updated"):
        c.print(f"[dim]Updated: {', '.join(synced['updated'])}[/]")
    c.print()

    if invalidate_cache:
        try:
            from agent.prompt_builder import clear_skills_system_prompt_cache
            clear_skills_system_prompt_cache(clear_snapshot=True)
        except Exception:
            pass
    else:
        c.print("[dim]Change will take effect in your next session.[/]")
        c.print("[dim]Use /reset to start a new session now, or --now to apply immediately (invalidates prompt cache).[/]\n")


def do_tap(action: str, repo: str = "", console: Optional[Console] = None) -> None:
    """Manage taps (custom GitHub repo sources)."""
    from tools.skills_hub import TapsManager

    c = console or _console
    mgr = TapsManager()

    if action == "list":
        taps = mgr.list_taps()
        if not taps:
            c.print("[dim]No custom taps configured. Using default sources only.[/]\n")
            return
        table = Table(title="Configured Taps")
        table.add_column("Repo", style="bold cyan")
        table.add_column("Path", style="dim")
        for t in taps:
            label = t.get("repo") or t.get("name") or t.get("path", "unknown")
            table.add_row(label, t.get("path", "skills/"))
        c.print(table)
        c.print()

    elif action == "add":
        if not repo:
            c.print("[bold red]Error:[/] Repo required. Usage: notpunks skills tap add owner/repo\n")
            return
        if mgr.add(repo):
            c.print(f"[bold green]Added tap:[/] {repo}\n")
        else:
            c.print(f"[yellow]Tap already exists:[/] {repo}\n")

    elif action == "remove":
        if not repo:
            c.print("[bold red]Error:[/] Repo required. Usage: notpunks skills tap remove owner/repo\n")
            return
        if mgr.remove(repo):
            c.print(f"[bold green]Removed tap:[/] {repo}\n")
        else:
            c.print(f"[bold red]Error:[/] Tap not found: {repo}\n")

    else:
        c.print(f"[bold red]Unknown tap action:[/] {action}. Use: list, add, remove\n")


def do_publish(skill_path: str, target: str = "github", repo: str = "",
               console: Optional[Console] = None) -> None:
    """Publish a local skill to a registry (GitHub PR or ClawHub submission)."""
    from tools.skills_hub import GitHubAuth, SKILLS_DIR
    from tools.skills_guard import scan_skill, format_scan_report

    c = console or _console
    path = Path(skill_path)

    # Resolve relative to skills dir if not absolute
    if not path.is_absolute():
        path = SKILLS_DIR / path
    if not path.exists() or not (path / "SKILL.md").exists():
        c.print(f"[bold red]Error:[/] No SKILL.md found at {path}\n")
        return

    # Validate the skill
    import yaml
    skill_md = (path / "SKILL.md").read_text(encoding="utf-8")
    fm = {}
    if skill_md.startswith("---"):
        import re
        match = re.search(r'\n---\s*\n', skill_md[3:])
        if match:
            try:
                fm = yaml.safe_load(skill_md[3:match.start() + 3]) or {}
            except yaml.YAMLError:
                pass

    name = fm.get("name", path.name)
    description = fm.get("description", "")
    if not description:
        c.print("[bold red]Error:[/] SKILL.md must have a 'description' in frontmatter.\n")
        return

    # Self-scan before publishing
    c.print(f"[bold]Scanning '{name}' before publish...[/]")
    result = scan_skill(path, source="self")
    c.print(format_scan_report(result))
    if result.verdict == "dangerous":
        c.print("[bold red]Cannot publish a skill with DANGEROUS verdict.[/]\n")
        return

    if target == "github":
        if not repo:
            c.print("[bold red]Error:[/] --repo required for GitHub publish.\n"
                    "Usage: notpunks skills publish <path> --to github --repo owner/repo\n")
            return

        auth = GitHubAuth()
        if not auth.is_authenticated():
            c.print("[bold red]Error:[/] GitHub authentication required.\n"
                    f"Set GITHUB_TOKEN in {display_hermes_home()}/.env or run 'gh auth login'.\n")
            return

        c.print(f"[bold]Publishing '{name}' to {repo}...[/]")
        success, msg = _github_publish(path, name, repo, auth)
        if success:
            c.print(f"[bold green]{msg}[/]\n")
        else:
            c.print(f"[bold red]Error:[/] {msg}\n")

    elif target == "clawhub":
        c.print("[yellow]ClawHub publishing is not yet supported. "
                "Submit manually at https://clawhub.ai/submit[/]\n")
    else:
        c.print(f"[bold red]Unknown target:[/] {target}. Use 'github' or 'clawhub'.\n")


def _github_publish(skill_path: Path, skill_name: str, target_repo: str,
                    auth) -> tuple:
    """Create a PR to a GitHub repo with the skill. Returns (success, message)."""
    import httpx

    headers = auth.get_headers()

    # 1. Fork the repo
    try:
        resp = httpx.post(
            f"https://api.github.com/repos/{target_repo}/forks",
            headers=headers, timeout=30,
        )
        if resp.status_code in (200, 202):
            fork = resp.json()
            fork_repo = fork["full_name"]
        elif resp.status_code == 403:
            return False, "GitHub token lacks permission to fork repos"
        else:
            return False, f"Failed to fork {target_repo}: {resp.status_code}"
    except httpx.HTTPError as e:
        return False, f"Network error forking repo: {e}"

    # 2. Get default branch
    try:
        resp = httpx.get(
            f"https://api.github.com/repos/{target_repo}",
            headers=headers, timeout=15,
        )
        default_branch = resp.json().get("default_branch", "main")
    except Exception:
        default_branch = "main"

    # 3. Get the base tree SHA
    try:
        resp = httpx.get(
            f"https://api.github.com/repos/{fork_repo}/git/refs/heads/{default_branch}",
            headers=headers, timeout=15,
        )
        base_sha = resp.json()["object"]["sha"]
    except Exception as e:
        return False, f"Failed to get base branch: {e}"

    # 4. Create a new branch
    branch_name = f"add-skill-{skill_name}"
    try:
        httpx.post(
            f"https://api.github.com/repos/{fork_repo}/git/refs",
            headers=headers, timeout=15,
            json={"ref": f"refs/heads/{branch_name}", "sha": base_sha},
        )
    except Exception as e:
        return False, f"Failed to create branch: {e}"

    # 5. Upload skill files
    for f in skill_path.rglob("*"):
        if not f.is_file():
            continue
        rel = str(f.relative_to(skill_path))
        upload_path = f"skills/{skill_name}/{rel}"
        try:
            import base64
            content_b64 = base64.b64encode(f.read_bytes()).decode()
            httpx.put(
                f"https://api.github.com/repos/{fork_repo}/contents/{upload_path}",
                headers=headers, timeout=15,
                json={
                    "message": f"Add {skill_name} skill: {rel}",
                    "content": content_b64,
                    "branch": branch_name,
                },
            )
        except Exception as e:
            return False, f"Failed to upload {rel}: {e}"

    # 6. Create PR
    try:
        resp = httpx.post(
            f"https://api.github.com/repos/{target_repo}/pulls",
            headers=headers, timeout=15,
            json={
                "title": f"Add skill: {skill_name}",
                "body": f"Submitting the `{skill_name}` skill via Hermes Skills Hub.\n\n"
                        f"This skill was scanned by the Hermes Skills Guard before submission.",
                "head": f"{fork_repo.split('/')[0]}:{branch_name}",
                "base": default_branch,
            },
        )
        if resp.status_code == 201:
            pr_url = resp.json().get("html_url", "")
            return True, f"PR created: {pr_url}"
        else:
            return False, f"Failed to create PR: {resp.status_code} {resp.text[:200]}"
    except httpx.HTTPError as e:
        return False, f"Network error creating PR: {e}"


def do_snapshot_export(output_path: str, console: Optional[Console] = None) -> None:
    """Export current hub skill configuration to a portable JSON file."""
    from tools.skills_hub import HubLockFile, TapsManager

    c = console or _console
    lock = HubLockFile()
    taps = TapsManager()

    installed = lock.list_installed()
    tap_list = taps.list_taps()

    snapshot = {
        "hermes_version": "0.1.0",
        "exported_at": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).isoformat(),
        "skills": [
            {
                "name": entry["name"],
                "source": entry.get("source", ""),
                "identifier": entry.get("identifier", ""),
                "category": str(Path(entry.get("install_path", "")).parent)
                            if "/" in entry.get("install_path", "") else "",
            }
            for entry in installed
        ],
        "taps": tap_list,
    }

    payload = json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n"
    if output_path == "-":
        import sys
        sys.stdout.write(payload)
    else:
        out = Path(output_path)
        out.write_text(payload)
        c.print(f"[bold green]Snapshot exported:[/] {out}")
        c.print(f"[dim]{len(installed)} skill(s), {len(tap_list)} tap(s)[/]\n")


def do_snapshot_import(input_path: str, force: bool = False,
                       console: Optional[Console] = None) -> None:
    """Re-install skills from a snapshot file."""
    from tools.skills_hub import TapsManager

    c = console or _console
    inp = Path(input_path)
    if not inp.exists():
        c.print(f"[bold red]Error:[/] File not found: {inp}\n")
        return

    try:
        snapshot = json.loads(inp.read_text())
    except json.JSONDecodeError:
        c.print(f"[bold red]Error:[/] Invalid JSON in {inp}\n")
        return

    # Restore taps first
    taps = snapshot.get("taps", [])
    if taps:
        mgr = TapsManager()
        for tap in taps:
            repo = tap.get("repo", "")
            if repo:
                mgr.add(repo, tap.get("path", "skills/"))
        c.print(f"[dim]Restored {len(taps)} tap(s)[/]")

    # Install skills
    skills = snapshot.get("skills", [])
    if not skills:
        c.print("[dim]No skills in snapshot to install.[/]\n")
        return

    c.print(f"[bold]Importing {len(skills)} skill(s) from snapshot...[/]\n")
    for entry in skills:
        identifier = entry.get("identifier", "")
        category = entry.get("category", "")
        if not identifier:
            c.print(f"[yellow]Skipping entry with no identifier: {entry.get('name', '?')}[/]")
            continue

        c.print(f"[bold]--- {entry.get('name', identifier)} ---[/]")
        do_install(identifier, category=category, force=force, console=c)

    c.print("[bold green]Snapshot import complete.[/]\n")


def do_marketplace_access(name: str = "", console: Optional[Console] = None) -> None:
    """Show wallet marketplace access, optionally for a specific local listing."""
    from agent.wallet.context import build_wallet_context
    from hermes_cli.skill_marketplace import check_skill_access, load_listing

    c = console or _console
    ctx = build_wallet_context()
    if not ctx:
        c.print("[red]No wallet connected. Run [bold]/wallet connect[/bold] first.[/red]\n")
        return

    if not name:
        c.print("[bold]Skill NFT Access[/bold]")
        c.print(f"  Custom skills: {'unlocked' if ctx.can_use_custom_skills else 'locked'}")
        c.print(f"  Local skill creation: {'unlocked' if ctx.can_create_skills else 'locked'}")
        c.print(f"  Marketplace publishing: {'unlocked' if getattr(ctx, 'can_publish_skills', False) else 'locked'}")
        c.print(f"  Skill Pass: {'yes' if ctx.skill_pass else 'no'}")
        if getattr(ctx, "paid_skill_slots", 0):
            c.print(f"  Paid skill slots: {ctx.paid_skill_slots}")
        if ctx.matching_pair_numbers:
            c.print(f"  Matching pairs: {', '.join(f'#{n}' for n in ctx.matching_pair_numbers)}")
        c.print(f"  Roles: {', '.join(ctx.access_roles) if ctx.access_roles else 'none'}")
        c.print(f"  Skill NFT licenses: {', '.join(sorted(ctx.skill_licenses)) if ctx.skill_licenses else 'none'}\n")
        return

    listing = load_listing(name)
    if not listing:
        c.print(f"[yellow]No local marketplace listing found for '{name}'.[/yellow]\n")
        return
    allowed, reason = check_skill_access(ctx, listing)
    status = "[green]allowed[/green]" if allowed else "[red]denied[/red]"
    c.print(f"[bold]{listing.get('name', name)}[/bold]: access {status}")
    c.print(f"  Reason: {reason}")
    c.print(f"  Policy: {listing.get('access', {}).get('policy', 'holders')}\n")


def do_marketplace_list(console: Optional[Console] = None) -> None:
    """List local marketplace skill listings."""
    from hermes_cli.skill_marketplace import list_listings

    c = console or _console
    listings = list_listings()
    if not listings:
        c.print("[dim]No local marketplace listings yet.[/]\n")
        c.print("[dim]Publish one with: /skills publish <skill> --price <TON>[/]\n")
        return

    table = Table(title="Skill Marketplace")
    table.add_column("Skill", style="bold cyan")
    table.add_column("Price", justify="right")
    table.add_column("Access")
    table.add_column("Status", style="dim")
    for listing in listings:
        price = listing.get("price_ton", 0)
        currency = listing.get("currency", "TON")
        access = listing.get("access", {})
        if not isinstance(access, dict):
            access = {}
        table.add_row(
            str(listing.get("name") or listing.get("skill_id") or ""),
            f"{price:g} {currency}" if isinstance(price, (int, float)) else f"{price} {currency}",
            str(access.get("policy", "holders")),
            str(listing.get("status", "local_published")),
        )
    c.print(table)
    c.print("[dim]Use /skills access <skill> to check access or /skills buy <skill> to create a purchase intent.[/]\n")


def do_marketplace_publish(skill_ref: str, price_ton: float = 0.0,
                           console: Optional[Console] = None,
                           remote: bool = False,
                           use_admin_token: bool = False) -> None:
    """Create a local marketplace listing for a skill."""
    from agent.wallet.context import build_wallet_context
    from hermes_cli.skill_marketplace import (
        build_listing,
        find_local_skill,
        publish_listing_remote,
        request_publish_wallet_proof,
        save_listing,
    )

    c = console or _console
    ctx = build_wallet_context()
    if not use_admin_token and (not ctx or not getattr(ctx, "can_publish_skills", False)):
        c.print(
            "[yellow]⛔ Publishing is locked.[/yellow]\n"
            "Hold a matching NOT Punks + NOT Punks Girls pair with the same number."
        )
        if ctx:
            c.print(
                f"[dim]Wallet checked: {len(ctx.nfts)} configured NFT(s), "
                f"roles={', '.join(ctx.access_roles) if ctx.access_roles else 'none'}, "
                f"pairs={', '.join(f'#{n}' for n in ctx.matching_pair_numbers) if ctx.matching_pair_numbers else 'none'}[/]"
            )
            c.print("[dim]Run /wallet nft to force rescan or /wallet access for details.[/]")
        return

    skill_path = find_local_skill(skill_ref)
    if not skill_path:
        c.print(f"[bold red]Error:[/] Local skill not found: {skill_ref}\n")
        return

    listing = build_listing(skill_path, creator_wallet=getattr(ctx, "wallet_address", ""), price_ton=price_ton)
    path = save_listing(listing)
    remote_result = None
    if remote:
        try:
            wallet_proof = None if use_admin_token else request_publish_wallet_proof(listing, console=c)
            remote_result = publish_listing_remote(
                listing,
                wallet_proof=wallet_proof,
                use_admin_token=use_admin_token,
            )
        except Exception as exc:
            c.print(f"[bold red]Remote publish failed:[/] {exc}")
            c.print(f"[dim]Local listing was still saved at: {path}[/]\n")
            return
    c.print(f"[bold green]Local marketplace listing created:[/] {listing['name']}")
    c.print(f"  Skill ID: {listing['skill_id']}")
    c.print(f"  Price: {listing['price_ton']} TON")
    c.print(f"  Access: {listing['access'].get('policy', 'holders')}")
    c.print(f"  Bundle hash: {listing.get('bundle', {}).get('bundle_hash', '')}")
    c.print(f"  Skill NFT metadata: {listing.get('nft_metadata', {}).get('url', '')}")
    c.print(f"  Registry: {path}")
    if remote_result:
        remote_listing = remote_result.get("listing", {})
        c.print(f"[bold green]Remote marketplace published:[/] https://app.notpunks.com/marketplace")
        if remote_listing.get("bundleUrl"):
            c.print(f"  Remote bundle: {remote_listing['bundleUrl']}")
    c.print("[dim]Skill NFT metadata is mint-ready; TON mint/royalty settlement is pending contract integration.[/]\n")


def do_marketplace_unpublish(
    skill_name: str,
    console: Optional[Console] = None,
    remote: bool = False,
    use_admin_token: bool = False,
) -> None:
    """Remove a local marketplace listing without deleting the local skill."""
    from hermes_cli.skill_marketplace import (
        delete_listing,
        fetch_remote_listing,
        request_publish_wallet_proof,
        unpublish_listing_remote,
        unpublish_proof_payload,
    )

    c = console or _console
    if not skill_name:
        c.print("[bold red]Usage:[/] /skills unpublish <skill-name> [--remote]\n")
        return
    if remote:
        try:
            wallet_proof = None
            if not use_admin_token:
                listing = fetch_remote_listing(skill_name)
                wallet_proof = request_publish_wallet_proof(
                    listing,
                    console=c,
                    payload=unpublish_proof_payload(listing),
                    prompt="Sign this skill unpublish request in your TON wallet.",
                )
            result = unpublish_listing_remote(
                skill_name,
                wallet_proof=wallet_proof,
                use_admin_token=use_admin_token,
            )
            c.print(f"[bold green]Remote unpublished:[/] {result.get('skillId', skill_name)}")
        except Exception as exc:
            c.print(f"[bold red]Remote unpublish failed:[/] {exc}\n")
            return
    deleted = delete_listing(skill_name)
    if not deleted:
        if not remote:
            c.print(f"[yellow]No local marketplace listing found for '{skill_name}'.[/yellow]\n")
        return
    c.print(f"[bold green]Unpublished:[/] {skill_name}")
    c.print("[dim]The local skill files were not deleted.[/]\n")


def do_marketplace_install(
    skill_name: str,
    category: str = "",
    console: Optional[Console] = None,
    force: bool = False,
) -> None:
    """Install a skill from app.notpunks.com marketplace."""
    from agent.wallet.context import build_wallet_context
    from hermes_cli.skill_marketplace import (
        check_skill_access,
        fetch_remote_listing,
        install_marketplace_skill,
        install_snft_from_metadata_url,
    )

    c = console or _console
    if not skill_name:
        c.print("[bold red]Usage:[/] /skills install-marketplace <skill-name> [--category <cat>]\n")
        return
    if skill_name.startswith(("http://", "https://")):
        try:
            result = install_snft_from_metadata_url(skill_name, category=category, force=force, console=c)
        except Exception as exc:
            c.print(f"[bold red]sNFT install failed:[/] {exc}\n")
            if "Security scan blocked install" in str(exc) and not force:
                c.print("[yellow]This sNFT skill needs explicit approval because the scan returned caution.[/]")
                c.print(f"Run: [bold]/skills install-snft {skill_name} --force[/]\n")
            return
        c.print(f"[bold green]Installed from sNFT cartridge:[/] {result['name']}")
        c.print(f"  Path: {result['path']}")
        c.print(f"  Bundle hash: {result['bundle_hash']}")
        c.print(f"  Scan: {result['scan_verdict']}")
        c.print("[dim]Use /reset or start a new session if you want the skill prompt cache refreshed immediately.[/]\n")
        return
    ctx = build_wallet_context()
    if not ctx:
        c.print("[red]No wallet connected. Run [bold]/wallet connect[/bold] first.[/red]\n")
        return
    try:
        listing = fetch_remote_listing(skill_name)
        allowed, reason = check_skill_access(ctx, listing)
    except Exception as exc:
        c.print(f"[bold red]Marketplace access check failed:[/] {exc}\n")
        return
    if not allowed:
        c.print(f"[bold red]Access denied:[/] {reason}\n")
        c.print("[dim]Buy the Skill NFT with /skills buy <skill-name>, then retry install.[/]\n")
        return
    try:
        result = install_marketplace_skill(skill_name, category=category, force=force, console=c)
    except Exception as exc:
        c.print(f"[bold red]Marketplace install failed:[/] {exc}\n")
        if "Security scan blocked install" in str(exc) and not force:
            c.print(f"[yellow]This marketplace skill needs explicit approval because the scan returned caution.[/]")
            c.print(f"Run: [bold]/skills install-marketplace {skill_name} --force[/]\n")
        return
    c.print(f"[bold green]Installed from marketplace:[/] {result['name']}")
    c.print(f"  Path: {result['path']}")
    c.print(f"  Bundle hash: {result['bundle_hash']}")
    c.print(f"  Scan: {result['scan_verdict']}")
    if result.get("source_kind") == "snft":
        c.print("  Source: sNFT encrypted cartridge")
    c.print("[dim]Use /reset or start a new session if you want the skill prompt cache refreshed immediately.[/]\n")


def do_marketplace_status(skill_name: str = "", console: Optional[Console] = None) -> None:
    """Show local install status for marketplace skills."""
    from hermes_cli.skill_marketplace import list_installed_marketplace_skills

    c = console or _console
    rows = list_installed_marketplace_skills(skill_name)
    if not rows:
        if skill_name:
            c.print(f"[yellow]Marketplace skill is not installed locally:[/] {skill_name}")
            c.print(f"[dim]Install it with: /skills install-marketplace {skill_name}[/]\n")
        else:
            c.print("[dim]No marketplace skills are installed locally.[/]\n")
        return

    table = Table(title="Marketplace Skill Install Status")
    table.add_column("Skill", style="bold cyan")
    table.add_column("Status")
    table.add_column("Path")
    table.add_column("Bundle")
    table.add_column("Scan")
    for row in rows:
        status = str(row.get("status") or "unknown")
        status_style = {
            "installed": "green",
            "modified": "yellow",
            "missing": "red",
        }.get(status, "yellow")
        bundle_hash = str(row.get("listingBundleHash") or row.get("bundleHash") or "")
        if len(bundle_hash) > 20:
            bundle_hash = f"{bundle_hash[:17]}..."
        table.add_row(
            str(row.get("skillId") or row.get("name") or ""),
            f"[{status_style}]{status}[/]",
            str(row.get("path") or ""),
            bundle_hash,
            str(row.get("scanVerdict") or "unknown"),
        )
    c.print(table)
    c.print("[dim]If status is modified or missing, reinstall with /skills install-marketplace <skill> --force.[/]\n")


def do_marketplace_buy(skill_name: str, console: Optional[Console] = None) -> None:
    """Create a local purchase intent and request a remote Skill NFT mint."""
    from agent.wallet.context import build_wallet_context
    from hermes_cli.skill_marketplace import (
        create_purchase_intent,
        create_purchase_intent_from_listing,
        fetch_remote_listing,
        fetch_skill_mint_status,
        load_listing,
        marketplace_skill_id,
        mint_proof_payload,
        mint_skill_nft_remote,
        record_marketplace_sale,
        request_publish_wallet_proof,
    )

    c = console or _console
    ctx = build_wallet_context()
    if not ctx:
        c.print("[red]No wallet connected. Run [bold]/wallet connect[/bold] first.[/red]\n")
        return
    requested_id = re.sub(r"[^a-zA-Z0-9_.-]+", "-", skill_name.strip()).strip("-").lower()
    if requested_id and requested_id in (ctx.skill_licenses or {}):
        c.print(f"[bold green]Already owned:[/] {requested_id}")
        c.print("  Skill NFT license is already detected in this wallet.\n")
        return
    try:
        listing = fetch_remote_listing(skill_name)
        skill_id = marketplace_skill_id(listing, skill_name)
        if skill_id in (ctx.skill_licenses or {}):
            c.print(f"[bold green]Already owned:[/] {skill_id}")
            c.print("  Skill NFT license is already detected in this wallet.\n")
            return
        intent = create_purchase_intent_from_listing(listing, wallet_address=ctx.wallet_address, source="remote")
    except FileNotFoundError:
        try:
            local_listing = load_listing(skill_name)
            if not local_listing:
                raise FileNotFoundError(skill_name)
            listing = local_listing
            intent = create_purchase_intent(skill_name, wallet_address=ctx.wallet_address)
        except FileNotFoundError:
            c.print(f"[bold red]Error:[/] Skill listing not found: {skill_name}\n")
            return
    except Exception as exc:
        local_listing = load_listing(skill_name)
        if not local_listing:
            c.print(f"[bold red]Marketplace lookup failed:[/] {exc}\n")
            return
        listing = local_listing
        intent = create_purchase_intent(skill_name, wallet_address=ctx.wallet_address)
    c.print(f"[bold yellow]Purchase intent created:[/] {intent['name']}")
    c.print(f"  Price: {intent['price_ton']} {intent['currency']}")
    if intent.get("bundle_hash"):
        c.print(f"  Bundle hash: {intent['bundle_hash']}")
    if intent.get("nft_metadata_url"):
        c.print(f"  Skill NFT metadata: {intent['nft_metadata_url']}")
    try:
        wallet_proof = request_publish_wallet_proof(
            listing,
            console=c,
            payload=mint_proof_payload(listing, ctx.wallet_address),
            prompt="Sign this Skill NFT mint request in your TON wallet.",
        )
        mint = mint_skill_nft_remote(listing, ctx.wallet_address, wallet_proof=wallet_proof)
        record_marketplace_sale(intent, listing=listing, mint=mint)
        mint_status = (mint.get("mint") or {}).get("status") if isinstance(mint.get("mint"), dict) else mint.get("status")
        c.print(f"  Remote mint status: {mint_status or 'pending'}")
        mint_payload = mint.get("mint") if isinstance(mint.get("mint"), dict) else mint
        if isinstance(mint_payload, dict):
            if mint_payload.get("txHash"):
                c.print(f"  Mint tx: {mint_payload['txHash']}")
            if mint_payload.get("nftAddress"):
                c.print(f"  NFT: {mint_payload['nftAddress']}")
        if mint.get("nftMetadataUrl"):
            c.print(f"  Remote metadata: {mint['nftMetadataUrl']}")
        try:
            status = fetch_skill_mint_status(skill_id, wallet_address=ctx.wallet_address)
            if status.get("confirmed"):
                c.print("  Indexed status: confirmed")
            elif status.get("pending"):
                c.print("  Indexed status: submitted")
        except Exception:
            pass
    except Exception as exc:
        c.print(f"  Remote mint: [yellow]pending[/] ({exc})")
    c.print("  Status: Skill NFT mint requested\n")


def do_marketplace_licenses(console: Optional[Console] = None) -> None:
    """List Skill NFT licenses detected in the connected wallet."""
    from agent.wallet.context import build_wallet_context

    c = console or _console
    ctx = build_wallet_context()
    if not ctx:
        c.print("[red]No wallet connected. Run [bold]/wallet connect[/bold] first.[/red]\n")
        return
    if not ctx.skill_licenses:
        c.print("[dim]No Skill NFT licenses detected in this wallet.[/]\n")
        return
    table = Table(title="Skill NFT Licenses")
    table.add_column("Skill", style="bold cyan")
    table.add_column("Tier")
    table.add_column("Status")
    table.add_column("NFT")
    for skill_id, license_data in sorted(ctx.skill_licenses.items()):
        source = str(license_data.get("source", "on_chain"))
        status = "confirmed" if source == "on_chain" else source.replace("_", " ")
        table.add_row(skill_id, str(license_data.get("tier", "standard")),
                      status, str(license_data.get("nftAddress") or license_data.get("name", "")))
    c.print(table)
    c.print()


def do_marketplace_earnings(console: Optional[Console] = None) -> None:
    """Show local marketplace earnings records."""
    from hermes_cli.skill_marketplace import backfill_sales_from_mint_intents, load_sales_records

    c = console or _console
    backfilled = backfill_sales_from_mint_intents()
    records = load_sales_records()
    total = sum(float(r.get("creator_amount_ton", 0) or 0) for r in records)
    c.print("[bold]Skill Marketplace Earnings[/bold]")
    c.print(f"  Sales records: {len(records)}")
    if backfilled:
        c.print(f"  Backfilled mint intents: {backfilled}")
    c.print(f"  Creator earnings: {total:g} TON")
    c.print("[dim]On-chain royalty settlement is pending contract integration.[/]\n")


# ---------------------------------------------------------------------------
# CLI argparse entry point
# ---------------------------------------------------------------------------

def skills_command(args) -> None:
    """Router for `notpunks skills <subcommand>` — called from hermes_cli/main.py."""
    action = getattr(args, "skills_action", None)

    if action == "browse":
        do_browse(page=args.page, page_size=args.size, source=args.source)
    elif action == "search":
        do_search(args.query, source=args.source, limit=args.limit)
    elif action == "install":
        do_install(args.identifier, category=args.category, force=args.force,
                   skip_confirm=getattr(args, "yes", False),
                   name_override=getattr(args, "name", "") or "")
    elif action in ("install-marketplace", "install_marketplace", "install-snft", "install_snft"):
        do_marketplace_install(
            args.name,
            category=getattr(args, "category", "") or "",
            force=getattr(args, "force", False),
        )
    elif action == "inspect":
        do_inspect(args.identifier)
    elif action == "create":
        do_create(
            args.name,
            from_file=getattr(args, "from_file", "") or "",
            category=getattr(args, "category", "") or "",
            interactive=True,
        )
    elif action == "list":
        do_list(
            source_filter=args.source,
            enabled_only=getattr(args, "enabled_only", False),
        )
    elif action == "check":
        do_check(name=getattr(args, "name", None))
    elif action == "update":
        do_update(name=getattr(args, "name", None))
    elif action == "audit":
        do_audit(name=getattr(args, "name", None))
    elif action == "uninstall":
        do_uninstall(args.name)
    elif action == "delete":
        do_delete(args.name)
    elif action == "reset":
        do_reset(args.name, restore=getattr(args, "restore", False),
                 skip_confirm=getattr(args, "yes", False))
    elif action == "publish":
        target = getattr(args, "to", "local")
        repo = getattr(args, "repo", "")
        if target == "local" and not repo:
            do_marketplace_publish(
                args.skill_path,
                price_ton=float(getattr(args, "price", 0.0) or 0.0),
                remote=bool(getattr(args, "remote", False)),
                use_admin_token=bool(getattr(args, "admin_token", False)),
            )
        else:
            do_publish(args.skill_path, target=target, repo=repo)
    elif action == "unpublish":
        do_marketplace_unpublish(
            args.name,
            remote=bool(getattr(args, "remote", False)),
            use_admin_token=bool(getattr(args, "admin_token", False)),
        )
    elif action == "snapshot":
        snap_action = getattr(args, "snapshot_action", None)
        if snap_action == "export":
            do_snapshot_export(args.output)
        elif snap_action == "import":
            do_snapshot_import(args.input, force=getattr(args, "force", False))
        else:
            _console.print("Usage: notpunks skills snapshot [export|import]\n")
    elif action == "tap":
        tap_action = getattr(args, "tap_action", None)
        repo = getattr(args, "repo", "") or getattr(args, "name", "")
        if not tap_action:
            _console.print("Usage: notpunks skills tap [list|add|remove]\n")
            return
        do_tap(tap_action, repo=repo)
    elif action == "access":
        do_marketplace_access(getattr(args, "name", "") or "")
    elif action in ("marketplace", "store"):
        do_marketplace_list()
    elif action in ("marketplace-status", "marketplace_status"):
        do_marketplace_status(getattr(args, "name", "") or "")
    elif action == "buy":
        do_marketplace_buy(args.name)
    elif action == "licenses":
        do_marketplace_licenses()
    elif action == "earnings":
        do_marketplace_earnings()
    else:
        _console.print("Usage: notpunks skills [browse|search|install|install-marketplace|install-snft|inspect|create|list|check|update|audit|uninstall|delete|reset|publish|unpublish|snapshot|tap|marketplace|marketplace-status|access|buy|licenses|earnings]\n")
        _console.print("Run 'notpunks skills <command> --help' for details.\n")


# ---------------------------------------------------------------------------
# Slash command entry point (/skills in chat)
# ---------------------------------------------------------------------------

def handle_skills_slash(cmd: str, console: Optional[Console] = None) -> None:
    """
    Parse and dispatch `/skills <subcommand> [args]` from the chat interface.

    Examples:
        /skills search kubernetes
        /skills install openai/skills/skill-creator
        /skills install openai/skills/skill-creator --force
        /skills install https://example.com/path/SKILL.md
        /skills install-marketplace my-skill
        /skills install-snft my-skill
        /skills inspect openai/skills/skill-creator
        /skills list
        /skills list --source hub
        /skills check
        /skills update
        /skills audit
        /skills audit my-skill
        /skills uninstall my-skill
        /skills delete my-local-skill
        /skills tap list
        /skills tap add owner/repo
        /skills tap remove owner/repo
        /skills marketplace
        /skills marketplace-status my-skill
        /skills access
        /skills buy my-skill
        /skills unpublish my-skill
        /skills licenses
        /skills earnings
    """
    c = console or _console
    parts = cmd.strip().split()

    # Strip the leading "/skills" if present
    if parts and parts[0].lower() == "/skills":
        parts = parts[1:]

    if not parts:
        _print_skills_help(c)
        return

    action = parts[0].lower()
    args = parts[1:]

    if action == "browse":
        page = 1
        page_size = 20
        source = "all"
        i = 0
        while i < len(args):
            if args[i] == "--page" and i + 1 < len(args):
                try:
                    page = int(args[i + 1])
                except ValueError:
                    pass
                i += 2
            elif args[i] == "--size" and i + 1 < len(args):
                try:
                    page_size = int(args[i + 1])
                except ValueError:
                    pass
                i += 2
            elif args[i] == "--source" and i + 1 < len(args):
                source = args[i + 1]
                i += 2
            else:
                i += 1
        do_browse(page=page, page_size=page_size, source=source, console=c)

    elif action == "search":
        if not args:
            c.print("[bold red]Usage:[/] /skills search <query> [--source skills-sh|well-known|github|official] [--limit N]\n")
            return
        source = "all"
        limit = 10
        query_parts = []
        i = 0
        while i < len(args):
            if args[i] == "--source" and i + 1 < len(args):
                source = args[i + 1]
                i += 2
            elif args[i] == "--limit" and i + 1 < len(args):
                try:
                    limit = int(args[i + 1])
                except ValueError:
                    pass
                i += 2
            else:
                query_parts.append(args[i])
                i += 1
        do_search(" ".join(query_parts), source=source, limit=limit, console=c)

    elif action == "install":
        if not args:
            c.print("[bold red]Usage:[/] /skills install <identifier-or-url> [--name <name>] [--category <cat>] [--force] [--now]\n")
            return
        identifier = args[0]
        category = ""
        name_override = ""
        # Slash commands run inside prompt_toolkit where input() hangs.
        # Always skip confirmation — the user typing the command is implicit consent.
        skip_confirm = True
        force = "--force" in args
        # --now invalidates prompt cache immediately (costs more money).
        # Default: defer to next session to preserve cache.
        invalidate_cache = "--now" in args
        for i, a in enumerate(args):
            if a == "--category" and i + 1 < len(args):
                category = args[i + 1]
            elif a == "--name" and i + 1 < len(args):
                name_override = args[i + 1]
        do_install(identifier, category=category, force=force,
                   skip_confirm=skip_confirm, invalidate_cache=invalidate_cache,
                   name_override=name_override, console=c)

    elif action in ("install-marketplace", "install_marketplace", "install-snft", "install_snft"):
        if not args:
            c.print("[bold red]Usage:[/] /skills install-marketplace <skill-name> [--category <cat>] [--force]\n")
            c.print("       /skills install-snft <metadata-url> [--category <cat>] [--force]\n")
            return
        category = ""
        force = "--force" in args
        for i, a in enumerate(args):
            if a == "--category" and i + 1 < len(args):
                category = args[i + 1]
        do_marketplace_install(args[0], category=category, force=force, console=c)

    elif action == "inspect":
        if not args:
            c.print("[bold red]Usage:[/] /skills inspect <identifier>\n")
            return
        do_inspect(args[0], console=c)

    elif action == "create":
        name = ""
        from_file = ""
        category = ""
        i = 0
        while i < len(args):
            a = args[i]
            if a == "--from-file" and i + 1 < len(args):
                from_file = args[i + 1]
                i += 2
            elif a == "--category" and i + 1 < len(args):
                category = args[i + 1]
                i += 2
            elif not a.startswith("--") and not name:
                name = a
                i += 1
            else:
                i += 1
        do_create(name, from_file=from_file, category=category, console=c, interactive=False)

    elif action == "list":
        source_filter = "all"
        enabled_only = "--enabled-only" in args or "--enabled" in args
        if "--source" in args:
            idx = args.index("--source")
            if idx + 1 < len(args):
                source_filter = args[idx + 1]
        do_list(source_filter=source_filter, enabled_only=enabled_only, console=c)

    elif action == "check":
        name = args[0] if args else None
        do_check(name=name, console=c)

    elif action == "update":
        name = args[0] if args else None
        do_update(name=name, console=c)

    elif action == "audit":
        name = args[0] if args else None
        do_audit(name=name, console=c)

    elif action == "uninstall":
        if not args:
            c.print("[bold red]Usage:[/] /skills uninstall <name> [--now]\n")
            return
        # Slash commands run inside prompt_toolkit where input() hangs.
        skip_confirm = True
        invalidate_cache = "--now" in args
        do_uninstall(args[0], console=c, skip_confirm=skip_confirm,
                     invalidate_cache=invalidate_cache)

    elif action == "delete":
        if not args:
            c.print("[bold red]Usage:[/] /skills delete <name> [--now]\n")
            return
        skip_confirm = True
        invalidate_cache = "--now" in args
        do_delete(args[0], console=c, skip_confirm=skip_confirm,
                  invalidate_cache=invalidate_cache)

    elif action == "reset":
        if not args:
            c.print("[bold red]Usage:[/] /skills reset <name> [--restore] [--now]\n")
            c.print("[dim]Clears the bundled-skills manifest entry so future updates stop marking it as user-modified.[/]")
            c.print("[dim]Pass --restore to also replace the current copy with the bundled version.[/]\n")
            return
        name = args[0]
        restore = "--restore" in args
        invalidate_cache = "--now" in args
        # Slash commands can't prompt — --restore in slash mode is implicit consent.
        do_reset(name, restore=restore, console=c, skip_confirm=True,
                 invalidate_cache=invalidate_cache)

    elif action == "publish":
        if not args:
            c.print("[bold red]Usage:[/] /skills publish <skill-name-or-path> [--price TON] [--remote] [--admin-token] [--to github] [--repo owner/repo]\n")
            return
        skill_path = args[0]
        target = "github"
        repo = ""
        price_ton = 0.0
        for i, a in enumerate(args):
            if a == "--to" and i + 1 < len(args):
                target = args[i + 1]
            if a == "--repo" and i + 1 < len(args):
                repo = args[i + 1]
            if a == "--price" and i + 1 < len(args):
                try:
                    price_ton = float(args[i + 1])
                except ValueError:
                    price_ton = 0.0
        if "--repo" in args or target != "github":
            do_publish(skill_path, target=target, repo=repo, console=c)
        else:
            do_marketplace_publish(
                skill_path,
                price_ton=price_ton,
                console=c,
                remote="--remote" in args,
                use_admin_token="--admin-token" in args,
            )

    elif action == "unpublish":
        if not args:
            c.print("[bold red]Usage:[/] /skills unpublish <skill-name> [--remote] [--admin-token]\n")
            return
        skill_name = next((arg for arg in args if not arg.startswith("--")), "")
        do_marketplace_unpublish(
            skill_name,
            console=c,
            remote="--remote" in args,
            use_admin_token="--admin-token" in args,
        )

    elif action == "access":
        do_marketplace_access(args[0] if args else "", console=c)

    elif action in ("marketplace", "store"):
        do_marketplace_list(console=c)

    elif action in ("marketplace-status", "marketplace_status"):
        do_marketplace_status(args[0] if args else "", console=c)

    elif action == "buy":
        if not args:
            c.print("[bold red]Usage:[/] /skills buy <skill-name>\n")
            return
        do_marketplace_buy(args[0], console=c)

    elif action == "licenses":
        do_marketplace_licenses(console=c)

    elif action == "earnings":
        do_marketplace_earnings(console=c)

    elif action == "snapshot":
        if not args:
            c.print("[bold red]Usage:[/] /skills snapshot export <file> | /skills snapshot import <file>\n")
            return
        snap_action = args[0]
        if snap_action == "export" and len(args) > 1:
            do_snapshot_export(args[1], console=c)
        elif snap_action == "import" and len(args) > 1:
            force = "--force" in args
            do_snapshot_import(args[1], force=force, console=c)
        else:
            c.print("[bold red]Usage:[/] /skills snapshot export <file> | /skills snapshot import <file>\n")

    elif action == "tap":
        if not args:
            do_tap("list", console=c)
            return
        tap_action = args[0]
        repo = args[1] if len(args) > 1 else ""
        do_tap(tap_action, repo=repo, console=c)

    elif action in ("help", "--help", "-h"):
        _print_skills_help(c)

    else:
        c.print(f"[bold red]Unknown action:[/] {action}")
        _print_skills_help(c)


def _print_skills_help(console: Console) -> None:
    """Print help for the /skills slash command."""
    console.print(Panel(
        "[bold]Skills Hub Commands:[/]\n\n"
        "  [cyan]browse[/] [--source official]   Browse all available skills (paginated)\n"
        "  [cyan]search[/] <query>              Search registries for skills\n"
        "  [cyan]install[/] <identifier>        Install a skill (with security scan)\n"
        "  [cyan]install-marketplace[/] <skill> Install from NOTPUNKS marketplace\n"
        "  [cyan]install-snft[/] <skill|url>   Install via sNFT encrypted cartridge when available\n"
        "  [cyan]inspect[/] <identifier>        Preview a skill without installing\n"
        "  [cyan]create[/] <name> [--category]  Create a starter skill (NFT-gated)\n"
        "  [cyan]create[/] <name> --from-file <path>\n"
        "       Import a prepared SKILL.md file\n"
        "  [cyan]list[/] [--source hub|builtin|local] [--enabled-only]\n"
        "       List installed skills; --enabled-only filters to the active profile's live set\n"
        "  [cyan]check[/] [name]                Check hub skills for upstream updates\n"
        "  [cyan]update[/] [name]               Update hub skills with upstream changes\n"
        "  [cyan]audit[/] [name]                Re-scan hub skills for security\n"
        "  [cyan]uninstall[/] <name>            Remove a hub-installed skill\n"
        "  [cyan]delete[/] <name>               Delete a local skill\n"
        "  [cyan]reset[/] <name> [--restore]    Reset bundled-skill tracking (fix 'user-modified' flag)\n"
        "  [cyan]publish[/] <skill> [--price N] [--remote] Create marketplace listing\n"
        "  [cyan]unpublish[/] <skill> [--remote] Remove marketplace listing\n"
        "  [cyan]marketplace[/]                 List local marketplace skills\n"
        "  [cyan]marketplace-status[/] [skill]  Show local marketplace install status\n"
        "  [cyan]access[/] [skill]              Show wallet/skill marketplace access\n"
        "  [cyan]buy[/] <skill>                 Create pending Skill NFT purchase intent\n"
        "  [cyan]licenses[/]                    List owned Skill NFT licenses\n"
        "  [cyan]earnings[/]                    Show local creator earnings records\n"
        "  [cyan]snapshot[/] export|import      Export/import skill configurations\n"
        "  [cyan]tap[/] list|add|remove         Manage skill sources\n",
        title="/skills",
    ))
