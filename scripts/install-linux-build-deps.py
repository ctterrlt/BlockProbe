#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Install or verify Flet's current Linux build packages on Debian/Ubuntu.

The package list is reported by the installed Flet CLI, so it stays correct when
Flet changes its build requirements. CI resolves the list first, restores it
from an APT package cache, and only falls back to a full install when a package
is still missing.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def _flet_cli() -> Path:
    """Return the installed Flet executable, or fail with an actionable error."""
    candidate = Path(sys.executable).with_name("flet")
    if os.name == "nt":
        candidate = candidate.with_suffix(".exe")
    if candidate.is_file():
        return candidate
    located = shutil.which("flet")
    if located is None:
        raise SystemExit(
            "Flet is missing; install with python -m pip install -e '.[packaging]'."
        )
    return Path(located)


def flet_linux_dependencies() -> list[str]:
    """Return the package names Flet reports for Linux builds."""
    result = subprocess.run(
        [str(_flet_cli()), "--version", "--json"],
        check=True,
        capture_output=True,
        text=True,
    )
    packages = json.loads(result.stdout).get("linux_dependencies", [])
    if not packages:
        raise SystemExit("Flet did not report Linux build dependencies.")
    return [str(package) for package in packages]


def missing_packages(packages: list[str]) -> list[str]:
    """Return packages that are not installed according to dpkg."""
    missing: list[str] = []
    for package in packages:
        result = subprocess.run(
            ["dpkg-query", "-W", "-f=${Status}", package],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0 or "install ok installed" not in result.stdout:
            missing.append(package)
    return missing


def install(packages: list[str], *, update: bool = True) -> None:
    """Install packages with apt-get, using sudo when the user is not root."""
    privilege = [] if os.geteuid() == 0 else [shutil.which("sudo") or "sudo"]
    apt_get = shutil.which("apt-get")
    if apt_get is None:
        raise SystemExit(
            "This helper supports Debian/Ubuntu hosts with apt-get. "
            "Install equivalent Flet Linux build dependencies on other distributions."
        )
    if update:
        subprocess.run([*privilege, apt_get, "update"], check=True)
    subprocess.run([*privilege, apt_get, "install", "-y", *packages], check=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--print-packages",
        action="store_true",
        help="Print Flet's Linux package list as one space-separated line and exit.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero if any Flet Linux package is missing; install nothing.",
    )
    parser.add_argument(
        "--no-update",
        action="store_true",
        help="Skip apt-get update when a fallback install is needed.",
    )
    args = parser.parse_args(argv)

    if sys.platform != "linux":
        raise SystemExit("This helper is for Linux build hosts only.")
    if shutil.which("apt-get") is None:
        raise SystemExit(
            "This helper supports Debian/Ubuntu hosts with apt-get. "
            "Install equivalent Flet Linux build dependencies on other distributions."
        )

    packages = flet_linux_dependencies()
    if args.print_packages:
        print(" ".join(packages))
        return 0

    missing = missing_packages(packages)
    if args.check:
        if missing:
            print("Missing Flet Linux packages: " + " ".join(missing), file=sys.stderr)
            return 1
        return 0

    # Keep local reruns cheap: do nothing when every package is already present.
    if missing:
        install(missing, update=not args.no_update)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
