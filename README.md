<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<!-- Purpose: Project overview, requirements, installation and architecture. -->

# BlockProbe

[![License: AGPL-3.0-or-later](https://img.shields.io/badge/license-AGPL--3.0--or--later-blue?style=flat-square)](LICENSE)
[![Python 3.14+](https://img.shields.io/badge/python-3.14%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![Version](https://img.shields.io/badge/version-1.2.3--gamma-blue?style=flat-square)](#status)
[![Platforms](https://img.shields.io/badge/platforms-Windows%20%7C%20macOS%20%7C%20Linux%20%7C%20Android-informational?style=flat-square)](#requirements)
[![Telemetry](https://img.shields.io/badge/telemetry-none-brightgreen?style=flat-square)](#privacy)

BlockProbe is an open-source, local-first tool for checking whether website filters and blockers behave as expected on a device. It began as a way to check gambling-site blockers such as BetBlocker and Gamban, and its built-in regulator feeds still focus on gambling websites. The same probe engine can check sites chosen by the user to assess other filtering use cases, including adult-content filters and parental controls. It classifies each selected domain as **blocked**, **unreachable**, **open**, or **could not verify**, with evidence and confidence.

The project is for people and families who want local evidence about website filtering, and for filter maintainers who need reproducible reports. It checks only the domains selected for a scan; it does not browse pages, inspect their full content, or certify a filter across every app, browser, profile, or network.

## Status

The current development build is **1.2.3-gamma**. The earlier 1.2.0 and 1.1.1
GitHub release downloads were withdrawn. This gamma build includes the current
regulator sources.

The app version is maintained in [`src/blockprobe/VERSION`](src/blockprobe/VERSION) and is used
by the runtime display and native installer builds. The Python package metadata
uses its PEP 440 equivalent (`1.2.3rc0` for this gamma build). Change the
version file to update the version throughout the project. BlockProbe
is usable **from source**: the ADM, DGOJ, SRIJ, ANJ, GGL and MGA portal refreshes,
live probing, SQLite storage, reports, diagnostics, consent flow, Flet UI, CLI and
local web UI all work, and the test suite covers the portal, classifier,
storage, cache, UI and CLI paths. No signed desktop or mobile release has been
produced yet (milestone M6 is in progress). The release workflow builds Linux,
Android APK/AAB and web artifacts for tagged versions. A locally built APK uses
the Android debug signature; tagged Android releases use the configured release
key. The shared Flet UI keeps scan actions visible while source choices scroll,
wraps long source labels, applies
first-run language changes as soon as they are selected, and uses short success
messages. The loading screen follows the system appearance, and generated
results and portal cards retain the selected theme while a scan runs. Scan
progress polling skips unchanged UI state to reduce phone rendering work. The
Settings layout uses compact phone spacing. Starting a scan immediately shows
a preparation message, then keeps
the result or reason it could not start visible on compact screens. First-run
language changes use the Flet Dropdown selection event and apply before the
consent decision across desktop, mobile and web clients. Linux and
Android debug builds were launched locally; the final Android APK was tapped
in an Android 35 emulator and visibly reported that the scan started, then
completed. The Linux desktop bundle stayed open through a 15-second Xvfb
launch. The web bundle builds and
loads at narrow and wide browser sizes, but headless Chromium stayed on its
startup screen, so web interaction still needs verification. The local APK is
debug-signed; tagged Android release APK/AAB files use the release key
configured in GitHub Actions. Windows and macOS native bundles must be built on
their respective hosts; the release workflow covers those targets. See
[docs/PACKAGING.md](docs/PACKAGING.md) for current build coverage.

Official F-Droid metadata is kept in
[`fdroid/io.github.ctterrlt.blockprobe.yaml`](fdroid/io.github.ctterrlt.blockprobe.yaml).
After the GitHub `Release` workflow succeeds for a tag, the `F-Droid` workflow
renders and submits the metadata to `fdroiddata` as a merge request. The
required `.yml` file exists only in that merge request. F-Droid maintainers
still review, build, sign and publish the app; the workflow cannot upload or
delete an F-Droid release. See [docs/FDROID.md](docs/FDROID.md) for setup,
release steps and limitations.

The classifier ships two verified BetBlocker rules and refreshes
`https://betblocker.org/blocked/` on every run. New visible block-page sentences
are appended to the local SQLite database as escaped literals and marked for
review; a normal run never deletes an older pattern. Unimplemented features
report that clearly instead of pretending to work. Removed options (MongoDB,
Redis, telemetry switches) fail at startup with a clear error instead of
silently doing nothing.

## Capabilities

- **Built-in gambling sources.** Downloads regulator-published gambling-domain lists from Italy (ADM), Spain (DGOJ), Portugal (SRIJ), France (ANJ), Germany (GGL), and the Malta Gaming Authority (MGA). The MGA source is its international list of URLs falsely claiming MGA affiliation, not a worldwide licence list. Updates respect robots.txt, use rate limits and pagination, and append new domains without deleting existing ones.
- **Personalized scans.** Add domains that matter to your own filtering setup, then select only those custom sites or combine them with built-in sources. This supports checks of adult-content filtering, parental controls, and other user-selected website categories; custom domains are never fetched as a feed. A domain-level probe can show whether a site responds on this device's network path, but cannot confirm that all pages or content within that site are filtered.
- **Local probing.** Resolves each domain through the system resolver, checks TCP/TLS connectivity, and fetches a bounded HTTP response. Tests the host as listed plus its apex and `www` variants.
- **Redirect and new-window evidence.** Records 302/307/308 chains, static `<meta refresh>`, `window.open`, `location` targets and reopen-on-return hints without executing JavaScript or downloading assets. A target on a known blocker landing page is blocked from the URL alone.
- **Evidence-based classification.** Applies ordered signature rules to page content, headers, and redirect chains. Verdicts carry a reason code, confidence, and evidence; no verdict is inferred from an HTTP status code alone.
- **Neutral controls and interference signal.** Requests Google, Wikipedia and Cloudflare before and between probes, reports the first control failure and the domains to recheck, and reports a transparent heuristic VPN/DNS interference signal. A locally detected content-filtering resolver is shown as context, but it does not by itself raise that signal.
- **Privacy and legal scope.** Every report states that BlockProbe asks for no age verification, sets or stores no cookies, uploads nothing, accepts no website terms, downloads no images or files, and claims no GDPR/US privacy-law compliance.
- **Local storage.** SQLite only, in one local database file. Probe verdicts are never cached; cached portal lists and views are invalidated through a database generation counter. Dynamic BetBlocker patterns live in an append-only table.
- **Diagnostics.** Reports system DNS configuration, resolver and VPN hints, VPN/proxy usage, hosts-file sinkholes, Private DNS and IPv6 availability, and optionally compares results with a user-configured control resolver.
- **Cross-platform interfaces.** A Flet desktop and mobile UI, a Typer-based CLI, and a local web UI bound to `127.0.0.1`.
- **Five languages, adaptive appearance.** The interface ships in English (default), Italian, French, Spanish and Portuguese. Choose a language on the first-run consent screen or later in Settings. Appearance follows the operating system by default, with saved Light and Dark overrides. Layouts adapt to narrow windows and phone widths, with scrolling content and compact navigation labels. Translated consent wording is a draft for human legal review; the English text prevails and is not legal advice.
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
| Packaged runtime | Flet bundles Python 3.14 and project dependencies; Linux x86_64 and Android 35 emulator builds were launched; Windows/macOS must be built on their native hosts |

Source installs require Python 3.14 or newer and pip installs all runtime
dependencies declared in `pyproject.toml`. Flet application builds bundle those
declared dependencies and Python 3.14 into the app runtime. The CLI declares
selected large modules for lazy import on Python 3.15+; Python 3.14 imports
them eagerly for compatibility. `_include_allowed` is a separate portal host
allowlist filter.

## Installation

Run `python scripts/build.py all` to rebuild every target available on the
current host, including Python, that host's desktop app, Android APK/AAB and
web. Windows, macOS and iOS simulator artifacts require their native build
hosts. The Android APK uses a debug certificate. See the build matrix and
limitations in [docs/PACKAGING.md](docs/PACKAGING.md).

- **From this repository (supported).** A Python 3.14+ environment with the `desktop` extra installs the declared dependencies and runs the full app and CLI.
- **Python package.** A locally built wheel or sdist provide the CLI, library, local web UI and FreeBSD support, with optional extras (`desktop`, `dev`).
- **Packaged applications (M6 in progress).** Linux, Windows, macOS, Android, web and iOS simulator build paths are documented. Android UI was checked in an emulator at two responsive sizes; the project owner has also exercised the app on Android. Production signing, Linux arm64, AppImage/Flatpak, iOS device packaging and store submission remain open. Android scans are user-started; background scheduling is not implemented. See [docs/PACKAGING.md](docs/PACKAGING.md).
- **Container image (optional).** `packaging/Dockerfile` builds a headless web UI image. Docker is not a prerequisite for normal installation; the container's network route may differ from the device's VPN/DNS route.

The application identity is `BlockProbe`, with the package and CLI name
`blockprobe` and bundle ID `io.github.ctterrlt.blockprobe`. Icon and splash
requirements are documented in [docs/BRANDING.md](docs/BRANDING.md).

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

Dependencies are declared in `pyproject.toml`. FastAPI is constrained to a release line compatible with Starlette 1.x; the resolver selects current compatible security fixes.

## Usage

The native Flet app is the primary interface. The first launch shows the
consent notice; nothing touches the network until it is accepted. The main
window then offers six screens: **Run** (choose a source or personalized set,
preview, start, pause, resume, cancel), **Sources** (add a regulator feed or
individual custom websites in separate forms; scan selections keep them
separate),
**Results** (filters, comparison, exports), **Logs**, **Settings** and **Help**.

The CLI provides the same use cases. A typical session:

```console
blockprobe consent accept          # once per consent version
blockprobe refresh                 # update stale portal lists (append-only)
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
blockprobe custom add example.it   # a personally selected site; never fetched as a feed
blockprobe scan --custom-only --wait
blockprobe scan --notify           # desktop notification about new leaks
blockprobe report -f json --leaks-only
blockprobe pause | resume | cancel
blockprobe signature list          # review stored dynamic patterns
blockprobe signature capture casino.example
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
    ├── i18n.py             # translations: English (default), Italian, French, Spanish, Portuguese
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
python -m pytest          # 214 passing, 2 live-capture tests skipped by default
python -m ruff check src tests scripts
python -m mypy            # strict on blockprobe.core
```

## Privacy

BlockProbe runs entirely on the local device. It does not create accounts, collect telemetry, or send results to a server. The only outbound requests are to the regulator portals selected by the user, the domains being probed, the neutral control sites, the live BetBlocker block page, and optional user-enabled checks such as a control DNS resolver. See [docs/PRIVACY.md](docs/PRIVACY.md) for the full data inventory and erasure paths.

Fetched pages are treated as hostile input: scripts are never executed, files are never downloaded, cookies are never stored or replayed, and response bodies are size-capped and discarded.

## Responsible use

Use BlockProbe only on devices you own or have explicit permission to test. Scanned domains are displayed as plain text, never as clickable links. The app does not provide access to gambling or adult-content services.

Results are indicative. They reflect the network path and resolver of the device at scan time and are not a guarantee that a filter works in every browser, app, profile, or configuration. A domain-level response cannot establish whether all content on a site is blocked. BlockProbe is not a medical, legal, or parental-control service and does not change device restrictions. For gambling-related support, contact a service in your country.

## Roadmap

- **M0** — runnable skeleton, Flet shell, CLI entry points, linting, CI. **Done.**
- **M1** — portal engine; ADM fixture and pagination. **Done.**
- **M2** — probe engine, classifier, SQLite storage. **Done.**
- **M3** — cache, 24-hour list refresh, append-only lists, guarded reset. **Done.**
- **M4** — Flet UI: consent, source selection, custom websites, run, results, logs. **Done.**
- **M5** — diagnostics and scheduling. **Done** (user-level jobs; no elevation).
- **M6** — packaging for desktop, mobile, and containers. **In progress**: Python packages, Linux x86_64 bundle and Android debug APK/AAB builds are supported; local Android UI checks use an emulator. Windows/macOS builds require native CI hosts. AppImage/Flatpak, Linux arm64, production signing and store publication remain.
- **M7** — documentation and the first usable release. **Docs updated**; the release waits for M6 and native artifact review.

## Contributing

Contribution guidelines are in [CONTRIBUTING.md](CONTRIBUTING.md). The project uses Conventional Commits and a Developer Certificate of Origin. Please discuss significant changes in an issue before opening a pull request.

## Security

For vulnerability reports, follow [SECURITY.md](SECURITY.md). Please do not open a public issue for undisclosed security problems.

## License

BlockProbe is free software licensed under the GNU Affero General Public License, version 3 or (at your option) any later version (`AGPL-3.0-or-later`). See [LICENSE](LICENSE) for the full text.
