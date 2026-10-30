# SPDX-License-Identifier: AGPL-3.0-or-later
"""F-Droid metadata rendering, validation and merge behaviour."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from scripts import build, fdroid_submit


def rendered() -> dict[str, Any]:
    version = fdroid_submit.project_version()
    return fdroid_submit.render(
        fdroid_submit.DEFAULT_METADATA,
        version,
        fdroid_submit.version_code(version),
        "v-test",
    )


def test_version_code_matches_the_release_build_number() -> None:
    version = fdroid_submit.project_version()

    assert fdroid_submit.version_code(version) == build.project_build_number()


def test_render_uses_the_release_tag_and_current_version() -> None:
    version = fdroid_submit.project_version()
    code = fdroid_submit.version_code(version)

    data = fdroid_submit.render(fdroid_submit.DEFAULT_METADATA, version, code, "v-test")

    assert data["CurrentVersion"] == version
    assert data["CurrentVersionCode"] == code
    current = data["Builds"][-1]
    assert current["versionName"] == version
    assert current["versionCode"] == code
    assert current["commit"] == "v-test"
    assert current["ndk"] == "r28c"
    assert current["output"] == "build/flet/apk/blockprobe.apk"
    assert current["timeout"] == 14400
    assert current["sudo"] == ["apt-get update", "apt-get install -y python3-venv"]
    assert current["srclibs"] == ["flutter@stable"]
    assert current["prebuild"] == [
        "git -C $$flutter$$ checkout -f 3.44.8",
        "export PATH=$$flutter$$/bin:$PATH && bash fdroid/build.sh prepare",
    ]
    assert current["build"] == ["bash fdroid/build.sh build"]


def test_render_rejects_leftover_placeholders(tmp_path: Path) -> None:
    metadata = tmp_path / "metadata.yaml"
    metadata.write_text("Name: '@@UNKNOWN@@'\n", encoding="utf-8")

    with pytest.raises(SystemExit, match="Unrendered placeholders"):
        fdroid_submit.render(metadata, "1.0.0", 1_000_000, "v1.0.0")


def test_validate_accepts_the_canonical_metadata() -> None:
    data = rendered()

    fdroid_submit.validate(
        data,
        str(data["CurrentVersion"]),
        int(data["CurrentVersionCode"]),
    )


def test_validate_rejects_binary_downloads() -> None:
    data = rendered()
    data["Binaries"] = "https://example.com/app.apk"

    with pytest.raises(SystemExit, match="Binaries must not be set"):
        fdroid_submit.validate(data, str(data["CurrentVersion"]), int(data["CurrentVersionCode"]))


def test_validate_allows_historical_build_methods() -> None:
    data = rendered()
    data["Builds"].insert(
        0,
        {
            "versionName": "1.2.1-alpha",
            "versionCode": 1_002_001,
            "commit": "v-1.2.1-alpha",
            "gradle": ["yes"],
        },
    )

    fdroid_submit.validate(data, str(data["CurrentVersion"]), int(data["CurrentVersionCode"]))


def test_validate_requires_current_build_commands() -> None:
    data = rendered()
    data["Builds"][-1].pop("prebuild")

    with pytest.raises(SystemExit, match="prebuild must be a non-empty list"):
        fdroid_submit.validate(data, str(data["CurrentVersion"]), int(data["CurrentVersionCode"]))


def test_validate_rejects_a_summary_with_punctuation() -> None:
    data = rendered()
    data["Summary"] = "Check whether selected websites remain reachable."

    with pytest.raises(SystemExit, match="Summary must not end with punctuation"):
        fdroid_submit.validate(data, str(data["CurrentVersion"]), int(data["CurrentVersionCode"]))


def test_merge_keeps_history_and_preserves_unknown_fields() -> None:
    version = fdroid_submit.project_version()
    code = fdroid_submit.version_code(version)
    data = fdroid_submit.render(fdroid_submit.DEFAULT_METADATA, version, code, "v-test")
    existing = {
        "CurrentVersion": "1.2.1-alpha",
        "CurrentVersionCode": 1_002_001,
        "MaintainerNotes": "maintainer edit",
        "ReviewerNote": "keep me",
        "Builds": [
            {
                "versionName": "1.2.1-alpha",
                "versionCode": 1_002_001,
                "commit": "v-1.2.1-alpha",
                "gradle": ["yes"],
            }
        ],
    }

    merged = fdroid_submit.merge_existing(data, existing, code)

    assert [entry["versionCode"] for entry in merged["Builds"]] == [1_002_001, code]
    assert merged["ReviewerNote"] == "keep me"
    assert merged["MaintainerNotes"].startswith("Built with Flet")
    assert merged["CurrentVersion"] == version
    assert merged["CurrentVersionCode"] == code
    fdroid_submit.validate(merged, version, code)


def test_merge_fills_fields_missing_from_canonical_metadata() -> None:
    version = fdroid_submit.project_version()
    code = fdroid_submit.version_code(version)
    data = fdroid_submit.render(fdroid_submit.DEFAULT_METADATA, version, code, "v-test")
    data.pop("AuthorEmail")
    existing = {
        "CurrentVersion": version,
        "CurrentVersionCode": code,
        "AuthorEmail": "maintainer@example.com",
        "Builds": [],
    }

    merged = fdroid_submit.merge_existing(data, existing, code)

    assert merged["AuthorEmail"] == "maintainer@example.com"


def test_merge_replaces_the_same_version_code() -> None:
    version = fdroid_submit.project_version()
    code = fdroid_submit.version_code(version)
    data = fdroid_submit.render(fdroid_submit.DEFAULT_METADATA, version, code, "v-test")
    existing = {
        "CurrentVersion": version,
        "CurrentVersionCode": code,
        "Builds": [
            {
                "versionName": version,
                "versionCode": code,
                "commit": "old-commit",
                "gradle": ["yes"],
            }
        ],
    }

    merged = fdroid_submit.merge_existing(data, existing, code)

    assert len(merged["Builds"]) == 1
    assert merged["Builds"][0]["commit"] == "v-test"


def test_merge_refuses_to_downgrade_metadata() -> None:
    version = fdroid_submit.project_version()
    code = fdroid_submit.version_code(version)
    data = fdroid_submit.render(fdroid_submit.DEFAULT_METADATA, version, code, "v-test")
    existing = {"CurrentVersionCode": code + 1, "Builds": []}

    with pytest.raises(SystemExit, match="Refusing to downgrade"):
        fdroid_submit.merge_existing(data, existing, code)


def test_dump_yaml_round_trips() -> None:
    data = rendered()

    dumped = fdroid_submit.dump_yaml(data)

    expected = dict(data)
    expected["Builds"] = [dict(build) for build in data["Builds"]]
    # F-Droid's canonical writer collapses one-command script lists to scalars.
    for build in expected["Builds"]:
        for field in ("build", "prebuild", "postbuild", "init", "patch", "sudo"):
            commands = build.get(field)
            if isinstance(commands, list) and len(commands) == 1:
                build[field] = commands[0]

    assert fdroid_submit.yaml.safe_load(dumped) == expected
    assert "Description: |-\n" in dumped


def test_canonical_tree_has_no_yml_metadata() -> None:
    fdroid_submit.check_no_committed_yml()


def test_check_no_committed_yml_rejects_yaml_suffix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "fdroid").mkdir()
    (tmp_path / "fdroid" / "io.github.ctterrlt.blockprobe.yml").write_text(
        "Name: BlockProbe\n", encoding="utf-8"
    )
    monkeypatch.setattr(fdroid_submit, "ROOT", tmp_path)

    with pytest.raises(SystemExit, match=r"Unexpected \.yml files"):
        fdroid_submit.check_no_committed_yml()


def test_default_commit_prefers_the_release_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_REF_TYPE", "tag")
    monkeypatch.setenv("GITHUB_REF_NAME", "v1.2.2-alpha")
    monkeypatch.setenv("GITHUB_SHA", "0" * 40)

    assert fdroid_submit.default_commit() == "v1.2.2-alpha"


def test_default_commit_uses_the_workflow_sha_on_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_REF_TYPE", "branch")
    monkeypatch.setenv("GITHUB_REF_NAME", "main")
    monkeypatch.setenv("GITHUB_SHA", "a" * 40)

    assert fdroid_submit.default_commit() == "a" * 40
