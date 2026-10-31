<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<!-- Purpose: Security policy and vulnerability disclosure contact. -->

# Security Policy

BlockProbe treats fetched web content as hostile input, stores everything
locally and never executes JavaScript from scanned pages. Security reports are
welcome, and private disclosure is strongly preferred.

BlockProbe checks selected domains against the network path of the current
device. It started with gambling-site blockers and also supports personalized
checks for other website filters. It does not inspect complete site content or
certify a blocker or parental-control setup.

HTTPS requests verify certificate chains and hostnames. The transient view
cache stores JSON in process memory and does not deserialize cache files. The
current locked dependencies passed `pip-audit` on 2026-10-29; this checks known
dependency advisories only and is not a security certification of the app.

## Supported versions

| Version | Supported |
| --- | --- |
| Version recorded in `src/blockprobe/VERSION` (source and wheel) | Yes |
| Older revisions | No |

M6 packaging is in progress. A local Android APK signed with the default debug
certificate and a Linux x86_64 portable archive have been built. The project
owner has exercised the Android app; an Android 35 emulator was also used to
check the UI at two phone sizes. These builds are not production releases.
Reports about source installs, Python packages and local artifacts follow the same policy. See
[docs/PACKAGING.md](docs/PACKAGING.md) for verified artifacts and remaining work.

Consent storage failures must remain distinct from a missing or outdated
consent record. A failed check does not authorize network activity, including
resuming a paused scan, or show a false consent prompt. File-picker failures
retain a local export copy where possible and display its location; export
errors must not be hidden.

## How to report

Use GitHub's private vulnerability reporting on the repository
(**Security → Report a vulnerability**). Do not open a public issue for an
undisclosed problem, and do not include third-party personal data.

Please include:

- affected version (`blockprobe version`) and platform;
- a minimal reproduction: command, URL or fixture, and observed behaviour;
- the impact you believe it has (for example: code execution, local file
  access, data exfiltration, false verdicts);
- any suggested fix, if you have one.

## What to expect

- Acknowledgement within about a week, best effort (this is a volunteer,
  non-commercial project; there is no bug bounty).
- A fix or a documented decision, credited in the changelog if you want.
- Please give us reasonable time to release a fix before publishing details.

## Scope

In scope:

- remote code execution, path traversal or unsafe deserialization triggered by
  fetched pages, portal files, signature files or imported data;
- anything that causes data to leave the device without the user's action, or
  that contradicts `docs/PRIVACY.md`;
- storage corruption, migration failures or bypasses of the append-only and
  guarded-reset protections;
- classifier logic that can produce a "blocked" verdict without real evidence
  or overstate what a domain-level check proves (a safety property of the tool,
  not just a bug).

Out of scope:

- help or advice to bypass blockers, filters or the law;
- the accuracy of a blocker's own claims or of regulator lists;
- findings that require a modified build, developer mode, or a compromised
  local account;
- denial of service against a third-party site from your own device.

## Safe harbor

Good-faith research on your own devices, following this policy, will not be
pursued or reported by the maintainers. Do not test against systems you do not
own or lack permission to test.
