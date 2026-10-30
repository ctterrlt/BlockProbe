<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<!-- Purpose: Architecture overview: core, services and interfaces. -->

# Architecture

BlockProbe is a local-first tool. Everything runs on the user's device: there
is no backend service, no account and no telemetry. The architecture exists to
keep three concerns separate:

1. **Facts about the network** — what the device actually observes.
2. **Classification** — which facts justify which verdict, with evidence.
3. **Presentation** — Flet UI, CLI and the local web UI, which all share the
   same use cases.

## Layers

| Layer | Package | Rules |
| --- | --- | --- |
| Core | `blockprobe.core` | Models, enums, errors, config, compliance facts. No network, no UI, no storage. Strictly typed (`mypy --strict`). |
| Engines | `blockprobe.portals`, `blockprobe.probe`, `blockprobe.diagnostics` | Pure or near-pure logic: extraction, normalisation, probing, classification, health scoring. No UI imports. |
| Services | `blockprobe.services`, `blockprobe.storage`, `blockprobe.cache`, `blockprobe.scheduler` | Use cases, persistence, TTL cache, user-level jobs. |
| Interfaces | `blockprobe.ui`, `blockprobe.cli` | Presentation only. Every button and command goes through `blockprobe.ui.actions`. |

The dependency direction is one-way: interfaces → services → engines → core.
`blockprobe.ui.actions` is Flet-free so the CLI, the tests and a future web UI
can reuse the exact same use cases; `blockprobe.ui.app` and the `ui/` screens
only build widgets and route events.

The app version is read from `src/blockprobe/VERSION`. Hatchling uses that same
file for wheel metadata, native installer builds read it when generating their
version fields, and `scripts/build.py` passes it to Flet as the display version
and derives the Android build code from it. The Flet UI uses a responsive
Material 3 theme that follows OS brightness by default, with saved light and
dark overrides. The scan source picker includes every enabled portal definition
and the user-maintained custom domain list.

```text
src/
├── main.py                       # desktop entry point: ft.run(ui.app.main)
└── blockprobe/
    ├── core/                     # models, enums, errors, config
    ├── i18n.py                   # translation catalogs (it, en, fr, es, pt)
    ├── portals/                  # normalisation, generic extractor, pagination, robots, loader
    ├── probe/                    # DNS, TCP/TLS, HTTP fetch, interception, classifier, health, runner
    ├── diagnostics/              # system DNS, resolver comparison, VPN/proxy/DNS-filter detection
    ├── storage/                  # backend interface, SQLite, migrations
    ├── cache/                    # embedded in-process TTL cache and key scheme
    ├── services/                 # refresh, scan, report, signature, Runtime
    ├── scheduler/                # user-level job rendering/installation + notifications
    ├── ui/                       # Flet shell and screens + shared actions
    └── cli.py                    # Typer CLI
```

## Data flow

### Portal refresh (`blockprobe.services.refresh`)

1. The portal definition (TOML file in `portals/`) is loaded and stored in the
   SQLite `portals` table; the file defines the start URL, rate limit, robots
   policy and exclusion list.
2. The page is fetched once, `robots.txt` is respected when available, and the
   generic extractor discovers candidate hosts in three passes: table cells,
   visible text, and quoted hostnames inside `<script>` blocks (JSON arrays).
   Bare JavaScript property chains are rejected; URL-pattern matches and
   quote-enclosed hostnames are accepted.
3. Hosts are normalised to a registered domain plus host list
   (`blockprobe.portals.domain_normalizer`) and filtered through the built-in
   and per-portal exclusion lists (`adm.gov.it`, `mef.gov.it`, `sogei.it`, …).
4. Pagination links are followed with `_resolve_page_url`: the link's own query
   is merged over the base query (link wins when the host and path match), so
   `p_p_id` and filters survive `?pager=N` links. Extraction stops at
   `max_pages` and on repeated signatures.
5. New hosts are **appended** to `sites`/`site_hosts`/`site_portals`. Rows are
   never deleted by a refresh; SQLite triggers abort any raw `DELETE` that would
   remove portal-linked rows unless the guarded reset set `reset_unlocked`.

The ADM Liferay site renders its table through an asynchronous fragment; the
loader discovers the fragment URL from the page itself (`p_p_lifecycle=2`) and
decodes JavaScript escapes (`\x3d`, `\u003d`). No fragment URL is hard-coded.

### Scan run (`blockprobe.services.scan`, `blockprobe.probe.runner`)

1. `ScanService.start()` records a `runs` row (`pending`), then spawns a worker
   thread. The UI and CLI never block: they poll storage.
2. For each stored website the runner builds variant targets (host as listed,
   apex, `www`) and probes in order: DNS resolution → TCP/TLS connect →
   bounded HTTP fetch with redirects.
3. The classifier turns observations into a verdict with `ReasonCode`,
   confidence and evidence. A blocked signature, a DNS sinkhole, a regulator
   redirect or a connection-level failure are the only roads to `BLOCKED`;
   HTTP status codes alone never decide. Unmatched responses are `OPENED`
   (`unclassified`, low confidence, `needs_review = true`).
4. Results are written to `site_results` plus `probe_attempts` (per host/scheme
   evidence: IP, timings, HTTP status, redirect chain, body hash, rule id).
5. `RunControl` carries pause/cancel flags. A network guard checks a neutral
   control URL every 10 domains by default and pauses the run after two
   consecutive failures, so a dead connection cannot produce fake "blocked"
   verdicts.
6. Crash recovery marks runs left `running` as `paused` at startup; resume
   continues from stored results.

### Reporting (`blockprobe.services.report`)

Reports are generated from stored rows only — never by re-probing. Markdown,
CSV and JSON exports include the run context (app version, OS, network
context), the per-domain verdicts and a comparison with the previous run
(newly leaking, newly blocked, still leaking, resolved).

## Storage

SQLite is the only storage backend. Config files that still select a removed
backend (`mongo`, `redis`) fail to load with a clear `ConfigError` naming the
supported value, so nothing silently pretends to work.

| Table | Purpose |
| --- | --- |
| `meta` | schema version, cache generation, reset unlock flag, consent metadata |
| `portals` | portal definitions and refresh state |
| `sites`, `site_hosts`, `site_portals` | append-only website list with per-portal provenance |
| `custom_sites` | user-added websites (never fetched from the network) |
| `refresh_log` | per-portal refresh history |
| `runs`, `site_results`, `probe_attempts` | scan history and evidence |
| `consents` | accepted consent text versions and timestamps |
| `dynamic_signatures` | append-only live BetBlocker text patterns, marked for review |

The schema is versioned (`SCHEMA_VERSION = 3`). Version 3 adds the persistent
append-only `dynamic_signatures` table used for live BetBlocker text patterns.
A database written by a newer version is refused instead of guessed. A guarded
privacy wipe is the only deletion path for learned signatures; it requires
explicit confirmation and unlocks deletion only for that operation.

## Cache

The cache is keyed by **content generation and schema version**, not by time
alone. A refresh bumps the generation and invalidates list-derived views, while
probe verdicts are never cached. The backend is embedded and in-process: there
is no server to run, no cache file is written, and cache entries are cleared
when the process exits. Values are serialized as JSON, not Python pickle data.

## Threading model

- Flet owns the UI thread. Long actions run via `page.run_thread`, so buttons
  never freeze the window.
- A daemon poller reads run progress from the database every second and updates
  the Run and Results screens. It survives restarts because storage is the
  single source of truth.
- The scan worker is one thread per run, guarded by a lock so manual and
  scheduled runs cannot overlap.

## Security and hostile input

- Fetched pages are treated as hostile: no JavaScript is executed, no file is
  downloaded, bodies are size-capped, and response bodies are never stored
  (only a hash and key phrases in a captured fingerprint).
- Gambling domains are rendered as plain text, never as links.
- Probing is HTTP-only outbound; no port scanning, no root, no permissions
  beyond network access.
- The local web UI binds to `127.0.0.1` and is intended for the same machine.

## Extension points

- **New portal:** add a TOML file under `portals/`; the generic runner needs no
  code (see `docs/PORTALS.md`).
- **New signature rule:** capture a real response, reproduce it, then add a
  reviewed rule (see `docs/SIGNATURES.md`). Rules are never invented.
- **New storage/cache backend:** implement `blockprobe.storage.base.Storage`
  or `blockprobe.cache.base.Cache` and register it in the service factory.
