<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<!-- Purpose: What BlockProbe stores, what leaves the device, and how to erase it. -->

# Privacy

BlockProbe is local-first. There is no account, no telemetry, no analytics, no
crash reporting and no BlockProbe server. This page lists exactly what is
stored, where it goes and how to remove it. It is a technical description, not
legal advice.

## First-run consent

Before the first network request the app shows a consent screen with five
sections: what BlockProbe does, what it does not do, privacy, terms, and the
permission to test only devices you own. The same texts are printed by
`blockprobe consent accept`; the CLI and UI share `ui/actions.CONSENT_TEXTS`.

Acceptance stores the consent version (`2026-10-19.1`) and a timestamp in the
local `consents` table. `blockprobe consent status` shows the state. Refusing
consent leaves everything untouched; the app will not refresh or scan until the
texts are accepted. Bumping the version (when the wording changes) makes the
app ask again.

## What is stored, and where

All paths are inside the configured data directory (default: the platform data
directory, overridable with `BLOCKPROBE_DATA_DIR`; config with
`BLOCKPROBE_CONFIG`).

| Data | Location | Notes |
| --- | --- | --- |
| Website list, runs, verdicts, evidence | `blockprobe.sqlite3` | Sites are append-only; evidence includes IPs, timings, HTTP status, redirect chains and body hashes — not bodies |
| Dynamic BetBlocker patterns | same database, `dynamic_signatures` table | Escaped visible sentences from the live block page, append-only; inspect with `signature list` |
| Consent version | same database, `consents` table | Version + timestamp only |
| Logs | `logs/blockprobe.jsonl` + in-memory ring buffer | Structured events; may contain domain names and errors |
| Reports | `reports/` when you export | Markdown/CSV/JSON/text you chose to write |
| Captured fingerprints | `signatures/<domain>.toml` | Only when you run `signature capture`; hash and key phrases, no body |
| Backups | `backups/` | Written before a guarded reset |
| Cache | memory and `cache/` (embedded, in-process) | List views only; probe verdicts are never cached |
| Scheduled job files | platform user directory | Only when you install a schedule; written, never enabled silently |

The diagnostics screen reads system configuration (resolvers, resolver labels,
`/etc/hosts` entries and sinkhole count, proxy environment variables, VPN
interfaces and provider hints, IPv6 availability). It does not upload it: it is
shown locally and included in reports you export. Resolver and VPN names are
inferred only from local configuration.

## Privacy and legal scope

Every report and the `blockprobe scope` command state the same facts:

- **No age verification.** BlockProbe does not ask for or verify age, and it is
  not an age-assurance control.
- **No cookies.** No cookie banner is accepted, no cookie jar is kept, and a
  `Set-Cookie` response header is not stored in evidence or replayed.
- **No uploads.** Results, reports and personal data are not sent to BlockProbe
  or an analytics service.
- **No website terms accepted.** No terms of service, age gate, cookie wall or
  login is accepted on the user's behalf.
- **No images or files downloaded.** Only bounded HTML responses are fetched
  for classification; assets, media and archives are ignored.
- **No GDPR or US privacy-law claim.** This is a technical statement, not legal
  advice or a compliance certification.

## What leaves the device

Only requests you start:

- the regulator portal pages you refresh, their paginated pages, the
  asynchronous table fragment, and `robots.txt` when respected;
- the domains being probed — as listed, plus their apex and `www` variants,
  over HTTPS/HTTP, through the system resolver;
- the neutral control sites during a scan (Google, Wikipedia, Cloudflare and
  `example.com` by default; configurable in Settings) to detect a dead
  connection or a filtering network and pause instead of producing fake results;
- `https://betblocker.org/blocked/` on every user-started scan, so the shipped
  BetBlocker wording does not silently go stale;
- an optional control-resolver comparison, only when you run that diagnostic;
- the domain you explicitly pass to `signature capture`.

No cookies are stored, no browser engine is used, JavaScript is never executed,
image or file URLs are not fetched, and response bodies are capped and
discarded. A `Set-Cookie` header is discarded with the response and is not kept
in evidence. DNS queries go through the system resolver, so a DNS-based blocker
or VPN sees them as it would any other app.

## Logs and debug mode

Normal logs record outcomes and errors. Debug mode (`app.debug` or
`BLOCKPROBE_DEBUG=1`) additionally records DNS answers, redirect hops and
timings; it never records full response bodies. Treat exported logs and reports
as sensitive: they contain the domains you checked and when you checked them.

## Retention and erasure

- Results and logs stay until you delete them. There is no automatic upload or
  expiry.
- **Settings → Delete all my data** removes runs, results, the website list,
  custom websites, consents and captured evidence after an explicit typed
  confirmation. It also unlocks and removes the append-only dynamic BetBlocker
  patterns; no normal run can delete them. `blockprobe` exposes the same guarded
  action.
- **Reset website list** removes portal-derived sites (custom sites are kept),
  writes a database backup first, and is the only path that can delete
  append-only rows.
- Reports and exported log zips are ordinary files: delete them with your file
  manager.
- Uninstalling the app does not delete the data directory; remove it yourself
  if you want a clean slate.

## Network and platform permissions

BlockProbe needs outbound network access only. It does not request root or
administrator rights, does not scan ports, and does not install filters or
modify system DNS. The local web UI binds to `127.0.0.1`; do not expose it to a
network. Scheduled scans run as user-level jobs (systemd user timer, launchd
agent, Task Scheduler) and are off by default. Android supports user-started
scans only; Android background scheduling is not implemented. Android Private
DNS settings are not reliably readable by the app, so diagnostics cannot
confirm that setting on every device.

## Harm reduction

Results are indicative, never a guarantee that a blocker works in every browser
or on every network. Gambling domains are shown as plain text, never as links.
If gambling is affecting you, use the support resources for your country; the
app is not a medical or legal service and does not provide access to gambling
sites.
