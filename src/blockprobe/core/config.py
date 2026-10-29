# SPDX-License-Identifier: AGPL-3.0-or-later
"""Configuration loading, defaults and per-user paths.

Configuration lives in ``config.toml`` under the platform's per-user config
directory. Paths in that file may be ``auto``, which resolves to the
platform-specific data directory from platformdirs. Environment overrides
(``BLOCKPROBE_CONFIG``, ``BLOCKPROBE_DATA_DIR``, ``BLOCKPROBE_DEBUG`` and the
legacy ``GS_DEBUG``) exist for tests and headless deployments.
"""

from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from platformdirs import user_config_dir, user_data_dir

from blockprobe import APP_NAME, __version__
from blockprobe.core.errors import ConfigError
from blockprobe.i18n import DEFAULT_LANGUAGE, LANGUAGES

DEFAULT_CONTROL_URL = "https://www.google.com/generate_204"
DEFAULT_CONTROL_URLS = [
    "https://www.google.com/generate_204",
    "https://www.wikipedia.org/",
    "https://www.cloudflare.com/",
]


def default_data_dir() -> Path:
    return Path(user_data_dir(APP_NAME, appauthor=False))


def default_config_dir() -> Path:
    return Path(user_config_dir(APP_NAME, appauthor=False))


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_list(value: Any, default: list[str] | None = None) -> list[str]:
    if value is None:
        return list(default or [])
    if isinstance(value, str):
        return [value] if value else []
    return [str(item) for item in value]


def _debug_from_env() -> bool | None:
    for name in ("BLOCKPROBE_DEBUG", "GS_DEBUG"):
        value = os.environ.get(name)
        if value is not None:
            return _as_bool(value)
    return None


@dataclass(slots=True)
class AppSection:
    debug: bool = False
    language: str = DEFAULT_LANGUAGE


@dataclass(slots=True)
class StorageSection:
    backend: str = "sqlite"
    path: str = "auto"


@dataclass(slots=True)
class CacheSection:
    backend: str = "embedded"
    ttl_seconds: int = 300
    size_limit_mb: int = 128


@dataclass(slots=True)
class PortalsSection:
    auto_refresh_on_run: bool = True
    refresh_ttl_hours: int = 24
    refresh_retry_cooldown_minutes: int = 60
    rate_limit_per_sec: float = 1.0
    max_pages: int = 50


@dataclass(slots=True)
class SafetySection:
    reset_confirm_word: str = "RESET"
    reset_all_confirm_word: str = "RESET ALL"
    reset_cooldown_seconds: int = 10
    reset_backup_required: bool = True


@dataclass(slots=True)
class ProbeSection:
    concurrency: int = 20
    timeout_seconds: int = 10
    connect_timeout_seconds: int = 6
    retries: int = 2
    max_redirects: int = 5
    body_cap_bytes: int = 262_144
    variants: list[str] = field(default_factory=lambda: ["as_listed", "apex", "www"])
    control_url: str = DEFAULT_CONTROL_URL
    control_urls: list[str] = field(default_factory=lambda: list(DEFAULT_CONTROL_URLS))
    control_every: int = 10
    use_control_resolver: bool = False
    control_resolver_url: str = "https://cloudflare-dns.com/dns-query"


@dataclass(slots=True)
class AppConfig:
    app: AppSection = field(default_factory=AppSection)
    storage: StorageSection = field(default_factory=StorageSection)
    cache: CacheSection = field(default_factory=CacheSection)
    portals: PortalsSection = field(default_factory=PortalsSection)
    safety: SafetySection = field(default_factory=SafetySection)
    probe: ProbeSection = field(default_factory=ProbeSection)
    source_path: Path | None = None

    @property
    def debug(self) -> bool:
        return bool(self.app.debug)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("source_path", None)
        return data


def is_mobile() -> bool:
    """Best-effort mobile detection for default concurrency and UI wording."""
    platform = sys.platform.lower()
    if platform.startswith(("android", "ios", "iphone")):
        return True
    if os.environ.get("ANDROID_ROOT") or os.environ.get("ANDROID_DATA"):
        return True
    return bool(os.environ.get("FLET_MOBILE"))


def project_root() -> Path | None:
    """Return the source checkout root when BlockProbe runs from a repo."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").is_file() and (parent / "portals").is_dir():
            return parent
    cwd = Path.cwd()
    if (cwd / "pyproject.toml").is_file() and (cwd / "portals").is_dir():
        return cwd
    return None


@dataclass(slots=True)
class Settings:
    """Resolved configuration plus concrete filesystem paths."""

    config: AppConfig
    config_dir: Path
    config_path: Path
    data_dir: Path
    db_path: Path
    cache_dir: Path
    logs_dir: Path
    backups_dir: Path
    reports_dir: Path
    signatures_dir: Path
    user_portals_dir: Path
    project_root: Path | None = None

    @property
    def portal_dirs(self) -> list[Path]:
        dirs: list[Path] = []
        if self.project_root is not None:
            dirs.append(self.project_root / "portals")
        dirs.append(self.user_portals_dir)
        return [path for path in dirs if path.exists()]

    @property
    def signature_dirs(self) -> list[Path]:
        dirs: list[Path] = []
        if self.project_root is not None:
            dirs.append(self.project_root / "signatures")
        dirs.append(self.config_dir / "signatures")
        dirs.append(self.signatures_dir)
        return [path for path in dirs if path.exists()]

    @property
    def primary_portal_dir(self) -> Path:
        return self.user_portals_dir

    def ensure_directories(self) -> None:
        for path in (
            self.data_dir,
            self.logs_dir,
            self.backups_dir,
            self.reports_dir,
            self.signatures_dir,
            self.user_portals_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def save(self) -> Path:
        """Write the current configuration back to ``config.toml``."""
        try:
            self.config_dir.mkdir(parents=True, exist_ok=True)
            self.config_path.write_text(render_config(self.config), encoding="utf-8")
        except OSError as exc:  # pragma: no cover - depends on the host filesystem
            raise ConfigError(f"Could not write {self.config_path}: {exc}") from exc
        return self.config_path


def _valid_control_urls(values: list[str], fallback: list[str]) -> list[str]:
    cleaned = [
        value.strip()
        for value in values
        if value.strip().startswith(("http://", "https://"))
    ]
    return list(dict.fromkeys(cleaned)) or list(fallback)


def _control_urls(probe: dict[str, Any], defaults: ProbeSection) -> list[str]:
    """Backward-compatible control URL list.

    A config that only sets ``control_url`` keeps using exactly that URL. With
    no explicit override, the interleaved neutral controls include Google,
    Wikipedia and Cloudflare so a false positive can be spotted in the report.
    """
    if "control_urls" in probe:
        return _valid_control_urls(_as_list(probe.get("control_urls")), defaults.control_urls)
    if "control_url" in probe:
        return _valid_control_urls([str(probe.get("control_url") or "")], defaults.control_urls)
    return list(defaults.control_urls)


def _merge_config(data: dict[str, Any], path: Path | None = None) -> AppConfig:
    app = data.get("app") or {}
    storage = data.get("storage") or {}
    cache = data.get("cache") or {}
    portals = data.get("portals") or {}
    safety = data.get("safety") or {}
    probe = data.get("probe") or {}
    sections = (app, storage, cache, portals, safety, probe)
    if not all(isinstance(section, dict) for section in sections):
        raise ConfigError(f"{path or 'config.toml'}: each section must be a TOML table")

    config = AppConfig(source_path=path)
    language = str(app.get("language") or config.app.language).lower()
    if language not in LANGUAGES:
        raise ConfigError(
            f"{path or 'config.toml'}: the language {language!r} is not supported. "
            f"Choose one of: {', '.join(LANGUAGES)}."
        )
    config.app = AppSection(
        debug=_as_bool(app.get("debug"), config.app.debug),
        language=language,
    )
    storage_backend = str(storage.get("backend") or config.storage.backend).lower()
    if storage_backend != "sqlite":
        raise ConfigError(
            f"{path or 'config.toml'}: the {storage_backend!r} storage backend is not "
            "available. BlockProbe stores everything in its local SQLite database; "
            "set [storage] backend = 'sqlite' or remove the key."
        )
    cache_backend = str(cache.get("backend") or config.cache.backend).lower()
    if cache_backend != "embedded":
        raise ConfigError(
            f"{path or 'config.toml'}: the {cache_backend!r} cache backend is not "
            "available. BlockProbe uses the embedded in-process cache; "
            "set [cache] backend = 'embedded' or remove the key."
        )
    config.storage = StorageSection(
        backend=storage_backend,
        path=str(storage.get("path") or config.storage.path),
    )
    config.cache = CacheSection(
        backend=cache_backend,
        ttl_seconds=_as_int(cache.get("ttl_seconds"), config.cache.ttl_seconds),
        size_limit_mb=_as_int(cache.get("size_limit_mb"), config.cache.size_limit_mb),
    )
    config.portals = PortalsSection(
        auto_refresh_on_run=_as_bool(
            portals.get("auto_refresh_on_run"), config.portals.auto_refresh_on_run
        ),
        refresh_ttl_hours=_as_int(
            portals.get("refresh_ttl_hours"), config.portals.refresh_ttl_hours
        ),
        refresh_retry_cooldown_minutes=_as_int(
            portals.get("refresh_retry_cooldown_minutes"),
            config.portals.refresh_retry_cooldown_minutes,
        ),
        rate_limit_per_sec=_as_float(
            portals.get("rate_limit_per_sec"), config.portals.rate_limit_per_sec
        ),
        max_pages=_as_int(portals.get("max_pages"), config.portals.max_pages),
    )
    config.safety = SafetySection(
        reset_confirm_word=str(safety.get("reset_confirm_word") or config.safety.reset_confirm_word),
        reset_all_confirm_word=str(
            safety.get("reset_all_confirm_word") or config.safety.reset_all_confirm_word
        ),
        reset_cooldown_seconds=_as_int(
            safety.get("reset_cooldown_seconds"), config.safety.reset_cooldown_seconds
        ),
        reset_backup_required=_as_bool(
            safety.get("reset_backup_required"), config.safety.reset_backup_required
        ),
    )
    config.probe = ProbeSection(
        concurrency=max(1, _as_int(probe.get("concurrency"), config.probe.concurrency)),
        timeout_seconds=max(1, _as_int(probe.get("timeout_seconds"), config.probe.timeout_seconds)),
        connect_timeout_seconds=max(
            1,
            _as_int(
                probe.get("connect_timeout_seconds"), config.probe.connect_timeout_seconds
            ),
        ),
        retries=max(0, _as_int(probe.get("retries"), config.probe.retries)),
        max_redirects=max(0, _as_int(probe.get("max_redirects"), config.probe.max_redirects)),
        body_cap_bytes=max(
            4096, _as_int(probe.get("body_cap_bytes"), config.probe.body_cap_bytes)
        ),
        variants=_as_list(probe.get("variants"), list(config.probe.variants)),
        control_url=str(probe.get("control_url") or config.probe.control_url),
        control_urls=_control_urls(probe, config.probe),
        control_every=max(1, _as_int(probe.get("control_every"), config.probe.control_every)),
        use_control_resolver=_as_bool(
            probe.get("use_control_resolver"), config.probe.use_control_resolver
        ),
        control_resolver_url=str(
            probe.get("control_resolver_url") or config.probe.control_resolver_url
        ),
    )
    env_debug = _debug_from_env()
    if env_debug is not None:
        config.app.debug = env_debug
    if is_mobile():
        config.probe.concurrency = min(config.probe.concurrency, 8)
    return config


def load_config(path: str | Path | None = None) -> Settings:
    """Load configuration and resolve all filesystem paths.

    A missing config file is valid and produces the documented defaults. A
    malformed file raises :class:`ConfigError` instead of silently ignoring it.
    """
    explicit = Path(path) if path else None
    if explicit is None and os.environ.get("BLOCKPROBE_CONFIG"):
        explicit = Path(os.environ["BLOCKPROBE_CONFIG"])

    config_dir = explicit.parent if explicit else default_config_dir()
    config_path = explicit or (config_dir / "config.toml")
    if config_path.exists():
        try:
            with config_path.open("rb") as handle:
                raw = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError(f"Could not read {config_path}: {exc}") from exc
        config = _merge_config(raw, config_path)
    else:
        config = _merge_config({}, None)

    data_dir = default_data_dir()
    env_data = os.environ.get("BLOCKPROBE_DATA_DIR")
    if env_data:
        data_dir = Path(env_data).expanduser()
    elif config.storage.path and config.storage.path != "auto":
        data_dir = Path(config.storage.path).expanduser()

    return Settings(
        config=config,
        config_dir=config_dir,
        config_path=config_path,
        data_dir=data_dir,
        db_path=data_dir / "blockprobe.sqlite3",
        cache_dir=data_dir / "cache",
        logs_dir=data_dir / "logs",
        backups_dir=data_dir / "backups",
        reports_dir=data_dir / "reports",
        signatures_dir=data_dir / "signatures",
        user_portals_dir=config_dir / "portals",
        project_root=project_root(),
    )


def render_config(config: AppConfig) -> str:
    """Serialize the known configuration sections to TOML.

    The project deliberately does not depend on a TOML writer package. Only the
    documented keys are written, so unknown user comments are not preserved.
    """
    lines: list[str] = [
        "# SPDX-License-Identifier: AGPL-3.0-or-later",
        "# Purpose: BlockProbe configuration. Generated from the in-app settings.",
        "",
        "[app]",
        f"debug = {str(config.app.debug).lower()}",
        f"language = {config.app.language!r}",
        "",
        "[storage]",
        f"backend = {config.storage.backend!r}",
        f"path = {config.storage.path!r}",
        "",
        "[cache]",
        f"backend = {config.cache.backend!r}",
        f"ttl_seconds = {config.cache.ttl_seconds}",
        f"size_limit_mb = {config.cache.size_limit_mb}",
        "",
        "[portals]",
        f"auto_refresh_on_run = {str(config.portals.auto_refresh_on_run).lower()}",
        f"refresh_ttl_hours = {config.portals.refresh_ttl_hours}",
        f"refresh_retry_cooldown_minutes = {config.portals.refresh_retry_cooldown_minutes}",
        f"rate_limit_per_sec = {config.portals.rate_limit_per_sec:g}",
        f"max_pages = {config.portals.max_pages}",
        "",
        "[safety]",
        f"reset_confirm_word = {config.safety.reset_confirm_word!r}",
        f"reset_all_confirm_word = {config.safety.reset_all_confirm_word!r}",
        f"reset_cooldown_seconds = {config.safety.reset_cooldown_seconds}",
        f"reset_backup_required = {str(config.safety.reset_backup_required).lower()}",
        "",
        "[probe]",
        f"concurrency = {config.probe.concurrency}",
        f"timeout_seconds = {config.probe.timeout_seconds}",
        f"connect_timeout_seconds = {config.probe.connect_timeout_seconds}",
        f"retries = {config.probe.retries}",
        f"max_redirects = {config.probe.max_redirects}",
        f"body_cap_bytes = {config.probe.body_cap_bytes}",
        f"variants = {config.probe.variants!r}",
        f"control_url = {config.probe.control_url!r}",
        f"control_urls = {config.probe.control_urls!r}",
        f"control_every = {config.probe.control_every}",
        f"use_control_resolver = {str(config.probe.use_control_resolver).lower()}",
        f"control_resolver_url = {config.probe.control_resolver_url!r}",
        "",
        f"# BlockProbe {__version__}",
        "",
    ]
    return "\n".join(lines)
