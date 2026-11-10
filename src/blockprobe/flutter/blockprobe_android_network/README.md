# BlockProbe Android network extension

Flet extension that lets the BlockProbe profile read the active Android
network's DNS servers and Private DNS state.

It is packaged as its own Python distribution because `flet build` only
bundles Flutter extensions that are installed under
`<site-packages>/flutter/<name>/`. The root `pyproject.toml` references this
directory through `[tool.flet.android.dependencies]` and
`[tool.flet.android.dev_packages]`.

The Kotlin plugin performs no network I/O and changes no settings: it reads
`ConnectivityManager`/`LinkProperties` for the network the device already
uses. `ACCESS_NETWORK_STATE` is an install-time permission, so no runtime
prompt is involved for this read. The app separately declares Android's
normal install-time `INTERNET` permission for scan and network socket access.
