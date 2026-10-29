# SPDX-License-Identifier: AGPL-3.0-or-later
"""Translation catalogs: default, lookup, fallback and coverage.

Coverage is enforced mechanically: every ``t("...")`` literal used in the
source must exist in every catalog, and the consent texts (passed to ``t()``
dynamically) must be translated too. A future string that forgets its
translations fails here instead of silently shipping English.
"""

from __future__ import annotations

import ast
import string
from pathlib import Path

import pytest

from blockprobe import i18n

SRC = Path(__file__).resolve().parents[2] / "src"


@pytest.fixture(autouse=True)
def italian_default() -> None:
    i18n.set_language("it")
    yield
    i18n.set_language("it")


def test_default_language_is_italian() -> None:
    assert i18n.DEFAULT_LANGUAGE == "it"
    assert list(i18n.LANGUAGES) == ["it", "en", "fr", "es", "pt"]
    assert i18n.get_language() == "it"
    assert i18n.available_languages()["it"] == "Italiano"


def test_language_codes_are_normalized() -> None:
    assert i18n.set_language(" FR ") == "fr"
    assert i18n.get_language() == "fr"
    assert i18n.is_supported("PT") is True
    assert i18n.is_supported("de") is False


def test_unsupported_language_raises_with_the_supported_list() -> None:
    with pytest.raises(ValueError, match="it, en, fr, es, pt"):
        i18n.set_language("de")


def test_lookup_falls_back_to_the_english_source_string() -> None:
    assert i18n.t("Ready") == "Pronto"
    unknown = "A string with no translation yet"
    assert i18n.t(unknown) == unknown
    i18n.set_language("en")
    assert i18n.t("Ready") == "Ready"


def test_placeholders_are_filled() -> None:
    i18n.set_language("it")
    assert i18n.t("Checked: {count}", count=7) == "Controllati: 7"
    assert i18n.t("Blocked share: {percent}%", percent=42) == "Quota bloccati: 42%"


def test_bad_placeholders_fall_back_instead_of_crashing() -> None:
    rendered = i18n.t("Checked: {count}")
    assert rendered  # never raises: the raw template is the fallback


def test_catalogs_share_the_same_key_set() -> None:
    base = set(i18n._translations["it"])
    for code, catalog in i18n._translations.items():
        assert set(catalog) == base, f"{code} has a different key set"


def test_catalogs_have_no_empty_values_or_duplicate_keys() -> None:
    for code, catalog in i18n._translations.items():
        empty = [key for key, value in catalog.items() if not value.strip()]
        assert not empty, f"{code} has empty translations: {empty[:5]}"

    tree = ast.parse((SRC / "blockprobe/i18n.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        keys = [key.value for key in node.keys if isinstance(key, ast.Constant)]
        duplicates = sorted({key for key in keys if keys.count(key) > 1})
        assert not duplicates, f"duplicate translation keys: {duplicates[:5]}"


def test_translation_placeholders_match_source_keys() -> None:
    formatter = string.Formatter()
    for code, catalog in i18n._translations.items():
        for source, translated in catalog.items():
            expected = {name for _, name, _, _ in formatter.parse(source) if name}
            actual = {name for _, name, _, _ in formatter.parse(translated) if name}
            assert actual == expected, (
                f"{code} placeholder mismatch for {source!r}: "
                f"expected {sorted(expected)}, got {sorted(actual)}"
            )


def _used_literal_keys() -> set[str]:
    used: set[str] = set()
    for path in sorted((SRC / "blockprobe").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "t"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                used.add(node.args[0].value)
    return used


def test_every_code_literal_has_a_translation_in_every_catalog() -> None:
    used = _used_literal_keys()
    assert "Ready" in used and "Start scan" in used  # guard the extractor itself
    for code, catalog in i18n._translations.items():
        missing = sorted(key for key in used if key not in catalog)
        assert not missing, f"{code} is missing translations for: {missing[:5]}"


def test_consent_texts_are_translated_in_every_catalog() -> None:
    from blockprobe.ui.actions import CONSENT_TEXTS

    for code, catalog in i18n._translations.items():
        for title, body in CONSENT_TEXTS:
            assert title in catalog, f"{code} is missing the title {title!r}"
            assert body in catalog, f"{code} is missing a body for {title!r}"
