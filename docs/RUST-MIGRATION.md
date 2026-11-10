<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<!-- Purpose: Staged Rust migration plan and native filtering constraints. -->

# Rust migration

## Goal and current state

The goal is to move measured, error-sensitive engine work into Rust while
keeping BlockProbe usable during the migration. The future product direction
includes a continuously active DNS/VPN-based blocker. That blocker does not
exist today: the current app checks selected domains on the device's network
path, and it does not install filters, intercept traffic, or run a background
network service.

The production scan path uses a dependency-free Rust `cdylib` through a narrow
C ABI for aggregate verdict selection, sinkhole-IP classification, and the
OS-resolver fallback. Python still performs primary DNS A/AAAA/CNAME queries
with dnspython, the opt-in Cloudflare DoH fallback with httpx, and HTTP probes.
These operations are network-wait bound; moving the requests to Rust would not
reduce upstream response latency. Blocking DNS work runs in a worker thread,
and the UI remains Python/Flet.

Rust does not guarantee fewer classification errors, lower energy use, or zero
runtime overhead. Deterministic parity tests cover the migrated behavior.
The user has chosen to make the Rust engine mandatory for current scans, so a
missing library is a startup/scan error and there is no Python verdict
fallback. The measured Rust system-resolver fallback is effectively tied with
Python's `socket.getaddrinfo` on this host, so it remains in Rust under the
user's tie rule. Primary DNS A/AAAA/CNAME queries and HTTP remain Python.

### Verified status — 2026-11-10

- Linux Python 3.14 wheel built as `py3-none-linux_x86_64`, declared
  `Root-Is-Purelib: false`, loaded the packaged Rust library, and passed the
  installed-wheel CLI self-check.
- The Rust library compiled for Linux and Android `arm64-v8a`, `armeabi-v7a`
  and `x86_64`. No APK/AAB or other app package was built for this validation.
- The host C ABI reports version 1; Python rejects an unexpected ABI version.
  Rust and Python tests compare aggregate selection and sinkhole classification.
- Windows and macOS wheel build/load checks are configured in Release CI, but
  have not run in this workspace. iOS simulator and Pyodide/Web dynamic loading
  are also unverified. The F-Droid recipe has not been executed.
- The 2026-11-10 Release Web job showed that Emscripten emits
  `blockprobe_core.wasm` without a `lib` prefix, both at the release root and
  under `deps`. The builder now accepts that name and its optional `lib`
  prefix, then stages the module at the `.so` path used by the Pyodide loader.
  Regression tests cover those output forms. This workspace has no `emcc`, so
  the actual Web build and runtime load remain pending a successful CI run.
- The Rust toolchain is pinned to 1.98.1. The crate has no external Cargo
  dependencies and native builds use Cargo offline. F-Droid still downloads
  the pinned Android Rust target components during metadata prebuild; its
  build-server behavior has not been confirmed.
- `scripts/benchmark_engine.py` compares all production Rust primitives with
  Python references and checks output parity before timing. On Linux x86_64,
  Python 3.14.9, three runs of 5,000 samples per operation (500 warmups),
  median run p50/p95 timings were:

  | Workload | Rust p50 / p95 | Python p50 / p95 |
  | --- | ---: | ---: |
  | Aggregate six variants | 8.7 / 10.2 µs | 2.4 / 3.1 µs |
  | Classify twenty IPs | 9.4 / 11.3 µs | 77.3 / 91.1 µs |
  | Resolve `localhost` through OS resolver | 107.5 / 177.3 µs | 107.0 / 161.5 µs |
  | Combined aggregate and IP classification | 20.1 / 24.2 µs | 84.1 / 100.3 µs |

  The resolver p50s differ by about 0.6 µs across the median runs. Its p95
  varied widely between runs for both implementations, and those ranges
  overlap; this is a practical tie. Rust remains selected for that fallback.
  Rust is slower for aggregation alone, much faster for the
  20-address classification fixture, and faster for the combined deterministic
  engine workload. The benchmark also reports additional traced Python
  allocations, but that does not count Rust native heap or process RSS and
  must not be presented as a memory comparison. This is not a full network
  scan benchmark: DNS, HTTP, device and idle-CPU costs were not measured.

## Boundary

Keep Flet, the CLI, consent, translations, reports, and current local storage
in Python initially. Introduce Rust behind a small, versioned engine interface
that accepts explicit inputs and returns typed outcomes, evidence, or a typed
error. The interface must not expose Python object layouts, SQLite connections,
or UI controls to Rust.

Do not move DNS protocol queries, HTTP, VPN, packet filtering, or storage into
Rust merely to call the project Rust-based. Select another component only when
Rust benefits that operation and it has a parity and platform plan.

The integration uses a versioned C ABI and `ctypes`; do not add PyO3 or a
per-site worker process without a separate migration plan. A native library
must report initialization and runtime failures through an actionable error
boundary and must not silently fall back to different verdict semantics.

## Migration stages

1. **Parity and measurements.** Keep Python reference behavior and
   deterministic fixtures for migrated operations. Measure representative scan
   latency, peak memory, CPU and idle/background use on desktop and Android
   before making full-scan performance claims. Run
   `PYTHONPATH=src python scripts/benchmark_engine.py` for the deterministic
   native-operation comparison; this does not replace full-scan measurements.
2. **Native packaging.** Build and load the C ABI library in platform-tagged
   Python wheels, desktop packages and Android ABIs. CI also attempts iOS
   simulator and Pyodide Web builds; runtime loading there must be verified
   before those targets are claimed as supported. Verify loading from the
   packaged app, not only from source.
3. **Expand selectively.** Migrate another operation only when Rust provides a
   concrete correctness or resource benefit and its behavior can be compared
   with the Python reference.
4. **Native filtering project.** Treat continuous DNS/VPN blocking as a
   separate product milestone with platform-specific design, user consent,
   lifecycle handling, diagnostics, and security review.

## Platform constraints

- **Python and desktop:** Python 3.14 remains the current source runtime.
  Native wheels must match the supported OS/CPU matrix. They are tagged
  `py3-none-<platform>` because the C ABI does not use CPython's ABI. Building
  from an sdist requires the pinned Rust/Cargo toolchain.
- **Android:** continuous filtering requires Android's `VpnService` or another
  permitted native integration, a visible user-approved VPN session, lifecycle
  and battery handling, and per-ABI packaging. A Rust library alone cannot
  register or own that service. Flet packaging and Android permissions must be
  verified on the configured AVD and on a real device before claiming support.
- **iOS:** continuous packet/DNS filtering requires Apple's Network Extension
  capabilities, entitlements, signing, and approval. These cannot be replaced
  by a Rust library or ordinary Flet background work.
- **F-Droid:** native build inputs must be pinned and reproducible. Crates and
  toolchains must be available to the F-Droid build recipe without reaching
  arbitrary networks during the isolated build. This crate has no external
  dependencies; the recipe obtains pinned Android standard-library targets in
  metadata prebuild, which remains to be tested on the F-Droid build server.
  Review source inclusion and licensing for every crate.
- **Other desktop platforms:** background service installation and network
  interception are separate OS-specific work. Keep the current user-started
  scan path available if a native service is unavailable or denied.

## Acceptance criteria

Before claiming a Rust performance or resource benefit, record baseline and
candidate measurements using the same fixture set and hardware. Rust is
currently mandatory by user choice; this does not establish a speed or memory
benefit. At minimum:

- All shared deterministic fixtures produce identical verdicts, reason codes,
  confidence, review flags, and evidence fields. Any intentional semantic
  change needs its own reviewed proposal and regression fixtures.
- The candidate introduces no unexplained error increase or lost evidence;
  typed native errors are mapped to user-visible actionable outcomes.
- Record p50/p95 scan latency, peak resident memory, CPU time, and idle CPU for
  the same workload. State the device, OS, build mode, sample count, and command.
  A claim of no measurable regression must include the observed variance and
  measurement limits.
- The Python app remains runnable when the native library is unavailable only
  if an explicit, tested compatibility policy defines that behavior. Never
  label the Rust feature active when it did not load.
- Build, package, and launch on each target claimed as supported; include the
  Android ABI and F-Droid recipe checks before claiming native support in
  those artifacts.

## Not in this migration slice

This document does not authorize a packet filter, a VPN service, a root helper,
an always-on background daemon, or broader permissions. Each requires a
separate exact-file proposal, platform design, privacy/consent review, and
measured acceptance criteria. No continuous filtering is enabled by this
documentation change.
