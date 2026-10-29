# SPDX-License-Identifier: AGPL-3.0-or-later
"""BlockProbe core library package."""

from pathlib import Path

_version_file = Path(__file__).with_name("VERSION")
__version__ = _version_file.read_text(encoding="utf-8").strip()
APP_NAME = "BlockProbe"
