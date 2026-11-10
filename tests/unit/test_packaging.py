# SPDX-License-Identifier: AGPL-3.0-or-later
"""Regression checks for native wheels and platform library staging."""

from __future__ import annotations

import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from scripts import build, build_rust_engine
from scripts.smoke_wheel import validate_wheel


def _wheel(path: Path, *, platform_tag: str, purelib: bool = False) -> Path:
    path = path / f"blockprobe-1.4.0-py3-none-{platform_tag}.whl"
    if os.name == "nt":
        native_library = "blockprobe/_native/blockprobe_core.dll"
    elif sys.platform == "darwin":
        native_library = "blockprobe/_native/libblockprobe_core.dylib"
    else:
        native_library = "blockprobe/_native/libblockprobe_core.so"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "blockprobe-1.4.0.dist-info/WHEEL",
            f"Wheel-Version: 1.0\nRoot-Is-Purelib: {str(purelib).lower()}\n",
        )
        archive.writestr(native_library, b"native")
    return path


def test_native_wheel_validator_accepts_platform_tag_and_non_pure_metadata(
    tmp_path: Path,
) -> None:
    wheel = _wheel(tmp_path, platform_tag="linux_x86_64")

    validate_wheel(wheel)


def test_native_wheel_validator_rejects_universal_tag(tmp_path: Path) -> None:
    wheel = _wheel(tmp_path, platform_tag="any")

    with pytest.raises(SystemExit, match="platform-specific"):
        validate_wheel(wheel)


def test_native_wheel_validator_rejects_pure_python_metadata(tmp_path: Path) -> None:
    wheel = _wheel(tmp_path, platform_tag="linux_x86_64", purelib=True)

    with pytest.raises(SystemExit, match="pure Python"):
        validate_wheel(wheel)


@pytest.mark.parametrize(
    "relative_source",
    (
        "blockprobe_core.wasm",
        "deps/blockprobe_core.wasm",
        "deps/blockprobe_core-abc123.wasm",
        "deps/libblockprobe_core-def456.so",
        "libblockprobe_core.wasm",
    ),
)
def test_pyodide_side_module_is_found_recursively_and_staged_with_loader_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    relative_source: str,
) -> None:
    build_dir = tmp_path / "release"
    source = build_dir / relative_source
    source.parent.mkdir(parents=True)
    source.write_bytes(b"wasm-side-module")
    monkeypatch.setattr(build_rust_engine, "PACKAGE", tmp_path / "package")

    destination = build_rust_engine.stage_pyodide_side_module(build_dir)

    assert destination.name == "libblockprobe_core.so"
    assert destination.read_bytes() == source.read_bytes()
    assert destination.parent.name == "pyemscripten-2026.0-wasm32"
    assert destination.parent.parent.name == "emscripten"


def test_pyodide_side_module_reports_discovered_artifacts(tmp_path: Path) -> None:
    release_dir = tmp_path / "release"
    release_dir.mkdir()
    (release_dir / "libblockprobe_core.rlib").touch()

    with pytest.raises(SystemExit, match=r"discovered: libblockprobe_core\.rlib"):
        build_rust_engine.stage_pyodide_side_module(release_dir)


@pytest.mark.parametrize("suffix", (".apk", ".aab"))
def test_android_artifact_verifier_uses_format_specific_tool(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    suffix: str,
) -> None:
    artifact = tmp_path / f"blockprobe{suffix}"
    artifact.touch()
    aapt2 = tmp_path / "aapt2"
    bundletool = tmp_path / "bundletool.jar"
    aapt2.touch()
    bundletool.touch()
    monkeypatch.setattr(build, "_aapt2", lambda: aapt2)
    monkeypatch.setattr(build.shutil, "which", lambda _name: "/usr/bin/java")
    monkeypatch.setenv("BUNDLETOOL_JAR", str(bundletool))
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        manifest = "\n".join(build.ANDROID_PACKAGED_MANIFEST_TOKENS)
        return subprocess.CompletedProcess(command, 0, stdout=manifest, stderr="")

    monkeypatch.setattr(build.subprocess, "run", fake_run)

    verified = build.verify_android_artifacts([artifact])

    assert len(verified) == 1
    assert "INTERNET" in verified[0]
    assert "ACCESS_NETWORK_STATE" in verified[0]
    if suffix == ".apk":
        assert commands[0][0] == str(aapt2)
        assert commands[0][1:3] == ["dump", "xmltree"]
    else:
        assert commands[0][:3] == ["/usr/bin/java", "-jar", str(bundletool)]
        assert commands[0][3:5] == ["dump", "manifest"]
        assert commands[0][5] == f"--bundle={artifact}"


@pytest.mark.parametrize(
    "missing_permission",
    (
        "android.permission.INTERNET",
        "android.permission.ACCESS_NETWORK_STATE",
    ),
)
def test_android_artifact_verifier_requires_dns_network_permissions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    missing_permission: str,
) -> None:
    artifact = tmp_path / "blockprobe.apk"
    artifact.touch()
    aapt2 = tmp_path / "aapt2"
    aapt2.touch()
    monkeypatch.setattr(build, "_aapt2", lambda: aapt2)
    manifest = "\n".join(
        token
        for token in build.ANDROID_PACKAGED_MANIFEST_TOKENS
        if token != missing_permission
    )

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, stdout=manifest, stderr="")

    monkeypatch.setattr(build.subprocess, "run", fake_run)

    with pytest.raises(SystemExit, match=missing_permission):
        build.verify_android_artifacts([artifact])


def test_android_aab_manifest_verifier_reports_missing_bundletool(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    artifact = tmp_path / "blockprobe.aab"
    artifact.touch()
    monkeypatch.setattr(build, "_aapt2", lambda: tmp_path / "aapt2")
    monkeypatch.delenv("BUNDLETOOL_JAR", raising=False)

    assert build.verify_android_artifacts([artifact]) == []
    assert "BUNDLETOOL_JAR is not set" in capsys.readouterr().out
