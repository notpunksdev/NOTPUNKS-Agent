"""Data models for TON Wallet and NFT integration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class WalletConnection:
    """Represents a connected TON wallet."""

    address: str
    network: str = "mainnet"  # "mainnet" | "testnet"
    verified: bool = False
    public_key: str = ""
    device_info: dict = field(default_factory=dict)
    connected_at: float = 0.0  # Unix timestamp

    def to_dict(self) -> dict:
        return {
            "address": self.address,
            "network": self.network,
            "verified": self.verified,
            "public_key": self.public_key,
            "device_info": self.device_info,
            "connected_at": self.connected_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> WalletConnection:
        return cls(
            address=data.get("address", ""),
            network=data.get("network", "mainnet"),
            verified=data.get("verified", False),
            public_key=data.get("public_key", ""),
            device_info=data.get("device_info", {}),
            connected_at=data.get("connected_at", 0.0),
        )


@dataclass
class NFTItem:
    """Represents a single NFT item owned by the wallet."""

    address: str
    collection_address: str
    owner_address: str
    index: int = 0
    metadata: dict = field(default_factory=dict)
    name: str = ""
    description: str = ""
    image: str = ""
    rarity: str = "common"
    level: int = 1

    def to_dict(self) -> dict:
        return {
            "address": self.address,
            "collection_address": self.collection_address,
            "owner_address": self.owner_address,
            "index": self.index,
            "metadata": self.metadata,
            "name": self.name,
            "description": self.description,
            "image": self.image,
            "rarity": self.rarity,
            "level": self.level,
        }

    @classmethod
    def from_dict(cls, data: dict) -> NFTItem:
        return cls(
            address=data.get("address", ""),
            collection_address=data.get("collection_address", ""),
            owner_address=data.get("owner_address", ""),
            index=data.get("index", 0),
            metadata=data.get("metadata", {}),
            name=data.get("name", ""),
            description=data.get("description", ""),
            image=data.get("image", ""),
            rarity=data.get("rarity", "common"),
            level=data.get("level", 1),
        )


@dataclass
class SkillMode:
    """Represents an unlocked agent skill mode derived from an NFT."""

    mode_id: str
    name: str
    description: str
    collection_key: str
    collection_address: str
    prompt_modifier: str
    nft_item: NFTItem | None = None

    def to_dict(self) -> dict:
        return {
            "mode_id": self.mode_id,
            "name": self.name,
            "description": self.description,
            "collection_key": self.collection_key,
            "collection_address": self.collection_address,
            "prompt_modifier": self.prompt_modifier,
            "nft_item": self.nft_item.to_dict() if self.nft_item else None,
        }


@dataclass
class WalletContext:
    """Full wallet + NFT context for system prompt injection."""

    wallet_address: str
    verified: bool
    network: str
    active_modes: list[SkillMode] = field(default_factory=list)
    nfts: list[NFTItem] = field(default_factory=list)
    can_create_skills: bool = False
    can_publish_skills: bool = False
    can_use_custom_skills: bool = False
    skill_pass: bool = False
    paid_skill_slots: int = 0
    matching_pair_numbers: list[int] = field(default_factory=list)
    access_roles: list[str] = field(default_factory=list)
    access_tiers: list[str] = field(default_factory=list)
    skill_licenses: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "wallet_address": self.wallet_address,
            "verified": self.verified,
            "network": self.network,
            "active_modes": [m.to_dict() for m in self.active_modes],
            "nfts": [n.to_dict() for n in self.nfts],
            "can_create_skills": self.can_create_skills,
            "can_publish_skills": self.can_publish_skills,
            "can_use_custom_skills": self.can_use_custom_skills,
            "skill_pass": self.skill_pass,
            "paid_skill_slots": self.paid_skill_slots,
            "matching_pair_numbers": self.matching_pair_numbers,
            "access_roles": self.access_roles,
            "access_tiers": self.access_tiers,
            "skill_licenses": self.skill_licenses,
        }

    def is_empty(self) -> bool:
        return not self.wallet_address

    def has_mode(self, mode_id: str) -> bool:
        return any(m.mode_id == mode_id for m in self.active_modes)
