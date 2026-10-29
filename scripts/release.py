#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bump the version, create the tag and assemble release artifacts.

Not implemented. Versions are edited in ``pyproject.toml`` and tags are
created by the maintainer on purpose, so this script refuses to do anything
and prints the manual checklist instead of committing, tagging or pushing.
"""

from __future__ import annotations

import sys

MESSAGE = """\
Release automation is not implemented.

BlockProbe never commits, tags or pushes on its own; the maintainer does that
manually after reviewing the changes.

Manual checklist:
  1. Update the version in pyproject.toml and CHANGELOG.md.
  2. Run: python -m ruff check src tests && python -m mypy && python -m pytest
  3. Build the wheel: python -m build
  4. Smoke-test the wheel (see .github/workflows/release.yaml).
  5. Commit, then create and push the tag yourself.

Nothing was changed.
"""


def main() -> int:
    print(MESSAGE, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
