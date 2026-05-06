#!/usr/bin/env python3
"""Build NOTPUNKS Agent release archives.

This creates portable source/runtime bundles that can be attached to a GitHub
Release. The bundles are intentionally simple: users can unpack them, inspect
the code, and run the installer/setup from the included files.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import tarfile
import zipfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT_DIR = REPO_ROOT / "dist" / "release-assets"

EXCLUDE_DIRS = {
    ".git",
    ".github",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "htmlcov",
    "node_modules",
    "venv",
}

EXCLUDE_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".log",
    ".sqlite",
    ".db",
}

EXCLUDE_FILES = {
    ".DS_Store",
    ".env",
}


def should_include(path: Path) -> bool:
    rel = path.relative_to(REPO_ROOT)
    parts = set(rel.parts)
    if parts & EXCLUDE_DIRS:
        return False
    if path.name in EXCLUDE_FILES:
        return False
    if path.suffix in EXCLUDE_SUFFIXES:
        return False
    return True


def iter_release_files() -> list[Path]:
    files: list[Path] = []
    for path in REPO_ROOT.rglob("*"):
        if path.is_file() and should_include(path):
            files.append(path)
    return sorted(files)


def read_version() -> str:
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    for line in pyproject.splitlines():
        if line.startswith("version = "):
            return line.split("=", 1)[1].strip().strip('"')
    return "0.0.0"


def write_install_readme(staging_root: Path, artifact_name: str) -> None:
    (staging_root / "README_INSTALL.txt").write_text(
        f"""NOTPUNKS Agent release bundle: {artifact_name}

Quick install:
  curl -fsSL https://agent.notpunks.com/install | bash

Install from this unpacked bundle:
  cd {artifact_name}
  uv sync
  uv run notpunks setup
  uv run notpunks

Windows:
  Use WSL for the agent runtime:
    irm https://agent.notpunks.com/install.ps1 | iex

Update an installed agent:
  notpunks update
  notpunks doctor
""",
        encoding="utf-8",
    )


def stage_bundle(staging_dir: Path, bundle_root_name: str) -> Path:
    bundle_root = staging_dir / bundle_root_name
    if bundle_root.exists():
        shutil.rmtree(bundle_root)
    bundle_root.mkdir(parents=True)

    for src in iter_release_files():
        rel = src.relative_to(REPO_ROOT)
        dst = bundle_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    write_install_readme(bundle_root, bundle_root_name)
    return bundle_root


def make_tar_gz(bundle_root: Path, out_path: Path) -> None:
    with tarfile.open(out_path, "w:gz") as tar:
        tar.add(bundle_root, arcname=bundle_root.name)


def make_zip(bundle_root: Path, out_path: Path) -> None:
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in sorted(bundle_root.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(bundle_root.parent))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description="Build NOTPUNKS Agent release archives")
    parser.add_argument("--tag", default=os.environ.get("GITHUB_REF_NAME") or f"v{read_version()}")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    args = parser.parse_args()

    tag = args.tag.lstrip("v")
    out_dir: Path = args.out_dir
    staging_dir = out_dir / ".staging"
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    staging_dir.mkdir(parents=True, exist_ok=True)

    artifacts = [
        ("linux-x64", "tar.gz"),
        ("macos-arm64", "tar.gz"),
        ("windows-wsl", "zip"),
        ("source", "tar.gz"),
    ]

    built: list[Path] = []
    for platform, ext in artifacts:
        bundle_name = f"NOTPUNKS-Agent-{tag}-{platform}"
        bundle_root = stage_bundle(staging_dir, bundle_name)
        out_path = out_dir / f"{bundle_name}.{ext}"
        if ext == "zip":
            make_zip(bundle_root, out_path)
        else:
            make_tar_gz(bundle_root, out_path)
        built.append(out_path)

    checksums = out_dir / "checksums.txt"
    checksums.write_text(
        "".join(f"{sha256(path)}  {path.name}\n" for path in built),
        encoding="utf-8",
    )
    built.append(checksums)

    shutil.rmtree(staging_dir, ignore_errors=True)

    print("Built release artifacts:")
    for path in built:
        print(f"  {path.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
