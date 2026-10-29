<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<!-- Purpose: Portal TOML format and how to add portals. -->

# Portals

A portal is a public regulator page that publishes a list of licensed or
blocked gambling websites. BlockProbe ships with one portal definition, Italy's
ADM Concessionari list; every portal is a TOML file that the generic engine can
run without new code.

The app currently bundles ADM as its only official regulator feed. Other
regulator feeds appear in source selection only after a reviewed TOML
definition is installed. “Custom websites” are user-entered domains, not
official portal feeds. The Sources screen is where users add those domains.

## Where definitions live

- Built-in definitions: the repository's `portals/` directory.
- User definitions: the `portals/` directory inside the BlockProbe data
  directory (`BLOCKPROBE_DATA_DIR`, or the platform data directory).
- Both directories are loaded at startup and merged into the `portals` table in
  SQLite. The database keeps refresh state; the TOML file keeps the definition.
- Every loaded, enabled definition is listed in the scan source picker. A new
  portal needs a validated source URL and extraction behavior; adding a custom
  domain does not create a regulator feed.

## TOML format

```toml
uuid = "44d93121-094d-4b4c-9e87-1c59380ec20f"   # stable id, generated if absent
name = "ADM - Concessionari gioco a distanza"
enabled = true
default = true                                   # preselected in the UI

[source]
country = "IT"
start_url = "https://www.adm.gov.it/portale/..."  # required, http(s)
strategy = "auto"                                 # auto | table | text | script
max_pages = 50                                    # pagination cap
rate_limit_per_sec = 1.0                          # one request per second
respect_robots = true                             # honour robots.txt when available
exclude_domains = ["adm.gov.it", "mef.gov.it", "sogei.it"]
include_regex = []                                # optional host allowlist patterns
```

Notes:

- `start_url` must be an absolute `http`/`https` URL. Everything else has a
  safe default.
- `exclude_domains` filters both the host itself and its subdomains; the
  built-in denylist (`adm.gov.it`, `mef.gov.it`, `sogei.it`, analytics hosts)
  always applies.
- `include_regex` is an optional list of regular expressions; when set, a host
  must match at least one pattern to be kept.
- `rate_limit_per_sec` is enforced between page requests, not per host.
- `respect_robots = true` means a disallowed path stops the refresh with a
  clear message. A `robots.txt` that cannot be fetched (for example HTTP 403)
  is treated as *unavailable* and logged as a warning, not as a failure.

## How pages are parsed

The generic extractor (`blockprobe.portals.generic_extractor`) is
layout-independent. It runs three passes and keeps the union:

1. **Table pass** — text inside table cells, including Liferay's
   `fragment`-rendered tables.
2. **Text pass** — visible text outside `<script>` and `<style>`.
3. **Script pass** — only URL-pattern matches and quote-enclosed hostnames
   such as `["www.example.it", ...]` inside scripts. Bare JavaScript property
   chains (`Liferay.Data`, `event.page`) are rejected, as are code identifiers.

Hosts are then normalised: case is folded, IDNA is applied, and each host is
mapped to its registered domain (`blockprobe.portals.domain_normalizer`). Both
the registered domain and the concrete hosts are stored, so variant probing can
test the apex and `www` forms later.

### Pagination

Paginated lists are followed until `max_pages` or until a page repeats. When a
`?pager=N` link carries a query string, BlockProbe merges it over the base
query with the link winning, so Liferay parameters such as `p_p_id` and the
active filters survive. Pagination labels are matched in English and Italian
("next", "successiva", "»", "›", …).

### Asynchronous fragments

Liferay portals (ADM) render the table through an AJAX fragment. BlockProbe
discovers the fragment URL from the page itself by looking for
`p_p_lifecycle=2` requests, decodes JavaScript escapes (`\x3d`, `\u003d`) and
fetches that fragment with the same rate limit. No fragment URL is hard-coded,
so the site can change its portlet id without breaking BlockProbe.

## Refreshing

- `blockprobe refresh` (CLI) or **Sources → Update website list** (UI) runs a
  refresh now.
- A run refreshes only *stale* portals: older than `refresh_ttl_hours`
  (default 24). `--no-refresh` skips this entirely.
- Updates are **append-only**: new domains and hosts are added, existing rows
  are never deleted, and a domain that disappears from the portal stays in the
  local list (marked by its missing `last_seen_at`).
- Each attempt is recorded in `refresh_log` with page count, found/added
  counters and the error, so the UI can show "never updated" or the last
  failure honestly.
- The refresh is single-flight: a lock prevents a manual and a scheduled
  refresh from overlapping.

## Adding a portal

1. Copy `portals/adm_concessionari.toml` to a new file and edit `name`,
   `start_url`, `exclude_domains` and `max_pages`. Leave `uuid` out; one is
   generated on first load.
2. Run `blockprobe refresh --portal <name-or-uuid>` and inspect the counters.
3. Check the extracted hosts with `blockprobe portals list` and by running a
   scan; report ambiguous pages as an issue instead of adding guessed rules.

The UI's **Sources** screen has separate forms for adding a public regulator
feed and for adding custom websites. A portal feed imports a published list
when the user updates it; each custom website is added directly and is never
fetched as a source or removed by a portal update. The scan source picker keeps
portal-derived and custom domains distinct, and users can select either or
both.
