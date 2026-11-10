# SPDX-License-Identifier: AGPL-3.0-or-later
"""Required Rust scan primitives exposed through a small C ABI."""

from __future__ import annotations

import ctypes
import os
import platform
import sys
import sysconfig
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from blockprobe.core.models import Classification

_ABI_VERSION = 2


class _AggregateInput(ctypes.Structure):
    """Fixed-width row matching the Rust `#[repr(C)]` input type."""

    _fields_ = [
        ("verdict", ctypes.c_uint8),
        ("confidence", ctypes.c_uint8),
        ("blocker_hint", ctypes.c_uint8),
        ("confident_opened", ctypes.c_uint8),
    ]


def _check_abi_version(actual: int) -> None:
    if actual != _ABI_VERSION:
        raise RuntimeError(
            "The BlockProbe Rust scan engine uses an unsupported ABI "
            f"version {actual}; expected {_ABI_VERSION}."
        )


def _library_names() -> tuple[str, ...]:
    if sys.platform == "win32":
        return ("blockprobe_core.dll",)
    if sys.platform == "darwin":
        return ("libblockprobe_core.dylib",)
    return ("libblockprobe_core.so",)


def _library_candidates() -> list[Path]:
    package = Path(__file__).resolve().parents[1]
    root = package.parents[1]
    names = _library_names()
    candidates = []
    if os.environ.get("ANDROID_ARGUMENT") or os.environ.get("ANDROID_ROOT"):
        abi = {
            "aarch64": "arm64-v8a",
            "arm64": "arm64-v8a",
            "armv7l": "armeabi-v7a",
            "x86_64": "x86_64",
        }.get(platform.machine().lower())
        if abi:
            candidates.extend(package / "_native" / "android" / abi / name for name in names)
    if sys.platform == "ios":
        arch = platform.machine().lower()
        if arch in {"arm64", "aarch64"}:
            arch = "arm64"
        elif arch in {"x86_64", "amd64"}:
            arch = "x86_64"
        candidates.extend(
            package / "_native" / "ios-simulator" / arch / name for name in names
        )
    if sys.platform == "emscripten":
        platform_tag = sysconfig.get_platform().replace("_", "-")
        tags = dict.fromkeys(
            (
                platform_tag,
                platform_tag.removeprefix("pyemscripten-"),
                "pyemscripten-2026.0-wasm32",
            )
        )
        candidates.extend(
            package / "_native" / "emscripten" / tag / name
            for tag in tags
            for name in names
        )
    candidates.extend(package / "_native" / name for name in names)
    for profile in ("release", "debug"):
        candidates.extend(root / "target" / profile / name for name in names)
    override = os.environ.get("BLOCKPROBE_RUST_LIBRARY")
    if override:
        candidates.insert(0, Path(override).expanduser())
    return candidates


@lru_cache(maxsize=1)
def _library() -> ctypes.CDLL:
    for candidate in _library_candidates():
        if candidate.is_file():
            library = ctypes.CDLL(str(candidate))
            library.bp_abi_version.argtypes = []
            library.bp_abi_version.restype = ctypes.c_uint32
            _check_abi_version(library.bp_abi_version())
            library.bp_aggregate.argtypes = [
                ctypes.POINTER(_AggregateInput),
                ctypes.c_size_t,
                ctypes.POINTER(ctypes.c_uint8),
            ]
            library.bp_aggregate.restype = ctypes.c_ssize_t
            library.bp_system_resolve.argtypes = [
                ctypes.c_char_p,
                ctypes.c_void_p,
                ctypes.c_size_t,
            ]
            library.bp_system_resolve.restype = ctypes.c_ssize_t
            library.bp_is_sinkhole_ip.argtypes = [ctypes.c_char_p]
            library.bp_is_sinkhole_ip.restype = ctypes.c_ssize_t
            library.bp_regex_set_new.argtypes = [
                ctypes.POINTER(ctypes.c_char_p),
                ctypes.c_size_t,
            ]
            library.bp_regex_set_new.restype = ctypes.c_void_p
            library.bp_regex_set_matches.argtypes = [
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_uint8),
                ctypes.c_size_t,
                ctypes.POINTER(ctypes.c_size_t),
                ctypes.c_size_t,
            ]
            library.bp_regex_set_matches.restype = ctypes.c_ssize_t
            return library
    attempted = ", ".join(str(path) for path in _library_candidates())
    raise RuntimeError(
        "The BlockProbe Rust scan engine is missing. Build it with "
        "`python scripts/build_rust_engine.py`; looked in: "
        f"{attempted}"
    )


def _call_text(function: object, value: bytes, *, capacity: int = 16_384) -> str:
    buffer = ctypes.create_string_buffer(capacity)
    result = function(value, buffer, capacity)  # type: ignore[operator]
    if result < 0:
        raise OSError("Rust scan engine operation failed")
    return buffer.value.decode("utf-8")


def aggregate_indices(classifications: list[Classification]) -> tuple[int, bool, list[int]]:
    """Select the aggregate verdict in Rust; Python retains evidence assembly."""
    if not classifications:
        return -1, False, []
    verdict_codes = {"blocked": 0, "unreachable": 1, "opened": 2}
    confidence_codes = {"low": 0, "medium": 1, "high": 2}
    rows = (_AggregateInput * len(classifications))()
    for index, item in enumerate(classifications):
        try:
            verdict = verdict_codes[item.verdict.value]
            confidence = confidence_codes[item.confidence.value]
        except KeyError as exc:
            raise ValueError(f"Unsupported Rust aggregate field: {exc.args[0]}") from exc
        rows[index] = _AggregateInput(
            verdict=verdict,
            confidence=confidence,
            blocker_hint=int(bool(item.blocker_hint)),
            confident_opened=int(
                item.verdict.value == "opened"
                and not item.needs_review
                and item.reason_code not in {"empty_page", "unclassified"}
            ),
        )
    conflict_flag = ctypes.c_uint8()
    chosen = _library().bp_aggregate(rows, len(rows), ctypes.byref(conflict_flag))
    if not 0 <= chosen < len(classifications):
        raise RuntimeError("Rust scan engine rejected aggregate input")
    conflict = bool(conflict_flag.value)
    uncertain = (
        [
            index
            for index, item in enumerate(classifications)
            if item.verdict.value == "opened"
            and (
                item.needs_review
                or item.reason_code in {"empty_page", "unclassified"}
            )
        ]
        if conflict
        else []
    )
    return chosen, conflict, uncertain


def system_resolve(host: str) -> list[str] | None:
    """Resolve through the platform resolver from the Rust worker thread."""
    try:
        output = _call_text(_library().bp_system_resolve, host.encode("idna"))
    except (OSError, UnicodeError):
        return None
    addresses = list(dict.fromkeys(line.strip() for line in output.splitlines() if line.strip()))
    return addresses or None


def is_sinkhole_ip(value: str) -> bool:
    """Return Rust's deterministic sinkhole classification for an IP literal."""
    try:
        return _library().bp_is_sinkhole_ip(value.encode("ascii")) == 1
    except (OSError, UnicodeError):
        return False


@lru_cache(maxsize=16)
def _compile_pattern_set(patterns: tuple[str, ...]) -> int | None:
    """Compile one fixed marker group once, preserving Python ownership."""
    if not patterns:
        return None
    try:
        encoded = [pattern.encode("utf-8") for pattern in patterns]
    except UnicodeError:
        return None
    pointers = (ctypes.c_char_p * len(encoded))(*encoded)
    handle = _library().bp_regex_set_new(pointers, len(encoded))
    return int(handle) if handle else None


def match_pattern_indices(patterns: tuple[str, ...], text: str) -> tuple[int, ...] | None:
    """Return Rust-matched pattern indices in source order.

    `None` asks the caller to use Python's reference matcher. This is used
    when a pattern set contains Python syntax unsupported by Rust's regex
    engine or the input cannot be encoded as UTF-8.
    """
    if not patterns or not text:
        return ()
    try:
        payload = text.encode("utf-8")
    except UnicodeError:
        return None
    handle = _compile_pattern_set(patterns)
    if handle is None:
        return None
    text_buffer = ctypes.create_string_buffer(payload)
    matched_indices = (ctypes.c_size_t * len(patterns))()
    count = _library().bp_regex_set_matches(
        handle,
        ctypes.cast(text_buffer, ctypes.POINTER(ctypes.c_uint8)),
        len(payload),
        matched_indices,
        len(patterns),
    )
    if count < 0:
        return None
    if count > len(patterns):
        raise RuntimeError("Rust regex matcher returned an invalid match count")
    return tuple(matched_indices[index] for index in range(count))
