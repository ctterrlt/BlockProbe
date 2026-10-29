# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared enums and user-facing labels.

The values stored in SQLite are stable, lower-case strings. Keeping them in one
place makes the database, CLI, reports and Flet UI agree about what a verdict
means.
"""

from __future__ import annotations

from enum import StrEnum


class Verdict(StrEnum):
    """The three verdicts shown to users."""

    BLOCKED = "blocked"
    UNREACHABLE = "unreachable"
    OPENED = "opened"

    @property
    def label(self) -> str:
        return {
            Verdict.BLOCKED: "Blocked by a blocker",
            Verdict.UNREACHABLE: "Destination unreachable",
            Verdict.OPENED: "Website opened successfully",
        }[self]

    @property
    def short_label(self) -> str:
        return {
            Verdict.BLOCKED: "Blocked",
            Verdict.UNREACHABLE: "Unreachable",
            Verdict.OPENED: "Opened",
        }[self]


class Confidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @property
    def rank(self) -> int:
        return {Confidence.LOW: 1, Confidence.MEDIUM: 2, Confidence.HIGH: 3}[self]

    @property
    def label(self) -> str:
        return self.value.capitalize()


class BlockerHint(StrEnum):
    BETBLOCKER = "betblocker"
    GAMBAN = "gamban"
    DNS_FILTER = "dns_filter"
    ISP_ADM = "isp_adm"
    UNKNOWN = "unknown"

    @property
    def label(self) -> str:
        return {
            BlockerHint.BETBLOCKER: "BetBlocker",
            BlockerHint.GAMBAN: "Gamban",
            BlockerHint.DNS_FILTER: "DNS filter",
            BlockerHint.ISP_ADM: "Italian ISP / ADM blackout",
            BlockerHint.UNKNOWN: "Unknown blocker",
        }[self]


class Origin(StrEnum):
    """Where a candidate hostname was found in the portal page."""

    TABLE_TEXT = "table_text"
    TEXT = "text"
    ANCHOR = "anchor"
    SCRIPT = "script"
    FORM = "form"
    CUSTOM = "custom"

    @property
    def label(self) -> str:
        return self.value.replace("_", " ").capitalize()


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class RefreshStatus(StrEnum):
    OK = "ok"
    EMPTY = "empty"
    ERROR = "error"
    SKIPPED = "skipped"
    COOLDOWN = "cooldown"


class SignatureKind(StrEnum):
    BLOCK = "block"
    DEAD = "dead"
    POSITIVE = "positive"
    CHALLENGE = "challenge"


class ReasonCode(StrEnum):
    DNS_SINKHOLE = "dns_sinkhole"
    DNS_FAILURE = "dns_failure"
    DNS_FILTERED = "dns_filtered"
    CONNECT_REFUSED = "connect_refused"
    CONNECT_RESET = "connect_reset"
    CONNECT_TIMEOUT = "connect_timeout"
    TLS_FAILURE = "tls_failure"
    CONNECT_FAILURE = "connect_failure"
    REGULATOR_REDIRECT = "regulator_redirect"
    BLOCK_SIGNATURE = "block_signature"
    BLOCKER_PAGE = "blocker_page"
    BLOCKER_REDIRECT = "blocker_redirect"
    ISP_BLACKOUT = "isp_blackout"
    CHALLENGE_PAGE = "challenge_page"
    POSITIVE_MARKERS = "positive_markers"
    DEAD_PAGE = "dead_page"
    EMPTY_PAGE = "empty_page"
    HTTP_ERROR = "http_error"
    UNCLASSIFIED = "unclassified"
    NETWORK_DOWN = "network_down"

    @property
    def label(self) -> str:
        return {
            ReasonCode.DNS_SINKHOLE: "DNS sinkhole or private address",
            ReasonCode.DNS_FAILURE: "DNS lookup failed",
            ReasonCode.DNS_FILTERED: "Filtered DNS answer",
            ReasonCode.CONNECT_REFUSED: "Connection refused",
            ReasonCode.CONNECT_RESET: "Connection reset",
            ReasonCode.CONNECT_TIMEOUT: "Connection timed out",
            ReasonCode.TLS_FAILURE: "TLS handshake failed",
            ReasonCode.CONNECT_FAILURE: "Connection failed",
            ReasonCode.REGULATOR_REDIRECT: "Redirected to a regulator page",
            ReasonCode.BLOCK_SIGNATURE: "Matched a block-page fingerprint",
            ReasonCode.BLOCKER_PAGE: "Generic block or denied page",
            ReasonCode.BLOCKER_REDIRECT: "Redirected or opened a blocker page",
            ReasonCode.ISP_BLACKOUT: "Italian ISP/ADM blackout",
            ReasonCode.CHALLENGE_PAGE: "Anti-bot challenge (site reachable)",
            ReasonCode.POSITIVE_MARKERS: "Gambling content markers found",
            ReasonCode.DEAD_PAGE: "Domain is dead, parked or unavailable",
            ReasonCode.EMPTY_PAGE: "Empty response body",
            ReasonCode.HTTP_ERROR: "HTTP error response",
            ReasonCode.UNCLASSIFIED: "No classification rule matched",
            ReasonCode.NETWORK_DOWN: "The network connectivity guard failed",
        }[self]


class SourceKind(StrEnum):
    PORTAL = "portal"
    CUSTOM = "custom"

    @property
    def label(self) -> str:
        return self.value.capitalize()
