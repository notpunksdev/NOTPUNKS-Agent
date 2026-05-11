"""OpenRouter provider routing helpers."""

from __future__ import annotations

from typing import Any, Mapping


KIMI_K26_LONG_CONTEXT_PROVIDERS = [
    "Moonshot AI",
    "DeepInfra",
    "Fireworks",
    "Together",
    "SiliconFlow",
    "Parasail",
    "Inceptron",
    "Novita",
    "Cloudflare",
]


def is_kimi_k26_model(model: str | None) -> bool:
    """Return True for OpenRouter's Kimi K2.6 model ids."""
    model_lower = (model or "").strip().lower()
    return "moonshotai/kimi-k2.6" in model_lower or "kimi-k2.6" == model_lower


def apply_openrouter_model_routing(
    model: str | None,
    preferences: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply model-specific OpenRouter routing safeguards.

    OpenRouter's aggregate metadata for ``moonshotai/kimi-k2.6`` can point at
    the Io Net endpoint, which currently reports a 32K context window while
    most other endpoints expose 256K. For that model, prefer known long-context
    endpoints and keep Io Net out of automatic fallback unless the user
    explicitly requested it.
    """
    prefs = dict(preferences or {})
    if not is_kimi_k26_model(model):
        return prefs

    explicit_only = prefs.get("only")
    explicit_order = prefs.get("order")
    explicit_names = []
    if isinstance(explicit_only, list):
        explicit_names.extend(str(name).strip().lower() for name in explicit_only)
    if isinstance(explicit_order, list):
        explicit_names.extend(str(name).strip().lower() for name in explicit_order)
    user_requested_io_net = any(name in {"io net", "io-net", "ionet"} for name in explicit_names)
    if user_requested_io_net:
        return prefs

    if not explicit_only and not explicit_order:
        prefs["order"] = list(KIMI_K26_LONG_CONTEXT_PROVIDERS)
        prefs.setdefault("allow_fallbacks", True)

    if not explicit_only:
        ignored = list(prefs.get("ignore") or [])
        if not any(str(name).strip().lower() in {"io net", "io-net", "ionet"} for name in ignored):
            ignored.append("Io Net")
        prefs["ignore"] = ignored

    return prefs
