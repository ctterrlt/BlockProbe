<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<!-- Purpose: Signature capture format, review policy and verified rules. -->

# Signatures

BlockProbe classifies a page from evidence, not from assumptions. Signature
rules are the last resort after connection-level evidence. **No invented rules
are shipped**: the repository contains only the two BetBlocker rules reproduced
from the live block page, plus optional test-only rules. A scan that cannot
justify a verdict reports `opened` with low confidence and
`needs_review = true` instead of guessing.

## What is evidence without a rule?

These outcomes are detected directly and never need a signature:

| Observation | Verdict | Reason |
| --- | --- | --- |
| DNS answer is `0.0.0.0`, `::`, loopback, private, link-local or reserved | blocked | `dns_sinkhole` |
| DNS failure, refused/reset/timed-out connection, TLS failure | blocked or unreachable, according to the failure class | `dns_failure`, `connect_refused`, … |
| Redirect chain or embedded target is `betblocker.org/blocked/` | blocked, immediately | `blocker_redirect` |
| Redirect chain ends on `adm.gov.it` / `mef.gov.it` | blocked | `regulator_redirect` |
| HTTP challenge page (Cloudflare, cookie wall) | needs review | `challenge_page` |
| Page with stop-gambling markers and no block markers | opened, with positive markers | `positive_markers` |
| No match | opened, unclassified, low confidence, needs review | `unclassified` |

Rules are needed for browser-visible block pages served after a successful
connection (BetBlocker, Gamban, DNS filter portals, ISP blackouts). The
classifier also has conservative generic block markers for a direct page and
for an embedded target; those results are always `needs_review` unless a
verified rule matched.

## Shipped BetBlocker rules

`signatures/betblocker.toml` contains two verified block rules:

- `betblocker-blocked-by`: title/body `Blocked by BetBlocker`.
- `betblocker-domain-blocked`: `This domain was blocked by BetBlocker` and the
  owner-reported `This … was blocked by BetBlocker` form.

A redirect or new-window target under `https://betblocker.org/blocked/` is
handled before any body regular expression. The URL alone is definitive, so a
changed body cannot hide the block.

## Live refresh and append-only learning

Every user-started scan (and `blockprobe signature refresh`) fetches
`https://betblocker.org/blocked/` through the same bounded, cookie-free HTTP
path. It never executes JavaScript and never keeps the response body. It then:

1. Matches the live title and visible block sentences against the verified
   defaults.
2. Converts only new, block-specific sentences into **escaped literals**
   (never interpolated regular expressions).
3. Appends them to the `dynamic_signatures` SQLite table with `needs_review`
   set, so a human can inspect them with `blockprobe signature list`.
4. Leaves every previously stored pattern in place, even if it disappears from
   the live page. Only the explicit privacy wipe unlocks deletion.

A failed fetch is recorded in run health; the static rules and previously
learned patterns remain active. Normal runs never remove or rewrite a pattern.

## Capturing a fingerprint

```console
blockprobe consent accept          # first run only
blockprobe signature capture casino.example
```

Capture performs a single HTTPS request (one HTTP retry if HTTPS never gets a
response), records the DNS answers, status, title, final URL, redirect chain,
headers, body SHA-256 and a short list of key phrases, and writes a TOML file to
`<data-dir>/signatures/<domain>.toml`. Response bodies are not stored.

Review the file, then reproduce the page at least twice on a real device. A
fingerprint alone is **not** a rule: capture never changes how scans classify.

## Rule format

Rules live in `signatures/*.toml` in the project, config or data directory:

```toml
[[rules]]
id = "betblocker-page-2026-01"
kind = "block"                  # block | dead | positive | challenge
hint = "betblocker"            # betblocker | gamban | dns_filter | isp_adm | unknown
confidence = "high"             # low | medium | high
note = "Reproduced on Windows 11, BetBlocker 6.x, 2026-01."
title = ["titel-regex"]         # case-insensitive regular expressions
body = ["sito non raggiungibile"]
headers = ["server: nginx"]     # "header-name: regex"
redirect = ["block\\.example"]  # matched against each redirect URL
```

Rules are evaluated in kind order: `block`, `dead`, `positive`, `challenge`.
The first matching rule wins. A rule that matches nothing is inert; a rule that
matches too much is a bug. Keep patterns narrow and anchor them where possible.

## Promotion checklist

A rule may be merged only when all of these are true:

1. The response was captured on a real device, with the timestamp and network
   path in the note.
2. The block page was reproduced at least twice, ideally on two networks.
3. The patterns do not match the operator's normal content and do not depend on
   timestamps, session ids or user-specific data.
4. The `hint` is justified: the page itself identifies the product, or the
   behaviour (DNS sinkhole vs. ISP page) is unambiguous.
5. The title/body/headers of a plain "site is down" page are not classified as
   a blocker without a second signal.

Never derive rules from memory, from a screenshot, or from a page you did not
fetch yourself. If a case is ambiguous, leave the rule out: the app will report
`needs_review` and a maintainer can ask for a capture instead of shipping a
wrong verdict.

## Confidence

- `high` — unique blocker page plus a supporting signal (header, redirect or
  known host).
- `medium` — a specific block page without a second signal.
- `low` — generic content that could be an ordinary error page.

Confidence is displayed in reports; `needs_review` is set when the classifier
could not justify a verdict. Low-confidence "blocked" results are flagged for
review, never silently treated as proof that the blocker works.

## Regression cases

`tests/regression/test_known_cases.py` contains structural entries for
`bwin.it` and `amazon-slots.it`. Live-capture assertions are skipped unless the
environment provides a capture; no expected verdict is hard-coded from
imagination. Synthetic rules used in tests are test-only and are never loaded
from the shipped signature files.
