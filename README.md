<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<!-- Purpose: Project overview, requirements, installation and architecture. -->

# BlockProbe

[![License: AGPL-3.0-or-later](https://img.shields.io/badge/license-AGPL--3.0--or--later-blue?style=flat-square)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![Version 1.0.0](https://img.shields.io/badge/version-1.0.0-blue?style=flat-square)](https://github.com/ctterrlt/BlockProbe/releases/tag/v1.0.0)
[![Platforms](https://img.shields.io/badge/platforms-Windows%20%7C%20macOS%20%7C%20Linux%20%7C%20Android-informational?style=flat-square)](#requirements)
[![Status](https://img.shields.io/badge/status-early%20development-orange?style=flat-square)](#status)
[![Telemetry](https://img.shields.io/badge/telemetry-none-brightgreen?style=flat-square)](#privacy)

BlockProbe is an open-source, local-first tool that verifies whether gambling websites remain reachable from a device after a blocker such as BetBlocker, Gamban, or a DNS filter has been installed. It collects domains from public regulator portals, probes each domain through the device's own network path, and classifies the outcome as **blocked**, **unreachable**, or **open**.

The project is intended for people who need evidence about whether a blocker is working as expected, and for blocker maintainers who need reproducible reports about leaks.

## Status

BlockProbe is in early development. The current scaffold is **runnable from source**: it opens a Flet window, performs a local self-check, and clearly reports which actions are not implemented yet. There is no packaged release yet.

The initial milestone (M0: runnable shell, linting, and CI) is in progress. Portal collection, probing, storage, and reports are not implemented; the UI and CLI never pretend otherwise.

## Planned capabilities

- **Portal collection.** Downloads regulator-published domain lists, starting with Italy's ADM Concessionari, with robots.txt compliance, rate limiting, pagination, and append-only updates at most once every 24 hours.
- **Local probing.** Resolves each domain through the system resolver, checks TCP/TLS connectivity, and fetches a bounded HTTP response. Tests the host as listed plus its apex and `www` variants.
- **Evidence-based classification.** Applies ordered signature rules to page content, headers, and redirect chains. Verdicts carry a reason code, confidence, and evidence; no verdict is inferred from an HTTP status code alone.
- **Local storage.** SQLite by default, with an optional MongoDB backend. Probe verdicts are never cached; cached portal lists and views are invalidated through a database generation counter.
- **Diagnostics.** Reports system DNS configuration, VPN/proxy usage, Private DNS and IPv6 availability, and optionally compares results with a user-configured control resolver.
- **Cross-platform interfaces.** A Flet desktop and mobile UI, a Typer-based CLI, and a local web UI bound to `127.0.0.1`.
- **Privacy by design.** No accounts, telemetry, analytics, or crash reporting. Network traffic is limited to selected portals, probe targets, and opt-in checks.

## Requirements

- Python 3.11 or newer for source installs (CLI, library, and local web UI).
- Windows 10 or newer, macOS, Linux (x86_64/arm64), or FreeBSD through the CLI and local web UI.
- Android 8 or newer is planned through Flet; iOS 15 or newer is best-effort.
- Outbound network access. No root or administrator privileges are required.
- No external services by default: SQLite and the embedded TTL cache run in-process.
- Redis or MongoDB are optional and only needed when selected as storage or cache backends.

### Compatibility notes

| Target | Runtime |
| --- | --- |
| Source installs | Python 3.11 or newer |
| Packaged desktop builds | Python bundled by Flet |
| Packaged Android/iOS builds | Python 3.12–3.14, matching the versions bundled by Flet |
| Python 3.15 | Not a default Flet target; only with an explicit toolchain override |

The project declares a minimum Python version rather than targeting the newest release. Packaged builds use one of the Python versions bundled by Flet (3.12, 3.13, or 3.14). Release builds pin dependencies with hashes for reproducible artifacts rather than resolving the latest versions at install time.

## Installation

There is no packaged release yet. The planned distribution paths are:

- **Packaged applications** for Windows (EXE or MSI), macOS (signed and notarized DMG), Linux (AppImage and Flatpak), and Android (APK or AAB), with iOS as best-effort. These bundle Python and all dependencies; Docker is not required and no administrator privileges are needed.
- **Python package** for the CLI, library, local web UI, and FreeBSD, with optional extras for additional backends (`redis`, `mongo`, `desktop`, `dev`).
- **Container image** as an optional artifact for headless deployments and CI. Docker is a packaging target, not a prerequisite for normal installation.

The application identity is `BlockProbe`, with the package and CLI name `blockprobe` and the bundle ID `io.github.ctterrlt.blockprobe`. Icon and splash requirements are documented in [docs/BRANDING.md](docs/BRANDING.md).

### Run from source

The scaffold runs today with Python 3.11 or newer and a desktop environment:

```console
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[desktop]"
python src/main.py
```

On Windows, activate the environment with `.venv\Scripts\activate`. The `desktop` extra installs the Flet desktop runtime; no Docker or external service is required.

The same installation provides the CLI and the local web UI:

```console
blockprobe --help
blockprobe version
blockprobe self-check
blockprobe serve --headless
```

`blockprobe serve --headless` binds the web UI to `127.0.0.1:8550` without opening a browser. On a headless Linux host, the native window can be started with `xvfb-run -a python src/main.py`.

Dependencies are declared in `pyproject.toml`. The Flet web runtime currently requires FastAPI below 0.116; the pin is documented in the project notes and must not be removed until Flet fixes its web server.

## Usage

The native Flet app is the primary interface. It starts with a working self-check and data-directory action; the remaining buttons show an explicit "not implemented" message until their milestone lands.

The CLI currently provides the same working checks and typed stubs for the planned scan workflow:

```console
blockprobe --help
blockprobe version
blockprobe self-check
blockprobe serve --headless

# Wired but not implemented yet; each exits with a clear message:
blockprobe refresh --portal adm_concessionari
blockprobe scan
blockprobe report --format markdown
blockprobe signature capture example.com
```

## Architecture

The codebase is organised in three layers:

1. `blockprobe.core` contains domain models, configuration, errors, and events. It has no UI or network dependencies.
2. Services cover portal extraction, probing, classification, storage, caching, and diagnostics.
3. Interfaces include the Flet UI, the CLI, and a loopback-only web UI. Button handlers in `ui/actions.py` call the shared service layer; neither the UI nor the CLI contains business logic.

SQLite is the default storage backend, and the embedded TTL cache requires no external service. Redis and MongoDB are optional backends for desktop and container deployments.

```text
src/
├── main.py                 # Flet entry point: ft.run(main)
├── assets/                 # icon.png, icon_windows.ico, splash.png and overrides
└── blockprobe/
    ├── core/               # models, enums, errors, config, events
    ├── portals/            # portal loading, extraction, pagination, normalisation
    ├── probe/              # DNS, TCP/TLS, HTTP, classification, variants
    ├── storage/            # backend interface, SQLite, MongoDB, migrations
    ├── cache/              # embedded TTL cache, optional Redis backend
    ├── diagnostics/        # DNS, resolver comparison, VPN/proxy detection
    ├── services/           # scan, refresh, report and signature use cases
    ├── scheduler/          # user-level scheduled scans
    ├── cli.py              # command-line interface
    ├── web/                # local web UI fallback
    └── ui/                 # Flet app shell, screens and actions
```

## Privacy

BlockProbe runs entirely on the local device. It does not create accounts, collect telemetry, or send results to a server. The only outbound requests are to the regulator portals selected by the user, the domains being probed, and optional user-enabled checks such as a control DNS resolver.

Fetched pages are treated as hostile input: scripts are never executed, files are never downloaded, and response bodies are size-capped.

## Responsible use

Use BlockProbe only on devices you own or have explicit permission to test. Gambling domains are displayed as plain text, never as clickable links, and the tool does not provide access to gambling services.

Results are indicative. They reflect the network path and resolver of the device at the time of the scan and should not be treated as a guarantee that a blocker is effective in every browser or configuration. The application will include country-specific support resources for people affected by gambling harm.

## Roadmap

- **M0** — runnable project skeleton, Flet shell, CLI entry points, linting, CI.
- **M1** — portal engine; ADM fixture and pagination.
- **M2** — probe engine, classifier, SQLite storage.
- **M3** — cache, 24-hour list refresh, append-only lists, guarded reset.
- **M4** — Flet UI: consent, source selection, custom websites, run, results, logs.
- **M5** — diagnostics and scheduling.
- **M6** — packaging for desktop, mobile, and containers.
- **M7** — documentation and the first usable release.

## Contributing

Contribution guidelines are in [CONTRIBUTING.md](CONTRIBUTING.md). The project uses Conventional Commits and a Developer Certificate of Origin. Please discuss significant changes in an issue before opening a pull request.

## Security

For vulnerability reports, follow [SECURITY.md](SECURITY.md). Please do not open a public issue for undisclosed security problems.

## License

BlockProbe is free software licensed under the GNU Affero General Public License, version 3 or (at your option) any later version (`AGPL-3.0-or-later`). See [LICENSE](LICENSE) for the full text.
