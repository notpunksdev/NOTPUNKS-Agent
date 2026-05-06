#!/usr/bin/env python3
"""Reference verifier for sNFT Protocol v1 encrypted skill cartridges."""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import tarfile
from typing import Any
from urllib.parse import urljoin

import httpx
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def _sha256_prefixed(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _snft(metadata: dict[str, Any]) -> dict[str, Any]:
    value = metadata.get("snft")
    if not isinstance(value, dict):
        raise ValueError("metadata does not contain an snft object")
    return value


def _fetch_json(url: str) -> dict[str, Any]:
    response = httpx.get(url, timeout=30)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError(f"{url} did not return a JSON object")
    return payload


def _decrypt(encrypted_payload: bytes, unlock_data: dict[str, Any]) -> bytes:
    if unlock_data.get("encryption") != "aes-256-gcm":
        raise ValueError(f"unsupported encryption: {unlock_data.get('encryption') or 'unknown'}")
    key = base64.b64decode(str(unlock_data.get("key") or ""))
    nonce = base64.b64decode(str(unlock_data.get("nonce") or ""))
    aad = str(unlock_data.get("aad") or "").encode("utf-8")
    if len(key) != 32:
        raise ValueError("unlock key must be 32 bytes for aes-256-gcm")
    if len(nonce) != 12:
        raise ValueError("nonce must be 12 bytes for aes-256-gcm")
    return AESGCM(key).decrypt(nonce, encrypted_payload, aad)


def _tar_summary(payload: bytes) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as tar:
        for member in tar.getmembers():
            if member.isdir():
                continue
            out.append({"path": member.name, "size": member.size})
    return out


def verify(metadata_url: str, *, unlock_token: str = "") -> dict[str, Any]:
    metadata = _fetch_json(metadata_url)
    snft = _snft(metadata)
    content = snft.get("content") if isinstance(snft.get("content"), dict) else {}
    if content.get("mode") != "encrypted_external":
        raise ValueError("only encrypted_external sNFT content is supported")
    uris = content.get("uris") if isinstance(content.get("uris"), list) else []
    cartridge_url = urljoin(metadata_url, str(uris[0] if uris else "cartridge"))
    cartridge = httpx.get(cartridge_url, timeout=60)
    cartridge.raise_for_status()
    encrypted_payload = cartridge.content

    encrypted_hash = str(content.get("encrypted_sha256") or "")
    actual_encrypted_hash = _sha256_prefixed(encrypted_payload)
    if encrypted_hash and actual_encrypted_hash != encrypted_hash:
        raise ValueError(f"encrypted hash mismatch: expected {encrypted_hash}, got {actual_encrypted_hash}")

    unlock = snft.get("unlock") if isinstance(snft.get("unlock"), dict) else {}
    unlock_endpoint = str(unlock.get("endpoint") or "unlock")
    unlock_url = urljoin(metadata_url, unlock_endpoint)
    headers = {"X-NOTPUNKS-Publish-Token": unlock_token} if unlock_token else {}
    unlock_response = httpx.post(unlock_url, json={"skillId": metadata.get("skill_id", "")}, headers=headers, timeout=60)
    unlock_response.raise_for_status()
    unlock_payload = unlock_response.json()
    if not unlock_payload.get("success"):
        raise RuntimeError(unlock_payload.get("error") or "unlock failed")
    unlock_data = unlock_payload.get("data") if isinstance(unlock_payload.get("data"), dict) else {}

    plaintext = _decrypt(encrypted_payload, unlock_data)
    plaintext_hash = str(unlock_data.get("plaintextSha256") or content.get("plaintext_sha256") or "")
    actual_plaintext_hash = _sha256_prefixed(plaintext)
    if plaintext_hash and actual_plaintext_hash != plaintext_hash:
        raise ValueError(f"plaintext hash mismatch: expected {plaintext_hash}, got {actual_plaintext_hash}")

    return {
        "ok": True,
        "metadata_url": metadata_url,
        "cartridge_url": cartridge_url,
        "unlock_url": unlock_url,
        "skill_id": metadata.get("skill_id") or metadata.get("skillId") or metadata.get("name"),
        "encrypted_sha256": actual_encrypted_hash,
        "plaintext_sha256": actual_plaintext_hash,
        "files": _tar_summary(plaintext),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify and decrypt an sNFT Protocol v1 encrypted cartridge.")
    parser.add_argument("metadata_url", help="Skill NFT metadata URL")
    parser.add_argument("--unlock-token", default="", help="Optional unlock/admin token for the issuer endpoint")
    args = parser.parse_args()
    print(json.dumps(verify(args.metadata_url, unlock_token=args.unlock_token), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
