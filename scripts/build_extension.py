#!/usr/bin/env python3
"""Assemble and package the Claude Desktop extension (.dxt).

A .dxt file is a ZIP archive containing ``manifest.json`` at its root plus the
bundled server. This script:

  1. Stages ``build/extension/`` with the manifest, the ``hpe_mist_mcp``
     package, and the ``server/main.py`` bootstrap.
  2. Installs runtime dependencies into ``server/lib`` (vendored, so the
     extension runs without touching the user's global environment).
  3. Zips the staged directory into ``dist/hpe-networking-assistant-<version>.dxt``.

Usage:
    python scripts/build_extension.py [--no-deps]

The version is read from manifest.json so it stays in sync with the release.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build" / "extension"
DIST = ROOT / "dist"


def load_version() -> str:
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    return manifest["version"]


def runtime_requirements() -> list[str]:
    """Return real (non-comment) lines from requirements.txt, if any."""
    req = ROOT / "requirements.txt"
    if not req.exists():
        return []
    out = []
    for line in req.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def stage(install_deps: bool = True) -> None:
    if BUILD.exists():
        shutil.rmtree(BUILD)
    (BUILD / "server").mkdir(parents=True)

    # manifest + optional icon
    shutil.copy2(ROOT / "manifest.json", BUILD / "manifest.json")
    icon = ROOT / "icon.png"
    if icon.exists():
        shutil.copy2(icon, BUILD / "icon.png")

    # server bootstrap + package
    shutil.copy2(ROOT / "server" / "main.py", BUILD / "server" / "main.py")
    shutil.copytree(
        ROOT / "src" / "hpe_mist_mcp",
        BUILD / "server" / "hpe_mist_mcp",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )

    # vendored dependencies (only if requirements.txt lists any)
    reqs = runtime_requirements()
    if install_deps and reqs:
        lib = BUILD / "server" / "lib"
        lib.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                sys.executable, "-m", "pip", "install",
                "-r", str(ROOT / "requirements.txt"),
                "--target", str(lib),
                "--upgrade",
            ],
            check=True,
        )
    else:
        print("No runtime dependencies to vendor (standard-library only).")


def package(version: str) -> Path:
    DIST.mkdir(parents=True, exist_ok=True)
    out = DIST / f"hpe-networking-assistant-{version}.dxt"
    if out.exists():
        out.unlink()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(BUILD.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(BUILD))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the .dxt extension package.")
    parser.add_argument("--no-deps", action="store_true", help="Skip vendoring dependencies.")
    args = parser.parse_args()

    version = load_version()
    print(f"Building HPE Networking Assistant v{version}…")
    stage(install_deps=not args.no_deps)
    out = package(version)
    size_kb = out.stat().st_size / 1024
    print(f"Created {out}  ({size_kb:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
