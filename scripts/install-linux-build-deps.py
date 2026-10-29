#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Install Flet's current Linux build packages on Debian/Ubuntu."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def main() -> int:
    if sys.platform != "linux":
        raise SystemExit("This helper is for Linux build hosts only.")
    apt_get = shutil.which("apt-get")
    if apt_get is None:
        raise SystemExit(
            "This helper supports Debian/Ubuntu hosts with apt-get. "
            "Install equivalent Flet Linux build dependencies on other distributions."
        )

    flet_cli = Path(sys.executable).with_name("flet")
    if os.name == "nt":
        flet_cli = flet_cli.with_suffix(".exe")
    if not flet_cli.is_file():
        located = shutil.which("flet")
        if located is None:
            raise SystemExit("Flet is missing; install with python -m pip install -e '.[packaging]'.")
        flet_cli = Path(located)

    result = subprocess.run(
        [str(flet_cli), "--version", "--json"],
        check=True,
        capture_output=True,
        text=True,
    )
    packages = json.loads(result.stdout).get("linux_dependencies", [])
    if not packages:
        raise SystemExit("Flet did not report Linux build dependencies.")

    privilege = [] if os.geteuid() == 0 else [shutil.which("sudo") or "sudo"]
    subprocess.run([*privilege, apt_get, "update"], check=True)
    subprocess.run([*privilege, apt_get, "install", "-y", *packages], check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
