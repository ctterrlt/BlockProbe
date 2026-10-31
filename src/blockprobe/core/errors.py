# SPDX-License-Identifier: AGPL-3.0-or-later
"""Typed errors used across BlockProbe.

Every user-facing layer catches these and turns them into a clear message. No
error message may contain a fabricated verdict or an invented block signature.
"""

from __future__ import annotations


class BlockProbeError(Exception):
    """Base class for all recoverable BlockProbe errors."""


class ConfigError(BlockProbeError):
    """The configuration file is missing, malformed or contradictory."""


class ValidationError(BlockProbeError):
    """User input is not a valid domain, portal or setting."""


class PortalError(BlockProbeError):
    """A portal definition cannot be loaded or used."""


class FetchError(PortalError):
    """A portal page could not be fetched."""


class StorageError(BlockProbeError):
    """The storage backend rejected an operation."""


class CacheError(BlockProbeError):
    """The cache backend rejected an operation."""


class ProbeError(BlockProbeError):
    """The probe engine could not complete an operation."""


class ConsentRequired(BlockProbeError):
    """The first-run consent flow has not been accepted yet."""


class ResetRefused(BlockProbeError):
    """A guarded reset was not confirmed correctly."""
