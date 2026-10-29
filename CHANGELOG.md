<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<!-- Purpose: Release history. -->

# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased] — 2026-10-29

First complete implementation, targeting 1.1.1.

### Added

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
- **Language selection and dark theme.** The interface ships in Italian
  (default), English, French, Spanish and Portuguese, switchable in Settings
  and applied instantly to every screen, with a consistent dark theme and
  responsive phone layouts. Technical logs, diagnostics and exported reports
  stay in English. Translated consent text is a draft pending human legal
  review; English prevails and is not legal advice.

### Security

- Fetched pages are treated as hostile: no JavaScript execution, size-capped
  bodies, no stored response bodies, no link rendering for gambling domains.
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
