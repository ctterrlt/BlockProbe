<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<!-- Purpose: Contribution workflow, commit conventions and DCO. -->

# Contributing

Thanks for taking the time to improve BlockProbe. The project checks selected
websites to help people assess blockers and filters. It began with gambling-site
blockers, while custom scans also support use cases such as adult-content
filtering and parental controls. Accuracy and clear limits matter: a wrong
verdict is worse than a "needs review".

## Before you start

- Read `Agents.md` (the project blueprint) and `docs/ARCHITECTURE.md`.
- Search existing issues; for significant changes, open an issue first and
  describe the plan.
- Never add invented block-page signatures. Rules follow the capture and review
  process in `docs/SIGNATURES.md`; unverified data is not merged.
- Security problems go to the private channel in `SECURITY.md`, not to a public
  issue.
- `src/blockprobe/VERSION` is the only application-version source. Do not copy
  its value into code, package metadata, installer templates or documentation.
- Keep every visible interface string in `blockprobe.i18n`; add and verify it
  in all supported language catalogs when changing a screen.

## Development setup

```console
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
python -m pip install -e ".[dev]"
python src/main.py               # desktop window
```

The same environment exposes the CLI (`blockprobe --help`) and the local web UI
(`blockprobe serve --headless`, bound to `127.0.0.1:8550`).

## Checks

All of these must pass before a pull request is reviewed:

```console
python -m ruff check src tests scripts
python -m mypy                    # strict on blockprobe.core
python -m pytest                  # includes hypothesis and respx tests
```

- Tests live in `tests/unit`, `tests/integration` and `tests/regression`.
  Fixtures under `tests/fixtures/` follow the rule that the expected result is
  derived from the fixture, never from an invented verdict.
- New behaviour needs a test; bug fixes need a regression test.
- `core` stays free of network, UI and storage imports, and stays strictly
  typed.

## Style

- Formatting and imports follow `ruff` with the configuration in
  `pyproject.toml` (110 columns, `py314` target; Python 3.14+ minimum).
- Use type hints everywhere; public functions have docstrings.
- Keep interfaces thin: UI and CLI code goes through `blockprobe.ui.actions`
  and contains no business logic.
- Comments explain why, not what. English only.

## Commits and sign-off

- The project uses [Conventional Commits](https://www.conventionalcommits.org/)
  (`feat:`, `fix:`, `docs:`, `test:`, `chore:` …).
- Every commit must carry a Developer Certificate of Origin sign-off:

  ```console
  git commit -s -m "fix(classifier): ..."
  ```

  The sign-off is a statement that you wrote the change or have the right to
  submit it under AGPL-3.0-or-later.
- Keep pull requests focused; describe what changed, how it was verified and on
  which platforms.

## Reporting results

When your change affects classification or probing, include in the pull request
the command you ran, the observed verdicts and the sanitized evidence
(status, headers, hashes, not full bodies). Domain names of third parties are
fine; do not attach personal data or logs that contain someone else's traffic.

## License

Contributions are accepted under AGPL-3.0-or-later. See `LICENSE`.
