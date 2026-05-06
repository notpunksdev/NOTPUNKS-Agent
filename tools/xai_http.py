"""Shared helpers for direct xAI HTTP integrations."""

from __future__ import annotations


def hermes_xai_user_agent() -> str:
    """Return a stable NOTPUNKS-specific User-Agent for xAI HTTP calls."""
    try:
        from hermes_cli import __version__
    except Exception:
        __version__ = "unknown"
    return f"NOTPUNKS-Agent/{__version__}"
