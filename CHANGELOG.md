<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<!-- Purpose: Release history. -->

# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.3.0-alpha] — 2026-10-31

### Changed

- Clarified that BlockProbe supports personalized website-filter checks,
  including parental controls and adult-content filters, while built-in
  regulator feeds remain focused on gambling domains.
- Made scan concurrency configurable in Settings within platform limits:
  three on Android/iOS and eight elsewhere.
- Added a global System/Light/Dark appearance menu and improved scrolling and
  bottom clearance at narrow window sizes.
- Distinguished consent storage errors from missing or outdated consent, and
  return to consent only after an action explicitly clears the record. Startup,
  scan start, resume and CLI status surface failed consent reads as storage
  errors; resume cannot bypass the consent gate.
- Replaced the prior action banner immediately and showed clear scan-start errors.
- Refreshed the scan preview whenever source selection changes, so portal
  refresh warnings match the current selection.
- Let users choose export paths with the platform save dialog. If it fails, a
  uniquely named copy is kept in the local reports folder and its path is
  shown; cancellation does not create a fallback copy. Preparation and
  readback errors also try to recover generated output before temporary cleanup.
- Fixed custom-site listing so regulator portal domains are not included as
  personal sites.
- Preserved one-command F-Droid build recipes as YAML lists so rendered
  metadata passes the release workflow's validation step.
- Sent probes, portal feed requests, control-resolver requests and optional
  curl checks with the same Chrome-for-Windows User-Agent. This is a request
  header, not Chrome browser or network-fingerprint emulation.
- Stopped requesting or enforcing `robots.txt` during portal refresh. The
  legacy `respect_robots` field is ignored; configured request rate limits and
  retry backoff remain active.
- Added an optional bounded `curl` cross-check after HTTPX reports block
  evidence. Curl checks content type with HEAD before fetching an HTML body;
  when curl opens a site successfully, the result remains marked for review
  and displays “Warning: curl opened the website successfully.” Curl absence
  or errors do not prevent a scan, including on Android where curl is not
  bundled.

## [1.2.4-alpha] — 2026-10-31

### Fixed

- Let HTTPX perform the website request even when the preliminary DNS or TCP
  diagnostic fails. A successful HTTP response now takes precedence over a
  conflicting diagnostic, avoiding false unreachable results for live sites.
- Follow portal pagination through pages that contain no new domains, and warn
  when the configured page limit leaves discovered list pages unvisited.
- Reject F-Droid metadata rendering if the top-level `Builds` list or current
  recipe commands are empty.
- Stretch the appearance and settings panels across the available width after
  theme changes, and give the log list the remaining screen height so it can
  scroll above the navigation on phones and narrow windows.
- Keep the latest action banner visible when actions overlap.
- Build F-Droid metadata against the exact successful Release workflow commit,
  so a later move of its tag does not invalidate that completed run.

## [1.2.3-gamma] — 2026-10-30

### Fixed

- Follow the operating system appearance during startup and use matching light
  and dark launch colors; apply the saved app theme before the first screen is
  painted.
- Improve contrast in consent dialogs, scan progress, portal errors and result
  verdicts in both appearance modes.
- Label probe failures without an HTTP response as needing review, since those
  failures alone cannot prove that a site is unavailable in a browser.
- Give each Android prerelease stage its own increasing version code, so this
  gamma update can replace earlier alpha/beta builds without reinstalling.
- Keep newly generated portal and result content in the selected theme, reduce
  redundant scan-poller updates, and refresh live results as verdict counts
  change.
- Show action feedback with a fresh notification control so a later action can
  replace a visible message reliably.
- Make Settings denser on phones and use a two-column schedule form at compact
  widths.

## [1.2.3-alpha] — 2026-10-30

### Changed

- Improved compact-screen layouts, theme updates and scan controls.
- Paginated mobile scan results to reduce rendering load and improve scrolling.
- Show the saved file path after exporting a report or domain list.

## [1.2.2-alpha] — 2026-10-30

### Added

- Added bundled regulator sources for France (ANJ), Germany (GGL), and Malta
  (MGA). The MGA feed is its international unauthorised-URL warning list; it
  is not a global licence registry. Together with ADM, DGOJ and SRIJ, six
  official sources are available on fresh installs.
- Updated the canonical app version and derived Android build number to
  `1.2.2-alpha` / `1002002`.
- Added canonical F-Droid metadata, a tag-driven workflow that renders the
  `fdroiddata` `.yml` and opens or updates the merge request when GitLab
  credentials are configured, and `docs/FDROID.md`. Official publication still
  requires F-Droid maintainer review, build and signing.

### Changed

- Reframed the network interference percentage as a heuristic signal rather
  than a probability. A configured filtering DNS resolver is now reported as
  context and does not raise the signal unless control checks or results show
  actual interference.

### Build and verification

- The local `all` build completed for Python wheel/sdist, Linux portable bundle,
  Android APK/AAB and web bundle. APK manifest reports `versionName=1.2.2-alpha`
  and `versionCode=1002002`; `apksigner` verified its Android debug signature.
  Windows, macOS and iOS simulator targets require their respective native
  runners. No tests or UI/device checks were run for this source update.

## [1.2.1-alpha] — 2026-10-30

This alpha replaces the withdrawn 1.2.0 and 1.1.1 release downloads. It
focuses on making the shared Flet interface work clearly across compact and
wide screens and on rebuilding the distribution artifacts.

### Cross-platform UI follow-up

- Made the Run screen a single scrolling surface so its final section stays
  reachable above the mobile navigation at short heights and narrow widths.
- Added a persisted System, Light and Dark appearance choice. System is the
  default and follows OS brightness changes; explicit colors in the shared UI
  are recolored to keep both appearances legible.
- Shortened the selected mobile navigation label and only show labels for the
  active destination, preventing “Esecuzione” from wrapping under the tab bar.
- Replaced an open notification before showing a newer result and ignored late
  results from older overlapping actions, so the latest action feedback stays
  visible.
- Return to the consent screen when deleting local data clears consent, and
  report a missing consent record as such instead of saying that no websites
  were selected.

### Fixed

- Saved the language selected during first-run consent and applied settings
  changes immediately. Consent now uses Flet 1.x Dropdown's `on_select` event
  and reads its selected control value; the previous `on_change` callback was
  never invoked on Linux. Success notices use short, human-readable titles.
- Kept scan controls visible while source choices scroll, wrapped long source
  names, and made source labels tappable. Empty selections report why a scan
  cannot start; pause, resume and cancel visibility follows stored run state.
- Made Start show an in-context preparation state immediately and keep the
  success or actionable failure visible on compact screens after the action
  returns. The poller no longer overwrites a failed-start message when no run
  was created or when an older completed run exists.
- Kept consent actions visible beside an independently scrolling consent text,
  replaced the header initials with the supplied app icon, and removed fixed
  widths from responsive form controls.
- Resume now finds a stored paused run when no scan is currently active.

### Build and verification

- `src/blockprobe/VERSION` is `1.2.1-alpha`. The Android/iOS build number is
  derived from its numeric version core (`1002001`). Build outputs are cleaned
  before each target so old artifacts cannot be mistaken for the new build.
- The `all` build target covers Python, the host desktop bundle, Android APK
  and AAB, web, and iOS simulator when running on macOS. CI builds each native
  target on its supported runner.
- Verification: 214 tests passed and 2 live-capture tests were skipped; Ruff,
  compileall and `git diff --check` passed. Python wheel/sdist, Linux x86_64,
  Android APK/AAB and web bundle builds completed locally. The Linux desktop
  stayed open through a 15-second Xvfb launch. On Android 35, the rebuilt APK
  displayed “Scan started” after tapping Start and retained the completed
  status. Headless Chromium loaded
  the web bundle at narrow and wide sizes, but the Flet client remained on its
  startup screen, so the web runtime was not considered verified. Windows,
  macOS and iOS simulator builds require their native CI runners.

## [1.2.0] — 2026-10-29

First complete implementation. The current application version is maintained
in `src/blockprobe/VERSION`; package and runtime version strings are generated
from it.

### Added

- **Source management.** The Sources screen has distinct flows for adding a
  regulator feed and a single custom website; scans retain their separate
  provenance and selection behavior.
- **Single version source.** Flet builds now receive the display version from
  `src/blockprobe/VERSION`, with an Android build code derived from the same
  value.
- **Portal engine.** ADM Concessionari definition, layout-independent generic
  extractor (table/text/script passes, denylist, JavaScript-escape decoding),
  Liferay AJAX fragment discovery, pagination with query merging, `robots.txt`
  handling, append-only website lists with per-portal provenance.
- **Probe engine.** System-resolver DNS checks, sinkhole detection, TCP/TLS
  connection checks, bounded HTTP fetches with redirect tracking, host variants
  (listed, apex, `www`), evidence-based classifier with reason codes,
  confidence, blocker hints and `needs_review`, connectivity guard that pauses
  after repeated network failures, crash recovery.
- **Storage and cache.** Versioned SQLite schema, append-only triggers, guarded
  reset with backup, consent records, run/result/attempt evidence, and a
  generation-keyed in-process TTL cache. No external services are required.
- **Services and interfaces.** Refresh, scan, report and signature use cases in
  a shared Flet-free action layer; Typer CLI with consent, refresh, scan,
  pause/resume/cancel, reports, portals, custom websites, signature capture,
  diagnostics, log export, local web serving and user-level schedule commands;
  Flet UI with consent, run, sources, results, logs, settings and help screens.
- **Reporting.** Markdown, CSV and JSON exports, leak-only filtering, run
  context and comparison with the previous run.
- **Diagnostics.** System resolvers and hosts file, proxy/VPN detection,
  Private DNS/IPv6 notes, optional control-resolver comparison.
- **Scheduling.** Off-by-default user-level jobs (systemd user timer, launchd
  agent, Task Scheduler XML), rendered and written without elevation, plus
  leak-only desktop notifications when a notifier is available.
- **Quality.** 202 tests passing and 2 live-capture tests skipped by default,
  real ADM fixture plus a layout-independence fixture, fake-server integration
  tests, strict typing on `core`, ruff, and cross-platform CI.
- **Linux installation.** The portable tarball includes the AGPL license and a
  per-user installer that registers the desktop launcher. It works across
  distributions without root; native `.deb`, `.rpm` and Arch packages are not
  built. Linux archive and installer paths now have regression coverage.

### Changed

- **Removed non-working options.** MongoDB and Redis backends, the telemetry
  and update-check configuration, the unused portal `interpreter`/`run`/
  `language` metadata, the internal event bus and two dead UI buttons were
  deleted instead of left as stubs. A config file that still selects a removed
  backend fails to load with an error naming the replacement.
- **Language selection and light theme.** The interface ships in English
  (default), Italian, French, Spanish and Portuguese, switchable in Settings
  and applied instantly to every screen, with a consistent light theme and
  responsive phone layouts. Technical logs, diagnostics and exported reports
  stay in English. Translated consent text is a draft pending human legal
  review; English prevails and is not legal advice.

### Security

- Fetched pages are treated as hostile: no JavaScript execution, size-capped
  bodies, no stored response bodies, and no clickable links for scanned domains.
- HTTPS certificates and hostnames are verified. The cache is now in-memory
  JSON with bounded LRU/TTL behavior; it no longer stores pickle-backed entries
  on disk.
- Consent is required before any network activity; the consent version is
  stored locally and re-asked when the wording changes.

### Notes

- Two conservative BetBlocker fallback signatures ship in the package; live
  sentences are appended as review-needed literals (see `docs/SIGNATURES.md`).
- M6 remains open. Python wheel/sdist, a Linux x86_64 portable bundle and a
  universal Android APK were built locally. The APK uses the Android debug
  certificate; no physical Android install was tested. Windows, macOS and Linux
  CI package jobs passed; the latest APK job failed during runtime download and
  the workflow now caches and retries that build. A macOS CI socket test failure is fixed
  locally and also awaits rerun. Linux arm64, AppImage/Flatpak, production
  signing, notarization and store submissions remain unverified or incomplete. See
  `docs/PACKAGING.md` for the current artifact status.
