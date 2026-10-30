# SPDX-License-Identifier: AGPL-3.0-or-later
"""Portal definition loader and writer.

Portal TOML files are read from the source checkout, the per-user config
directory and the data directory. The bundled ADM definition is always present
even when BlockProbe is installed from a wheel, so a fresh install can refresh
without copying repository files by hand.
"""

from __future__ import annotations

import logging
import re
import tomllib
import uuid
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit

from blockprobe.core.errors import PortalError, ValidationError
from blockprobe.core.models import Portal, builtin_adm_portal, builtin_regulator_portals
from blockprobe.portals.domain_normalizer import DomainRejected, normalize_domain

_LOGGER = logging.getLogger("blockprobe.portals.loader")
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def load_portal_file(path: Path) -> Portal:
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise PortalError(f"{path.name}: {exc}") from exc
    portal = Portal.from_toml(data, source_path=path)
    if not portal.start_url:
        raise PortalError(f"{path.name}: missing [source].start_url")
    parts = urlsplit(portal.start_url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise PortalError(f"{path.name}: start_url must be an http(s) URL")
    return portal


def load_portals(
    directories: list[Path] | None = None,
    *,
    include_disabled: bool = True,
) -> list[Portal]:
    """Load every portal definition, always including the bundled ADM portal."""
    portals: dict[str, Portal] = {}
    for directory in directories or []:
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*.toml")):
            try:
                portal = load_portal_file(path)
            except PortalError as exc:
                _LOGGER.warning("skipping portal definition", extra={"event": "portal_skip"})
                _LOGGER.debug("%s", exc)
                continue
            portals.setdefault(portal.uuid, portal)

    for builtin in [builtin_adm_portal(), *builtin_regulator_portals()]:
        if builtin.uuid not in portals and not any(
            portal.start_url == builtin.start_url for portal in portals.values()
        ):
            portals[builtin.uuid] = builtin

    values = list(portals.values())
    if not include_disabled:
        values = [portal for portal in values if portal.enabled]
    return sorted(values, key=lambda portal: (not portal.default, portal.name.lower()))


def find_portal(portals: list[Portal], identifier: str) -> Portal | None:
    wanted = (identifier or "").strip().lower()
    if not wanted:
        return None
    for portal in portals:
        if wanted in {portal.uuid.lower(), portal.slug.lower(), portal.name.lower()}:
            return portal
    # Prefix match on the UUID is convenient in the CLI.
    matches = [portal for portal in portals if portal.uuid.lower().startswith(wanted)]
    if len(matches) == 1:
        return matches[0]
    return None


def slugify(name: str) -> str:
    slug = _SLUG_RE.sub("-", (name or "portal").lower()).strip("-")
    return slug[:60] or "portal"


def save_portal(portal: Portal, directory: Path) -> Path:
    """Write a user-added portal definition, never overwriting an existing file."""
    if not portal.start_url:
        raise ValidationError("A portal needs a start URL.")
    parts = urlsplit(portal.start_url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValidationError("The portal start URL must be a valid http(s) URL.")
    try:
        normalize_domain(portal.start_url)
    except DomainRejected as exc:
        raise ValidationError(f"The portal start URL is not a public domain: {exc}") from exc

    directory.mkdir(parents=True, exist_ok=True)
    slug = slugify(portal.slug if portal.slug != portal.uuid else portal.name)
    path = directory / f"{slug}.toml"
    counter = 2
    while path.exists():
        path = directory / f"{slug}-{counter}.toml"
        counter += 1
    portal = replace(portal, source_path=path)
    path.write_text(portal.to_toml(), encoding="utf-8")
    _LOGGER.info("portal definition saved", extra={"event": "portal_saved"})
    return path


def ensure_portal_uuid(value: str | None = None) -> str:
    if value:
        try:
            return str(uuid.UUID(value))
        except ValueError as exc:
            raise ValidationError("The portal UUID is not valid.") from exc
    return str(uuid.uuid4())
