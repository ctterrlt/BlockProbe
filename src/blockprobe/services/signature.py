# SPDX-License-Identifier: AGPL-3.0-or-later
"""Capture response fingerprints for later manual review (FR-12).

Capture is evidence collection, not signature activation. The saved TOML file
contains a reviewable fingerprint and a commented-out rule template. Nothing in
this module changes how live scans classify pages, so an unreviewed fingerprint
can never invent a block verdict.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from blockprobe.core.config import Settings
from blockprobe.core.errors import ValidationError
from blockprobe.core.models import CapturedSignature, utc_now
from blockprobe.portals.domain_normalizer import DomainRejected, normalize_domain
from blockprobe.probe.classifier import sniff_key_phrases
from blockprobe.probe.dns import resolve
from blockprobe.probe.http_fetch import fetch
from blockprobe.storage.base import Storage

_LOGGER = logging.getLogger("blockprobe.services.signature")


@dataclass(slots=True)
class CaptureResult:
    signature: CapturedSignature
    path: Path
    message: str


class SignatureService:
    def __init__(self, settings: Settings, storage: Storage) -> None:
        self.settings = settings
        self.storage = storage

    async def capture_async(self, value: str) -> CaptureResult:
        try:
            normalized = normalize_domain(value)
        except DomainRejected as exc:
            raise ValidationError(f"'{value}' is not a usable domain: {exc}") from exc
        host = normalized.host
        dns_result = await resolve(host, timeout=float(self.settings.config.probe.timeout_seconds))
        fetch_result = await fetch(
            f"https://{host}/",
            timeout=float(self.settings.config.probe.timeout_seconds),
            max_redirects=self.settings.config.probe.max_redirects,
            max_bytes=self.settings.config.probe.body_cap_bytes,
        )
        if fetch_result.status is None:
            # https failed before a response; try http once so the fingerprint
            # can still document what the network returns.
            fetch_result = await fetch(
                f"http://{host}/",
                timeout=float(self.settings.config.probe.timeout_seconds),
                max_redirects=self.settings.config.probe.max_redirects,
                max_bytes=self.settings.config.probe.body_cap_bytes,
            )
        signature = CapturedSignature(
            domain=normalized.domain,
            captured_at=utc_now(),
            status=fetch_result.status,
            title=fetch_result.title,
            final_url=fetch_result.final_url,
            redirect_chain=list(fetch_result.redirect_chain),
            headers=dict(fetch_result.headers),
            ips=list(dns_result.ips),
            body_sha256=fetch_result.body_sha256,
            key_phrases=sniff_key_phrases(fetch_result.body),
            note=(
                "Captured locally. Review the fingerprint, reproduce the page "
                "yourself and only then propose a rule."
            ),
        )
        path = self.storage.save_signature(signature)
        _LOGGER.info(
            "signature captured",
            extra={"event": "signature_capture", "domain": normalized.domain},
        )
        return CaptureResult(
            signature=signature,
            path=path,
            message=(
                f"Fingerprint for {normalized.domain} saved to {path}. "
                "It is evidence only: no signature was activated."
            ),
        )

    def capture(self, value: str) -> CaptureResult:
        return asyncio.run(self.capture_async(value))

    def list_captured(self) -> list[Path]:
        directory = self.settings.signatures_dir
        if not directory.exists():
            return []
        return sorted(directory.glob("*.toml"))

    def active_rule_count(self) -> int:
        """Count verified rules loaded from any signature directory."""
        from blockprobe.probe.classifier import load_signatures

        return len(load_signatures(self.settings.signature_dirs))
