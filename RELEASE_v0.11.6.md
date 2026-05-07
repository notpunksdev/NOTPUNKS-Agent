# NOTPUNKS Agent v0.11.6

This release adds protected sNFT runtime execution for wallet-gated Skill NFT cartridges.

## Highlights

- Protected sNFT install path for encrypted marketplace cartridges.
- Encrypted skills no longer write plaintext `SKILL.md` to disk by default.
- New protected runtime execution tool for running encrypted skills through the agent dialog.
- Memory hardening best-effort for decrypted skill payloads: no core dumps, non-dumpable process mode, locked mutable buffers where supported, and zeroization after execution.
- Marketplace compatibility for `encrypted_external` cartridge metadata and unlock endpoints.

## Notes

Protected sNFT reduces casual copying and public source exposure. It is not a claim of absolute protection against a fully compromised host or modified runtime.
