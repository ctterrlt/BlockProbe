<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<!-- Purpose: Project overview, requirements, installation and architecture. -->

# BlockProbe

[![License: AGPL-3.0-or-later](https://img.shields.io/badge/license-AGPL--3.0--or--later-blue?style=flat-square)](LICENSE)
[![Python 3.14+](https://img.shields.io/badge/python-3.14%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![Version 1.0.0 source](https://img.shields.io/badge/version-1.0.0%20source-blue?style=flat-square)](#status)
[![Platforms](https://img.shields.io/badge/platforms-Windows%20%7C%20macOS%20%7C%20Linux%20%7C%20Android-informational?style=flat-square)](#requirements)
[![Status](https://img.shields.io/badge/status-1.0.0%20source-orange?style=flat-square)](#status)
[![Telemetry](https://img.shields.io/badge/telemetry-none-brightgreen?style=flat-square)](#privacy)

BlockProbe is an open-source, local-first tool that verifies whether gambling websites remain reachable from a device after a blocker such as BetBlocker, Gamban, or a DNS filter has been installed. It collects domains from public regulator portals, probes each domain through the device's own network path, and classifies the outcome as **blocked**, **unreachable**, or **open**.

The project is intended for people who need evidence about whether a blocker is working as expected, and for blocker maintainers who need reproducible reports about leaks.

## Status

BlockProbe is usable **from source**: the ADM portal refresh, live
probing, SQLite storage, reports, diagnostics, consent flow, Flet UI, CLI and
local web UI all work, and the test suite covers the portal, classifier,
storage, cache, UI and CLI paths. No signed desktop or mobile release has been
produced yet (milestone M6 is in progress). The Python wheel and source archive
build successfully. A universal Android APK and Linux portable tarball now
build locally; the Linux per-user installer and a headless Linux app launch were
smoke-checked. The APK has an Android debug certificate and has not been
installed on a physical device. Other native targets still need CI/toolchain
verification before they can be treated as releases.

The classifier ships two verified BetBlocker rules and refreshes
`https://betblocker.org/blocked/` on every run. New visible block-page sentences
are appended to the local SQLite database as escaped literals and marked for
review; a normal run never deletes an older pattern. Unimplemented features
report that clearly instead of pretending to work. Removed options (MongoDB,
Redis, telemetry switches) fail at startup with a clear error instead of
silently doing nothing.

## Capabilities

- **Portal collection.** Downloads regulator-published domain lists, starting with Italy's ADM Concessionari, with robots.txt compliance, rate limiting, pagination, and append-only updates at most once every 24 hours.
- **Local probing.** Resolves each domain through the system resolver, checks TCP/TLS connectivity, and fetches a bounded HTTP response. Tests the host as listed plus its apex and `www` variants.
- **Redirect and new-window evidence.** Records 302/307/308 chains, static `<meta refresh>`, `window.open`, `location` targets and reopen-on-return hints without executing JavaScript or downloading assets. A target on a known blocker landing page is blocked from the URL alone.
- **Evidence-based classification.** Applies ordered signature rules to page content, headers, and redirect chains. Verdicts carry a reason code, confidence, and evidence; no verdict is inferred from an HTTP status code alone.
- **Neutral controls and interference risk.** Requests Google, Wikipedia, Cloudflare and `example.com` before and between probes, reports the first control failure and the domains to recheck, and raises a transparent VPN/DNS interference percentage. A locally detected content-filtering resolver raises the risk substantially.
- **Privacy and legal scope.** Every report states that BlockProbe asks for no age verification, sets or stores no cookies, uploads nothing, accepts no website terms, downloads no images or files, and claims no GDPR/US privacy-law compliance.
- **Local storage.** SQLite only, in one local database file. Probe verdicts are never cached; cached portal lists and views are invalidated through a database generation counter. Dynamic BetBlocker patterns live in an append-only table.
- **Diagnostics.** Reports system DNS configuration, resolver and VPN hints, VPN/proxy usage, hosts-file sinkholes, Private DNS and IPv6 availability, and optionally compares results with a user-configured control resolver.
- **Cross-platform interfaces.** A Flet desktop and mobile UI, a Typer-based CLI, and a local web UI bound to `127.0.0.1`.
- **Five languages, dark theme.** The interface ships in Italian (default), English, French, Spanish and Portuguese, switchable in Settings, with a consistent dark theme and layouts that adapt to narrow phone screens. Translated consent wording is a draft for human legal review; the English text prevails and is not legal advice.
- **Scheduled scans.** Off by default, user-level only (systemd user timer, launchd agent, Task Scheduler XML), with leak-only notifications when a notifier is available.
- **Privacy by design.** No accounts, telemetry, analytics, or crash reporting. Network traffic is limited to selected portals, probe targets, neutral controls, the BetBlocker block page and opt-in checks.

## Requirements

- Python 3.14 or newer for source installs (CLI, library, and local web UI).
- Windows 10 or newer, macOS, Linux (x86_64/arm64), or FreeBSD through the CLI and local web UI. The locally built Linux desktop bundle targets x86_64; build on arm64 for a native arm64 bundle.
- Android APK/AAB builds are configured through Flet; iOS is limited to simulator builds until signing and device packaging are completed.
- Android supports manual scans; background scheduled scans are not implemented on Android yet.
- Outbound network access. No root or administrator privileges are required.
- No external services: SQLite and the embedded TTL cache run in-process; there is nothing to install or configure.

### Compatibility notes

| Target | Runtime |
| --- | --- |
| Source installs | Python 3.14 or newer |
| Packaged runtime | Flet bundles Python 3.14 and project dependencies; local Linux x86_64 and Android debug APK artifacts are smoke-checked, other native outputs need verification |

Source installs require Python 3.14 or newer and pip installs all runtime
dependencies declared in `pyproject.toml`. Flet application builds bundle those
declared dependencies and Python 3.14 into the app runtime. The CLI declares
selected large modules for lazy import on Python 3.15+; Python 3.14 imports
them eagerly for compatibility. `_include_allowed` is a separate portal host
allowlist filter.

## Installation

The Python wheel and source archive build successfully. A Linux x86_64 bundle
and Android APK were also built locally; the APK uses a debug certificate.
Other native targets await CI/toolchain verification:

- **From this repository (supported).** A Python 3.14+ environment with the `desktop` extra installs the declared dependencies and runs the full app and CLI.
- **Python package.** A locally built wheel or sdist provide the CLI, library, local web UI and FreeBSD support, with optional extras (`desktop`, `dev`).
- **Packaged applications (M6 in progress).** The Linux x86_64 portable tarball and universal Android APK were built locally; the Linux launch and Android debug signature were smoke-checked. Windows/macOS builds and CI release artifacts remain unverified. Build targets also exist for those platforms, web and the iOS simulator. AppImage/Flatpak, Linux arm64, physical-device installation checks, production signing and store submission remain open. Android scans are user-started; background scheduling is not implemented. See [docs/PACKAGING.md](docs/PACKAGING.md).
- **Container image (optional).** `packaging/Dockerfile` builds a headless web UI image. Docker is not a prerequisite for normal installation; the container's network route may differ from the device's VPN/DNS route.

The application identity is `BlockProbe`, with the package and CLI name `blockprobe` and the bundle ID `io.github.ctterrlt.blockprobe`. Icon and splash requirements are documented in [docs/BRANDING.md](docs/BRANDING.md).

The Linux download is a distribution-independent `tar.gz`, not a package for
APT, DNF or Pacman. Extract it and run `./install.sh`; the helper installs the
app and AGPL license in the current user's XDG directories and adds a desktop
launcher without asking for administrator access. On Arch Linux, the build
command can also create a `.pkg.tar.zst` package for `pacman -U`, `yay -U` or a
graphical package installer. Debian `.deb` and Fedora `.rpm` packages are not
currently produced. The portable app still depends on the host's GTK and
related system libraries.

### Run from source

The app runs from source with Python 3.14 or newer and a desktop environment:

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

The native Flet app is the primary interface. The first launch shows the
consent notice; nothing touches the network until it is accepted. The main
window then offers six screens: **Run** (source selection, preview, start,
pause, resume, cancel), **Sources** (portal lists and custom websites),
**Results** (filters, comparison, exports), **Logs**, **Settings** and **Help**.

The CLI provides the same use cases. A typical session:

```console
blockprobe consent accept          # once per consent version
blockprobe refresh                 # update the ADM list (append-only)
blockprobe scan --wait             # probe everything, print each verdict
blockprobe report --format markdown
blockprobe diagnose
blockprobe scope                   # privacy, age, cookie and legal scope
blockprobe signature refresh       # fetch the live BetBlocker page
blockprobe serve --headless        # web UI on 127.0.0.1:8550
```

Useful variations:

```console
blockprobe scan --portal "ADM" --no-refresh
blockprobe custom add example.it   # your own websites (never fetched by an update)
blockprobe scan --custom-only --wait
blockprobe scan --notify           # desktop notification about new leaks
blockprobe report -f json --leaks-only
blockprobe pause | resume | cancel
blockprobe signature list          # review stored dynamic patterns
blockprobe signature capture example.com
blockprobe schedule install --frequency weekly --time 20:00
blockprobe schedule status
```

`blockprobe refresh` contacts the regulator pages; `scan` contacts the
websites being checked, the neutral control sites and the live BetBlocker block
page. All of them require consent. Results are stored locally and later reports
are generated from storage without re-probing.

## Architecture

The codebase is organised in three layers:

1. `blockprobe.core` contains domain models, configuration, errors and enums. It has no UI or network dependencies.
2. Services cover portal extraction, probing, classification, storage, caching, and diagnostics.
3. Interfaces include the Flet UI, the CLI, and a loopback-only web UI. Button handlers in `ui/actions.py` call the shared service layer; neither the UI nor the CLI contains business logic.

SQLite is the only storage backend and the embedded TTL cache requires no external service: there is no server to run and nothing to configure.

```text
src/
├── main.py                 # Flet entry point: ft.run(main)
├── assets/                 # icon.png, icon_windows.ico, splash.png and overrides
└── blockprobe/
    ├── core/               # models, enums, errors, config
    ├── i18n.py             # translations: Italian (default), English, French, Spanish, Portuguese
    ├── portals/            # portal loading, extraction, pagination, normalisation
    ├── probe/              # DNS, TCP/TLS, HTTP, classification, variants
    ├── storage/            # backend interface, SQLite, migrations
    ├── cache/              # embedded TTL cache (in-process, no server)
    ├── diagnostics/        # DNS, resolver comparison, VPN/proxy detection
    ├── services/           # scan, refresh, report and signature use cases
    ├── scheduler/          # user-level scheduled scans and notifications
    ├── cli.py              # command-line interface
    └── ui/                 # Flet app shell, screens and shared actions
```

Further reading: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md),
[docs/PORTALS.md](docs/PORTALS.md), [docs/SIGNATURES.md](docs/SIGNATURES.md),
[docs/PRIVACY.md](docs/PRIVACY.md) and [docs/PACKAGING.md](docs/PACKAGING.md).

## Development

```console
python -m pip install -e ".[dev]"
python -m pytest          # 186 passing, 2 live-capture tests skipped by default
python -m ruff check src tests scripts
python -m mypy            # strict on blockprobe.core
```

## Privacy

BlockProbe runs entirely on the local device. It does not create accounts, collect telemetry, or send results to a server. The only outbound requests are to the regulator portals selected by the user, the domains being probed, the neutral control sites, the live BetBlocker block page, and optional user-enabled checks such as a control DNS resolver. See [docs/PRIVACY.md](docs/PRIVACY.md) for the full data inventory and erasure paths.

Fetched pages are treated as hostile input: scripts are never executed, files are never downloaded, cookies are never stored or replayed, and response bodies are size-capped and discarded.

## Responsible use

Use BlockProbe only on devices you own or have explicit permission to test. Gambling domains are displayed as plain text, never as clickable links, and the tool does not provide access to gambling services.

Results are indicative. They reflect the network path and resolver of the device at the time of the scan and should not be treated as a guarantee that a blocker is effective in every browser or configuration. BlockProbe is not a medical or legal service and does not provide access to gambling services. If gambling is affecting you or someone close to you, contact the support service for your country.

## Roadmap

- **M0** — runnable skeleton, Flet shell, CLI entry points, linting, CI. **Done.**
- **M1** — portal engine; ADM fixture and pagination. **Done.**
- **M2** — probe engine, classifier, SQLite storage. **Done.**
- **M3** — cache, 24-hour list refresh, append-only lists, guarded reset. **Done.**
- **M4** — Flet UI: consent, source selection, custom websites, run, results, logs. **Done.**
- **M5** — diagnostics and scheduling. **Done** (user-level jobs; no elevation).
- **M6** — packaging for desktop, mobile, and containers. **In progress**: Python packages, Linux x86_64 bundle and Android debug APK have been built locally. Windows/macOS and CI artifacts remain unverified; AppImage/Flatpak, Linux arm64, physical-device checks, production signing and store publication remain.
- **M7** — documentation and the first usable release. **Docs updated**; the release waits for M6 and native artifact review.

## Contributing

Contribution guidelines are in [CONTRIBUTING.md](CONTRIBUTING.md). The project uses Conventional Commits and a Developer Certificate of Origin. Please discuss significant changes in an issue before opening a pull request.

## Security

For vulnerability reports, follow [SECURITY.md](SECURITY.md). Please do not open a public issue for undisclosed security problems.

## License

BlockProbe is free software licensed under the GNU Affero General Public License, version 3 or (at your option) any later version (`AGPL-3.0-or-later`). See [LICENSE](LICENSE) for the full text.
