#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Print the maintainer's manual release checklist without changing files.

Versions are edited only in ``src/blockprobe/VERSION``. Tags and publishing
remain maintainer actions, so this script never commits, tags or pushes. The
GitHub Actions release workflow builds and publishes after a version tag push.
"""

from __future__ import annotations

import sys

MESSAGE = """\
BlockProbe never commits, tags or pushes on its own; the maintainer does that
manually after reviewing the changes.

Manual checklist:
  1. Update src/blockprobe/VERSION and CHANGELOG.md.
  2. Run: python -m ruff check src tests && python -m mypy && python -m pytest
  3. Run: python scripts/build.py all (on macOS for the iOS simulator bundle).
  4. Smoke-test each available artifact and inspect the all-platform release CI.
  5. Review the changes, then commit and push a version tag yourself.

"""


def main() -> int:
    print(MESSAGE, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
