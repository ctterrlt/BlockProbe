<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<!-- Purpose: What BlockProbe stores, what leaves the device, and how to erase it. -->

# Privacy

BlockProbe is local-first. There is no account, no telemetry, no analytics, no
crash reporting and no BlockProbe server. This page lists exactly what is
stored, where it goes and how to remove it. It is a technical description, not
legal advice.

BlockProbe began as a way to check gambling-site blockers. Its built-in public
regulator feeds still focus on gambling domains, while custom domains support
personalized checks for other filtering needs, including adult-content filters
and parental controls. The app probes only selected domain endpoints; it does
not inspect browsing history or determine whether all content within a site is
filtered.

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

If the local consent record cannot be read, the app reports that check failure
and blocks network actions, including resuming a paused scan, until it can
determine the record's state. It does not treat a storage error as either
acceptance or a missing consent record.

## What is stored, and where

All paths are inside the configured data directory (default: the platform data
directory, overridable with `BLOCKPROBE_DATA_DIR`; config with
`BLOCKPROBE_CONFIG`).

| Data | Location | Notes |
| --- | --- | --- |
| Website list, runs, verdicts, evidence | `blockprobe.sqlite3` | Sites are append-only; evidence includes IPs, timings, HTTP status, redirect chains, body hashes and optional curl cross-check status — not bodies |
| Dynamic BetBlocker patterns | same database, `dynamic_signatures` table | Escaped visible sentences from the live block page, append-only; inspect with `signature list` |
| Consent version | same database, `consents` table | Version + timestamp only |
| Logs | `logs/blockprobe.jsonl` + in-memory ring buffer | Structured events; may contain domain names and errors |
| Reports | The path you choose in the platform save dialog; `reports/` under the app data directory if that dialog fails | Markdown/CSV/JSON/text you chose to write. A picker failure saves a uniquely named local copy and displays its path; cancelling the picker does not save a fallback copy. |
| Captured fingerprints | `signatures/<domain>.toml` | Only when you run `signature capture`; hash and key phrases, no body |
| Backups | `backups/` | Written before a guarded reset |
| Cache | process memory only (embedded, in-process) | JSON list views only; probe verdicts are never cached; cleared on exit; no cache directory is created |
| Scheduled job files | platform user directory | Only when you install a schedule; written, never enabled silently |

The diagnostics screen reads system configuration (resolvers, resolver labels,
`/etc/hosts` entries and sinkhole count, proxy environment variables, VPN
interfaces and provider hints, IPv6 availability). It does not upload it: it is
shown locally and included in reports you export. Resolver and VPN names are
inferred only from local configuration.

HTTPS connections use the platform trust store and verify certificate hostnames.
An invalid or intercepted certificate is recorded as a TLS failure; BlockProbe
does not fetch that unverified response body.

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
- **No website files saved.** Linked assets, media and archives are not
  requested or inspected. Response bodies are capped; only HTML is inspected.
  The optional curl cross-check may receive bounded bytes before it knows the
  content type, then discards non-HTML bodies without inspection or storage.
- **No GDPR or US privacy-law claim.** This is a technical statement, not legal
  advice or a compliance certification.

## What leaves the device

Only requests you start:

- the regulator portal pages you refresh, their paginated pages and the
  asynchronous table fragment; portal refresh does not request `robots.txt`;
- the domains being probed — as listed, plus their apex and `www` variants,
  over HTTPS/HTTP, through the system resolver;
- the neutral control sites during a scan (Google, Wikipedia and Cloudflare
  by default; configurable in Settings) to detect a dead
  connection or a filtering network and pause instead of producing fake results;
- `https://betblocker.org/blocked/` on every user-started scan, so the shipped
  BetBlocker wording does not silently go stale;
- an optional control-resolver comparison, only when you run that diagnostic;
- the domain you explicitly pass to `signature capture`;
- an additional check of the same URL when HTTPX has found block evidence,
  using the system `curl` executable if available. Curl sends a bodyless HEAD
  first, then retrieves a capped body only when that response declares HTML.
  This check is optional, uses the same common User-Agent and is not bundled
  on Android.

Outbound HTTP requests use one common Chrome-for-Windows User-Agent, including
website probes, portal refresh, the optional control-resolver request and
system `curl` checks. This is only a request header: HTTPX and curl do not
emulate Chrome's browser, TLS or network fingerprint and cannot prevent all
bot detection. Portal refresh does not request or enforce `robots.txt`; its
configured request-rate limit and retry backoff remain active.

After an HTTPX blocked result, the app optionally checks the same URL with the
system `curl` executable. A matching block signal is recorded as confirmation;
a different successful result keeps the primary verdict marked for review and
shows “Warning: curl opened the website successfully.” in results and exports.
A missing or failed `curl` check is recorded as unavailable or inconclusive and
leaves the primary HTTPX verdict intact. `curl` is not bundled, and the Android
app image used here has no curl executable.

No cookies are stored, no browser engine is used, JavaScript is never executed,
linked image or file URLs are not fetched, and response bodies are capped and
discarded after classification. The curl HEAD check reads no body; its bounded
GET is attempted only when HEAD reports HTML. A non-HTML response body is
discarded without inspection or storage. A `Set-Cookie` header is discarded
with the response and is not kept in evidence. DNS queries go through the
system resolver, so a DNS-based blocker or VPN sees them as it would any other
app.

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

## Responsible use and harm reduction

Results are indicative, never a guarantee that a blocker works in every browser
or on every network. Scanned domains are shown as plain text, never as links.
BlockProbe does not verify every browser, app, profile, or content category and
is not a parental-control, medical, or legal service. It does not provide
access to gambling or adult-content services. If gambling is affecting you,
use the support resources for your country.
