# SPDX-License-Identifier: AGPL-3.0-or-later
"""Scope, privacy and compliance facts shown in reports and diagnostics.

These statements describe what the application does not ask for and does not
send. They are deliberately static: they are a transparency statement, not a
legal certification, and they must stay accurate when the probe changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ScopeFact:
    """One machine-readable transparency fact."""

    key: str
    title: str
    status: str
    statement: str

    def to_dict(self) -> dict[str, str]:
        return {
            "key": self.key,
            "title": self.title,
            "status": self.status,
            "statement": self.statement,
        }


SCOPE_FACTS: tuple[ScopeFact, ...] = (
    ScopeFact(
        key="age_verification",
        title="Age verification",
        status="not asked",
        statement=(
            "BlockProbe does not ask for or verify age. Results are not an "
            "age-assurance control, and age-related gambling rules are outside "
            "the tool's scope."
        ),
    ),
    ScopeFact(
        key="cookies",
        title="Cookies",
        status="not asked, not stored, not replayed",
        statement=(
            "No cookie banner is accepted and no cookie jar is kept or replayed. "
            "The probe is not a browser and does not act on consent dialogs."
        ),
    ),
    ScopeFact(
        key="data_transfer",
        title="Data sent",
        status="none uploaded",
        statement=(
            "No scan result, report or personal data is uploaded to BlockProbe or "
            "to an analytics service. Results stay in the local data directory. "
            "Only the outbound HTTP requests described below leave the device."
        ),
    ),
    ScopeFact(
        key="website_terms",
        title="Website terms",
        status="not accepted",
        statement=(
            "No terms of service, age gate, cookie wall or login of a probed "
            "website is accepted on the user's behalf."
        ),
    ),
    ScopeFact(
        key="images_and_downloads",
        title="Images and downloads",
        status="not fetched, not sent",
        statement=(
            "Images, media, fonts, scripts and files are not downloaded, and no "
            "image, file, form value or archive is uploaded. Only bounded HTML "
            "responses are fetched for classification."
        ),
    ),
    ScopeFact(
        key="legal_frameworks",
        title="Legal frameworks",
        status="not applied or claimed",
        statement=(
            "BlockProbe does not apply or claim compliance with the GDPR, US "
            "state privacy laws or any other jurisdiction-specific legal "
            "framework. This is a technical statement, not legal advice."
        ),
    ),
)


def scope_payload() -> dict[str, Any]:
    """Return the facts as a JSON-ready mapping."""
    return {fact.key: fact.to_dict() for fact in SCOPE_FACTS}


def scope_lines() -> list[str]:
    """Return the facts as plain-text lines for CLI and Markdown output."""
    return [f"- {fact.title}: **{fact.status}** — {fact.statement}" for fact in SCOPE_FACTS]


def scope_summary() -> str:
    """One-line summary used in scan results and the UI."""
    return (
        "Scope: no age verification, no cookies, no uploads, no website terms, "
        "no images, no GDPR/US-law claim."
    )
