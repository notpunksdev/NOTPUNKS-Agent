---
sidebar_position: 12
---

# sNFT Protocol

sNFT Protocol (Skill NFT Protocol) packages an installable agent skill as an NFT-backed cartridge.
The NFT metadata points to encrypted cartridge bytes, while the issuer unlock endpoint releases the
decryption material only after the wallet/license check succeeds.

## Goals

- Keep skill code portable across agents that implement the protocol.
- Let creators distribute paid skills without exposing the full bundle publicly.
- Keep the cartridge self-describing through standard NFT metadata.
- Preserve local installation safety: decrypt, verify hashes, unpack safely, scan, then install.

## Metadata

An sNFT-compatible metadata object includes a top-level `snft` descriptor:

```json
{
  "name": "NOTPUNKS Skill NFT: deploy-bot",
  "type": "notpunks_skill_nft",
  "skill_id": "deploy-bot",
  "bundle_hash": "sha256:<deterministic-file-manifest-hash>",
  "snft": {
    "protocol": "snft",
    "version": "1.0",
    "content": {
      "mode": "encrypted_external",
      "encryption": "aes-256-gcm",
      "compression": "tar.gz",
      "encrypted_sha256": "sha256:<encrypted-cartridge-hash>",
      "plaintext_sha256": "sha256:<decrypted-tar-gz-hash>",
      "uris": [
        "https://app.notpunks.com/api/skills/marketplace/deploy-bot/cartridge"
      ]
    },
    "unlock": {
      "endpoint": "https://app.notpunks.com/api/skills/marketplace/deploy-bot/unlock"
    },
    "chains": [
      {
        "chain": "ton",
        "standard": "TEP-62",
        "metadata_standard": "TEP-64",
        "proof": {
          "method": "ton_proof",
          "collection_address": "EQ...",
          "item_address": "EQ...",
          "challenge": "snft-unlock:deploy-bot:sha256:<encrypted-cartridge-hash>"
        }
      },
      {
        "chain": "evm",
        "standard": "ERC-721",
        "metadata_standard": "ERC-721 Metadata JSON",
        "proof": {
          "method": "eip712",
          "chain_id": 1,
          "contract": "0x0000000000000000000000000000000000000000",
          "token_id": "1",
          "challenge": "snft-unlock:deploy-bot:sha256:<encrypted-cartridge-hash>"
        }
      },
      {
        "chain": "solana",
        "standard": "Metaplex NFT",
        "metadata_standard": "Metaplex JSON",
        "proof": {
          "method": "solana_sign_message",
          "mint": "11111111111111111111111111111111",
          "challenge": "snft-unlock:deploy-bot:sha256:<encrypted-cartridge-hash>"
        }
      }
    ]
  }
}
```

Relative URLs are allowed. If `uris[0]` or `unlock.endpoint` is relative, clients resolve it
against the metadata URL.

## Client Install Flow

1. Fetch the NFT metadata URL.
2. Read `snft.content`; require `mode = encrypted_external` and `encryption = aes-256-gcm`.
3. Download the cartridge from `snft.content.uris[0]`.
4. Verify `sha256(cartridge_bytes)` equals `snft.content.encrypted_sha256`.
5. Select an adapter from `snft.chains`. The default preference is `ton`; set `NOTPUNKS_SNFT_CHAIN=evm` or `NOTPUNKS_SNFT_CHAIN=solana` to prefer another chain.
6. If no issuer token is configured, create a normalized `unlockRequest`:
   - TON: the agent asks the connected TON wallet to sign `ton_proof`.
   - EVM: the agent opens a local browser signer and asks MetaMask or another injected EVM wallet to sign EIP-712 typed data.
   - Solana: the agent opens a local browser signer and asks Phantom to sign a wallet message.
7. POST the `unlockRequest` to `snft.unlock.endpoint`.
8. Decrypt the cartridge with AES-256-GCM using the returned `key`, `nonce`, and `aad`.
9. Verify `sha256(decrypted_bytes)` equals `plaintextSha256` from the unlock response or `snft.content.plaintext_sha256`.
10. Extract the tar.gz using safe path checks.
11. Verify the deterministic skill manifest hash against `bundle_hash` when present.
12. Run the normal skill security scan.
13. Install only if the scan policy allows it, or if the user explicitly passes `--force` for caution-level findings.

## Multi-Chain Unlock Request

`/skills install-marketplace` and `notpunks skills install-snft` send the same normalized
request shape for every supported chain:

```json
{
  "protocol": "snft",
  "version": "1.0",
  "skill_id": "deploy-bot",
  "chain": "evm",
  "wallet": "0x...",
  "challenge": "snft-unlock:deploy-bot:sha256:<encrypted-cartridge-hash>",
  "nft": {
    "chain_id": 1,
    "contract": "0x0000000000000000000000000000000000000000",
    "token_id": "1",
    "standard": "ERC-721"
  },
  "proof": {
    "method": "eip712",
    "wallet": "0x...",
    "signature": "0x...",
    "typedData": {
      "domain": { "name": "sNFT Protocol" },
      "message": { "skill_id": "deploy-bot" }
    }
  }
}
```

For EVM and Solana, the interactive agent opens a temporary local signer page on
`127.0.0.1`, receives the signed `unlockRequest` through a local callback, and closes
the server after the request is received or the timeout expires. For automation and CI,
you can bypass the browser signer with `NOTPUNKS_SNFT_UNLOCK_REQUEST` or
`NOTPUNKS_SNFT_UNLOCK_REQUEST_FILE`.

## NOTPUNKS Commands

Install by marketplace slug. This checks the local wallet context before install and then prefers
the encrypted cartridge when metadata is available:

```bash
notpunks skills install-marketplace deploy-bot --force
```

Install directly from any sNFT metadata URL:

```bash
notpunks skills install-snft https://app.notpunks.com/api/skills/marketplace/deploy-bot/metadata --force
```

Prefer a non-TON adapter:

```bash
NOTPUNKS_SNFT_CHAIN=evm notpunks skills install-marketplace deploy-bot
NOTPUNKS_SNFT_CHAIN=solana notpunks skills install-snft https://example.com/skill/metadata.json
```

The same commands work inside the agent:

```text
/skills install-marketplace deploy-bot --force
/skills install-snft https://app.notpunks.com/api/skills/marketplace/deploy-bot/metadata --force
```

## Reference Verifier

The repo ships a minimal verifier for third-party integrators:

```bash
python scripts/snft_verify.py https://app.notpunks.com/api/skills/marketplace/deploy-bot/metadata \
  --unlock-token "$NOTPUNKS_MARKETPLACE_UNLOCK_TOKEN"
```

The verifier fetches metadata, downloads the encrypted cartridge, checks hashes, asks the issuer
unlock endpoint for AES-GCM material, decrypts the bundle, verifies the plaintext hash, and prints
the files inside the cartridge. It does not install the skill.

## Contract Boundary

v1 treats the unlock endpoint as the enforcement boundary. The endpoint must verify one of:

- the caller owns the specific Skill NFT license;
- the caller has an allowed ecosystem role, such as a NOT PUNKS holder role;
- an issuer/admin token is supplied by trusted infrastructure.

When the TON Skill NFT collection contract is deployed, clients do not need a new cartridge format.
Only the issuer unlock implementation changes from local/mock license records to on-chain ownership
verification and royalty settlement.
