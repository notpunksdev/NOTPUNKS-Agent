"""Skill mode mapper — maps NFT collections to agent skill modes.

Reads configuration from config.yaml under the ``nft.collections`` key
and translates found NFTs into active skill modes with prompt modifiers.
"""

from __future__ import annotations

from hermes_cli.config import load_config

from .models import NFTItem, SkillMode


class SkillMapper:
    """Maps NFT collections to agent skill modes."""

    def __init__(self):
        self._config = load_config()

    def reload(self) -> None:
        """Reload configuration from disk."""
        self._config = load_config()

    def map_nfts_to_modes(self, nfts: list[NFTItem]) -> list[SkillMode]:
        """Convert a list of NFTs into active skill modes.

        Each NFT whose collection_address matches a configured collection
        unlocks the corresponding skill mode. If the config specifies an
        ``element_filter``, the NFT must also have a matching ``Element``
        attribute (e.g. ``element earth``).
        """
        collections_cfg = self._config.get("nft", {}).get("collections", {})
        modes: list[SkillMode] = []
        seen_modes: set[str] = set()

        for nft in nfts:
            for key, cfg in collections_cfg.items():
                if cfg.get("address", "").strip() != nft.collection_address.strip():
                    continue

                # Check element filter if configured
                element_filter = cfg.get("element_filter", "").strip().lower()
                if element_filter:
                    nft_element = nft.metadata.get("_element", "").lower()
                    # Support both "element earth" and "earth" forms
                    if element_filter not in nft_element and nft_element not in element_filter:
                        continue

                mode_id = cfg.get("skill_mode", key)
                if mode_id in seen_modes:
                    continue
                seen_modes.add(mode_id)

                modes.append(SkillMode(
                    mode_id=mode_id,
                    name=cfg.get("skill_mode", key).replace("_", " ").title(),
                    description=cfg.get("description", f"{key} mode"),
                    collection_key=key,
                    collection_address=nft.collection_address,
                    prompt_modifier=cfg.get("prompt_modifier", ""),
                    nft_item=nft,
                ))

        return modes

    def get_prompt_modifier(self, modes: list[SkillMode]) -> str:
        """Build a combined prompt modifier from active skill modes.

        Loads modifier text either inline from config or from a skill file.
        """
        if not modes:
            return ""

        parts: list[str] = []
        for mode in modes:
            modifier = self._load_modifier_text(mode)
            if modifier:
                parts.append(f"## {mode.name}\n{modifier}")

        if not parts:
            return ""

        header = (
            "## NFT-UNLOCKED AGENT MODES\n"
            "The following modes are active based on the user's NFT holdings. "
            "Apply ALL relevant modes to your responses automatically.\n"
        )
        return header + "\n\n".join(parts)

    def _load_modifier_text(self, mode: SkillMode) -> str:
        """Load prompt modifier text for a skill mode.

        Tries (in order):
        1. Inline text from config nft.collections.<key>.prompt_modifier_text
        2. Skill file referenced by nft.collections.<key>.prompt_modifier
        3. Fallback empty
        """
        collections_cfg = self._config.get("nft", {}).get("collections", {})
        cfg = collections_cfg.get(mode.collection_key, {})

        # 1. Inline text
        inline = cfg.get("prompt_modifier_text", "")
        if inline:
            return inline.strip()

        # 2. Skill file reference
        skill_name = mode.prompt_modifier
        if not skill_name:
            return ""

        from agent.skill_utils import iter_skill_index_files, get_external_skills_dirs
        from agent.skill_preprocessing import preprocess_skill_content
        from pathlib import Path
        from tools.skills_tool import SKILLS_DIR

        dirs_to_scan = [SKILLS_DIR] + get_external_skills_dirs()
        for scan_dir in dirs_to_scan:
            for skill_md in iter_skill_index_files(scan_dir, "SKILL.md"):
                skill_dir = skill_md.parent
                if skill_dir.name == skill_name or skill_md.stem == skill_name:
                    try:
                        content = skill_md.read_text(encoding="utf-8")
                        content = preprocess_skill_content(content, str(skill_dir))
                        return content.strip()
                    except Exception:
                        break

        return ""

    def get_premium_modes(self) -> list[SkillMode]:
        """Return all premium skill modes defined in config (regardless of holdings).

        Collections marked with ``premium: true`` are considered premium.
        """
        collections_cfg = self._config.get("nft", {}).get("collections", {})
        modes: list[SkillMode] = []
        seen_modes: set[str] = set()

        for key, cfg in collections_cfg.items():
            if not cfg.get("premium", False):
                continue
            mode_id = cfg.get("skill_mode", key)
            if mode_id in seen_modes:
                continue
            seen_modes.add(mode_id)

            modes.append(SkillMode(
                mode_id=mode_id,
                name=cfg.get("skill_mode", key).replace("_", " ").title(),
                description=cfg.get("description", f"{key} mode"),
                collection_key=key,
                collection_address=cfg.get("address", ""),
                prompt_modifier=cfg.get("prompt_modifier", ""),
                nft_item=None,
            ))

        return modes

    def check_skill_access(self, skill_name: str) -> tuple[bool, str]:
        """Check whether a skill is accessible given current NFT holdings.

        Returns (allowed, reason).

        A skill can declare NFT requirements in its SKILL.md frontmatter:
            metadata:
              hermes:
                requires_nft: earth_kid
        """
        # TODO: implement after skill frontmatter parsing is extended
        return True, ""
