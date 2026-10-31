#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Render and submit BlockProbe metadata to the official F-Droid repository.

The BlockProbe repository keeps its canonical metadata in
``fdroid/io.github.ctterrlt.blockprobe.yaml``. F-Droid only reads
``metadata/<app-id>.yml`` from the ``fdroiddata`` repository, so this script
renders the canonical file to that name only inside the merge-request branch.
No ``.yml`` file is written to the BlockProbe working tree.

Typical local use::

    python scripts/fdroid_submit.py --check
    python scripts/fdroid_submit.py --output /tmp/io.github.ctterrlt.blockprobe.yml

The release workflow calls ``--submit`` with a GitLab token. The token needs
the ``api`` scope and permission to fork ``fdroid/fdroiddata``. F-Droid
maintainers still control merge, build, signing and publication.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

try:
    import yaml
except ModuleNotFoundError as exc:  # pragma: no cover - exercised by the CLI
    raise SystemExit("PyYAML is required: python -m pip install pyyaml") from exc

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_METADATA = ROOT / "fdroid" / "io.github.ctterrlt.blockprobe.yaml"
APP_ID = "io.github.ctterrlt.blockprobe"
DEFAULT_GITLAB_PROJECT = "fdroid/fdroiddata"
DEFAULT_GITLAB_API = "https://gitlab.com/api/v4"
DEFAULT_TARGET_BRANCH = "master"
DEFAULT_FILE_PATH = f"metadata/{APP_ID}.yml"


class GitLabError(RuntimeError):
    """A GitLab API request failed."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def project_version() -> str:
    """Read the single user-editable version value."""
    value = (ROOT / "src" / "blockprobe" / "VERSION").read_text(encoding="utf-8").strip()
    if not value or any(char.isspace() for char in value):
        raise SystemExit("VERSION must contain one non-empty version string.")
    return value


def version_code(version: str) -> int:
    """Encode major/minor/patch and prerelease stage as Android's version code."""
    match = re.fullmatch(
        r"(\d+)\.(\d+)\.(\d+)"
        r"(?:-(alpha|beta|gamma|rc)(?:\.(\d+))?)?"
        r"(?:\+[0-9A-Za-z.-]+)?",
        version,
    )
    if not match:
        raise SystemExit(f"VERSION is not major.minor.patch[-prerelease]: {version}")
    major, minor, patch = map(int, match.groups()[:3])
    stage, serial_text = match.groups()[3:]
    parts = (major, minor, patch)
    if any(part >= 1000 for part in parts):
        raise SystemExit("Each VERSION component must be less than 1000.")
    stage_slots = {"alpha": 10, "beta": 20, "gamma": 30, "rc": 40}
    stage_slot = stage_slots.get(stage, 99)
    serial = int(serial_text or 0)
    if serial > 9:
        raise SystemExit("Prerelease serials must be between 0 and 9.")
    if stage is not None:
        stage_slot += serial
    code = (major * 1_000_000 + minor * 1_000 + patch) * 100 + stage_slot
    if code <= 0 or code > 2_100_000_000:
        raise SystemExit("VERSION produces an invalid Android version code.")
    return code


def git_head() -> str:
    """Return the checked-out commit, or a harmless placeholder outside git."""
    try:
        return subprocess.check_output(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "HEAD"


def default_commit() -> str:
    """Use the tag name in tag builds, otherwise the workflow commit SHA."""
    ref_type = os.environ.get("GITHUB_REF_TYPE", "")
    ref_name = os.environ.get("GITHUB_REF_NAME", "")
    if ref_type == "tag" and ref_name:
        return ref_name
    return os.environ.get("GITHUB_SHA") or git_head()


def render(metadata_path: Path, version: str, code: int, commit: str) -> dict[str, Any]:
    """Render placeholders and parse the canonical metadata."""
    text = metadata_path.read_text(encoding="utf-8")
    replacements = {
        "@@VERSION@@": version,
        "@@VERSION_CODE@@": str(code),
        "@@COMMIT@@": commit,
    }
    # The canonical template quotes version-code placeholders so that the file
    # is valid YAML on its own. F-Droid needs an integer, so remove the quotes
    # around the substituted value before the generic placeholder pass.
    text = text.replace("'@@VERSION_CODE@@'", str(code))
    text = text.replace('"@@VERSION_CODE@@"', str(code))
    for placeholder, value in replacements.items():
        text = text.replace(placeholder, value)

    leftovers = sorted(set(re.findall(r"@@[A-Z_]+@@", text)))
    if leftovers:
        raise SystemExit(f"Unrendered placeholders in {metadata_path}: {', '.join(leftovers)}")

    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise SystemExit(f"{metadata_path} must contain a YAML mapping.")
    return data


def _as_int(value: Any) -> int | None:
    """Return an integer for YAML values that may be quoted."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def merge_existing(
    data: dict[str, Any],
    existing: dict[str, Any],
    code: int,
) -> dict[str, Any]:
    """Keep prior build recipes and preserve maintainer-added fields."""
    old_code = _as_int(existing.get("CurrentVersionCode"))
    if old_code is not None and old_code > code:
        raise SystemExit(
            f"Refusing to downgrade F-Droid metadata: existing version code {old_code} "
            f"is newer than {code}."
        )

    builds = existing.get("Builds") or []
    if not isinstance(builds, list):
        raise SystemExit("Existing F-Droid metadata has a non-list Builds field.")
    merged = [build for build in builds if isinstance(build, dict)]

    new_builds = data.get("Builds") or []
    if not isinstance(new_builds, list) or not new_builds:
        raise SystemExit("Canonical metadata has no Builds entry.")
    for new_build in new_builds:
        new_code = _as_int(new_build.get("versionCode"))
        if new_code is None:
            raise SystemExit("Canonical metadata has a non-integer versionCode.")
        merged = [
            build for build in merged if _as_int(build.get("versionCode")) != new_code
        ]
        merged.append(new_build)

    merged.sort(key=lambda build: _as_int(build.get("versionCode")) or 0)
    data["Builds"] = merged

    for key, value in existing.items():
        if key != "Builds":
            data.setdefault(key, value)
    return data


def validate(data: dict[str, Any], version: str, code: int) -> None:
    """Check the fields that F-Droid and the release workflow depend on."""
    errors: list[str] = []
    required = (
        "Categories",
        "License",
        "Name",
        "Summary",
        "Description",
        "RepoType",
        "Repo",
        "Builds",
        "ArchivePolicy",
        "AutoUpdateMode",
        "UpdateCheckMode",
        "CurrentVersion",
        "CurrentVersionCode",
    )
    for key in required:
        if not data.get(key):
            errors.append(f"missing or empty field: {key}")

    if data.get("RepoType") != "git":
        errors.append("RepoType must be git")
    repo = data.get("Repo")
    if not isinstance(repo, str) or not repo.startswith("https://"):
        errors.append("Repo must be a public https URL")
    if data.get("Binaries"):
        errors.append("Binaries must not be set: F-Droid builds BlockProbe from source")

    summary = data.get("Summary")
    if isinstance(summary, str):
        if len(summary) > 80:
            errors.append("Summary must be 80 characters or fewer")
        if summary.rstrip().endswith((".", "!", "?")):
            errors.append("Summary must not end with punctuation")

    if _as_int(data.get("ArchivePolicy")) != 1:
        errors.append("ArchivePolicy must be 1")
    if data.get("AutoUpdateMode") != "None":
        errors.append("AutoUpdateMode must be None (the release workflow opens the MR)")
    if data.get("UpdateCheckMode") != "None":
        errors.append("UpdateCheckMode must be None (the release workflow opens the MR)")
    if data.get("CurrentVersion") != version:
        errors.append(f"CurrentVersion must be {version}")
    if _as_int(data.get("CurrentVersionCode")) != code:
        errors.append(f"CurrentVersionCode must be {code}")

    builds = data.get("Builds")
    if not isinstance(builds, list) or not builds:
        errors.append("Builds must be a non-empty list")
        builds = []

    codes: list[int] = []
    for index, build in enumerate(builds):
        if not isinstance(build, dict):
            errors.append(f"Builds[{index}] must be a mapping")
            continue
        build_code = _as_int(build.get("versionCode"))
        if build_code is None:
            errors.append(f"Builds[{index}].versionCode must be an integer")
            continue
        codes.append(build_code)
        if not build.get("versionName"):
            errors.append(f"Builds[{index}].versionName is empty")
        if not build.get("commit"):
            errors.append(f"Builds[{index}].commit is empty")

        # Historical entries may use any F-Droid build method. Only the
        # current entry needs BlockProbe's Flet prebuild/build commands.
        if build_code == code:
            if not str(build.get("output", "")).endswith(".apk"):
                errors.append(f"Builds[{index}].output must end with .apk")
            for field in ("prebuild", "build"):
                commands = build.get(field)
                non_empty_list = isinstance(commands, list) and any(
                    isinstance(command, str) and command.strip()
                    for command in commands
                )
                if not non_empty_list:
                    errors.append(
                        f"Builds[{index}].{field} must be a non-empty list of commands"
                    )

    current = [
        build
        for build in builds
        if isinstance(build, dict) and _as_int(build.get("versionCode")) == code
    ]
    if not current:
        errors.append(f"Builds has no entry for versionCode {code}")
    else:
        build = current[-1]
        if build.get("versionName") != version:
            errors.append(f"Builds versionName must be {version}")
        if not build.get("commit"):
            errors.append("Builds commit is empty")

    if len(codes) != len(set(codes)):
        errors.append("Builds contains duplicate versionCode values")

    if errors:
        raise SystemExit("Invalid F-Droid metadata:\n- " + "\n- ".join(errors))


class _BlockDumper(yaml.SafeDumper):
    """Dump multiline strings as literal blocks for a readable merge request."""


def _represent_string(dumper: yaml.SafeDumper, value: str) -> yaml.ScalarNode:
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_BlockDumper.add_representer(str, _represent_string)


def dump_yaml(data: dict[str, Any]) -> str:
    """Return stable, readable YAML for the fdroiddata commit."""
    if not isinstance(data.get("Builds"), list) or not data["Builds"]:
        raise SystemExit("Refusing to render F-Droid metadata with an empty Builds list.")
    try:
        from ruamel.yaml import YAML
        from ruamel.yaml.comments import CommentedMap
        from ruamel.yaml.scalarstring import LiteralScalarString
    except ModuleNotFoundError as exc:
        raise SystemExit("ruamel.yaml is required: python -m pip install ruamel.yaml") from exc

    field_order = [
        "Disabled", "AntiFeatures", "Categories", "License", "AuthorName",
        "AuthorEmail", "AuthorWebSite", "WebSite", "SourceCode", "IssueTracker",
        "Translation", "Changelog", "Donate", "Liberapay", "OpenCollective",
        "Bitcoin", "Litecoin", "Name", "AutoName", "Summary", "Description",
        "RequiresRoot", "RepoType", "Repo", "Binaries", "Builds",
        "AllowedAPKSigningKeys", "MaintainerNotes", "ArchivePolicy", "AutoUpdateMode",
        "UpdateCheckMode", "UpdateCheckIgnore", "VercodeOperation", "UpdateCheckName",
        "UpdateCheckData", "CurrentVersion", "CurrentVersionCode", "NoSourceSince",
    ]
    build_order = [
        "versionName", "versionCode", "disable", "commit", "timeout", "subdir",
        "submodules", "sudo", "init", "patch", "gradle", "maven", "output",
        "binary", "srclibs", "oldsdkloc", "encoding", "forceversion", "forcevercode",
        "rm", "extlibs", "prebuild", "androidupdate", "target", "scanignore",
        "scandelete", "build", "buildjni", "ndk", "preassemble", "gradleprops",
        "antcommands", "postbuild", "novcheck", "antifeatures",
    ]

    ordered = CommentedMap()
    for key in field_order:
        if key not in data:
            continue
        value = data[key]
        if key in {"Description", "MaintainerNotes"} and isinstance(value, str):
            value = LiteralScalarString(value)
        if key == "Builds":
            builds = []
            for build in value or []:
                if not isinstance(build, dict):
                    continue
                current = CommentedMap(
                    (field, build[field]) for field in build_order if field in build
                )
                builds.append(current)
            value = builds
        ordered[key] = value
    for key, value in data.items():
        if key not in ordered:
            ordered[key] = value

    for key in ("Name", "RepoType", "Builds", "AllowedAPKSigningKeys", "MaintainerNotes", "ArchivePolicy"):
        if key in ordered:
            ordered.yaml_set_comment_before_after_key(key, before="blank")
            ordered.ca.items[key][1][-1].value = "\n"

    dumper = YAML(typ="rt")
    dumper.indent(mapping=2, sequence=4, offset=2)
    dumper.width = 1000
    from io import StringIO

    stream = StringIO()
    dumper.dump(ordered, stream)
    content = stream.getvalue()
    rendered = yaml.safe_load(content)
    if not isinstance(rendered, dict):
        raise SystemExit("Rendered F-Droid metadata is not a YAML mapping.")
    builds = rendered.get("Builds")
    if not isinstance(builds, list) or not builds:
        raise SystemExit("Rendered F-Droid metadata has an empty Builds list.")
    return content


class GitLabClient:
    """Minimal GitLab API client for fork-based merge requests."""

    def __init__(self, token: str, api: str = DEFAULT_GITLAB_API) -> None:
        self.token = token
        self.api = api.rstrip("/")

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
        raw: bool = False,
    ) -> Any:
        url = f"{self.api}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {
            "PRIVATE-TOKEN": self.token,
            "Accept": "application/json",
            "User-Agent": "blockprobe-fdroid-submit",
        }
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            raise GitLabError(exc.code, f"{method} {url} -> HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise GitLabError(0, f"{method} {url} -> {exc.reason}") from exc

        if raw:
            return payload.decode("utf-8")
        if not payload:
            return None
        return json.loads(payload)

    def get_project(self, path: str) -> dict[str, Any]:
        encoded = urllib.parse.quote(path, safe="")
        return self._request("GET", f"/projects/{encoded}")

    def current_user(self) -> dict[str, Any]:
        return self._request("GET", "/user")

    def ensure_fork(self, upstream: str, username: str, fork_path: str | None = None) -> dict[str, Any]:
        fork = fork_path or f"{username}/{upstream.rsplit('/', 1)[-1]}"
        existing = False
        try:
            project = self.get_project(fork)
            existing = True
        except GitLabError as exc:
            if exc.status != 404:
                raise

        if not existing:
            encoded = urllib.parse.quote(upstream, safe="")
            try:
                self._request("POST", f"/projects/{encoded}/fork", body={})
            except GitLabError as exc:
                if exc.status not in (400, 409):
                    raise

        for _ in range(60):
            try:
                project = self.get_project(fork)
                import_status = project.get("import_status")
                if import_status in {"failed", "timeout"}:
                    raise SystemExit(f"GitLab fork {fork} import finished with status {import_status}.")
                # GitLab creates forks asynchronously. A default_branch can be
                # present before the repository refs are imported, so wait for
                # import_status before trying to create a branch in the fork.
                if project.get("default_branch") and import_status not in {
                    "scheduled",
                    "queued",
                    "started",
                    "importing",
                }:
                    return project
            except GitLabError as exc:
                if exc.status != 404:
                    raise
            time.sleep(2)
        raise SystemExit(f"Fork {fork} did not become ready within 120 seconds.")

    def ensure_branch(self, project_id: int, branch: str, ref: str) -> None:
        try:
            self._request(
                "POST",
                f"/projects/{project_id}/repository/branches",
                body={"branch": branch, "ref": ref},
            )
        except GitLabError as exc:
            if exc.status not in (400, 409):
                raise

    def get_file(self, project_id: int, file_path: str, ref: str) -> dict[str, Any] | None:
        encoded = urllib.parse.quote(file_path, safe="")
        try:
            return self._request(
                "GET",
                f"/projects/{project_id}/repository/files/{encoded}",
                params={"ref": ref},
            )
        except GitLabError as exc:
            if exc.status == 404:
                return None
            raise

    def get_file_raw(self, project_id: int, file_path: str, ref: str) -> str | None:
        encoded = urllib.parse.quote(file_path, safe="")
        try:
            return self._request(
                "GET",
                f"/projects/{project_id}/repository/files/{encoded}/raw",
                params={"ref": ref},
                raw=True,
            )
        except GitLabError as exc:
            if exc.status == 404:
                return None
            raise

    def upsert_file(
        self,
        project_id: int,
        file_path: str,
        branch: str,
        content: str,
        message: str,
    ) -> None:
        encoded = urllib.parse.quote(file_path, safe="")
        existing = self.get_file(project_id, file_path, branch)
        body: dict[str, Any] = {
            "branch": branch,
            "content": content,
            "commit_message": message,
            "encoding": "text",
        }
        if existing is None:
            self._request(
                "POST",
                f"/projects/{project_id}/repository/files/{encoded}",
                body=body,
            )
        else:
            body["last_commit_id"] = existing["last_commit_id"]
            self._request(
                "PUT",
                f"/projects/{project_id}/repository/files/{encoded}",
                body=body,
            )

    def find_open_mr(
        self,
        target_id: int,
        source_id: int,
        source_branch: str,
        target_branch: str,
    ) -> dict[str, Any] | None:
        merge_requests = self._request(
            "GET",
            f"/projects/{target_id}/merge_requests",
            params={
                "source_project_id": str(source_id),
                "source_branch": source_branch,
                "target_branch": target_branch,
                "state": "opened",
            },
        )
        if isinstance(merge_requests, list) and merge_requests:
            return merge_requests[0]
        return None

    def create_or_update_mr(
        self,
        target_id: int,
        source_id: int,
        source_branch: str,
        target_branch: str,
        title: str,
        description: str,
    ) -> str:
        existing = self.find_open_mr(target_id, source_id, source_branch, target_branch)
        if existing is not None:
            updated = self._request(
                "PUT",
                f"/projects/{target_id}/merge_requests/{existing['iid']}",
                body={"title": title, "description": description},
            )
            return str(updated["web_url"])

        body = {
            "source_project_id": source_id,
            "source_branch": source_branch,
            "target_project_id": target_id,
            "target_branch": target_branch,
            "title": title,
            "description": description,
            "remove_source_branch": True,
        }
        try:
            created = self._request(
                "POST",
                f"/projects/{target_id}/merge_requests",
                body=body,
            )
            return str(created["web_url"])
        except GitLabError as exc:
            if exc.status not in (400, 409):
                raise
            existing = self.find_open_mr(target_id, source_id, source_branch, target_branch)
            if existing is None:
                raise
            updated = self._request(
                "PUT",
                f"/projects/{target_id}/merge_requests/{existing['iid']}",
                body={"title": title, "description": description},
            )
            return str(updated["web_url"])


def mr_description(version: str, code: int, commit: str, existing: bool) -> str:
    action = "updates" if existing else "adds"
    return f"""## BlockProbe {version}

This merge request {action} the F-Droid metadata for `{APP_ID}`.

- Version: `{version}`
- Version code: `{code}`
- Source commit: `{commit}`
- Archive policy: `1` (only the current version stays in the main repository;
  older versions move to F-Droid's archive)
- License: AGPL-3.0-or-later

### What happens on release

After the GitHub Release workflow succeeds, the BlockProbe F-Droid workflow
renders `fdroid/io.github.ctterrlt.blockprobe.yaml` to the required `.yml` name
and opens or updates this merge request. It does not upload, replace or delete
APKs in the official F-Droid repository. F-Droid maintainers control merge,
build, signing and publication.

The old GitHub release is not touched by this workflow. F-Droid keeps older
build recipes in this file so archived versions remain reproducible.

### Build notes

Flet 1.0.5 / Flutter 3.44.8. The `prebuild` step checks out Flutter from the
F-Droid srclib and downloads the Python 3.14 runtime and Android wheels; the
`build` step copies the finished APK to the recorded output path. The app is
packaged with readable Python sources (`--no-compile-app`,
`--no-compile-packages`).

### Reviewer checklist

- [ ] Metadata matches the tagged source.
- [ ] The Flet Android build succeeds on the buildserver.
- [ ] The APK is signed by F-Droid, not by the upstream debug key.
- [ ] `ArchivePolicy: 1` leaves only this version in the main repository.

_Generated by `scripts/fdroid_submit.py`._
"""


def submit(
    data: dict[str, Any],
    args: argparse.Namespace,
    existing_text: str | None,
) -> str:
    """Create or update the fdroiddata merge request and return its URL."""
    token = args.gitlab_token or os.environ.get("GITLAB_TOKEN")
    if not token:
        raise SystemExit("GITLAB_TOKEN is required for --submit.")

    version = str(data["CurrentVersion"])
    code = _as_int(data.get("CurrentVersionCode"))
    if code is None:
        raise SystemExit("CurrentVersionCode must be an integer.")
    client = GitLabClient(token, args.gitlab_api)
    upstream = client.get_project(args.gitlab_project)
    upstream_id = int(upstream["id"])

    remote_text = existing_text
    if remote_text is None:
        remote_text = client.get_file_raw(upstream_id, args.file_path, args.target_branch)
    if remote_text:
        remote = yaml.safe_load(remote_text)
        if not isinstance(remote, dict):
            raise SystemExit("Existing fdroiddata metadata is not a YAML mapping.")
        data = merge_existing(data, remote, code)
    else:
        data = render(args.metadata, version, code, args.commit)
    validate(data, version, code)
    content = dump_yaml(data)

    username = args.gitlab_username or os.environ.get("GITLAB_USERNAME")
    if not username and not args.no_fork:
        username = str(client.current_user()["username"])

    if args.no_fork:
        fork_id = upstream_id
        fork_name = args.gitlab_project
    else:
        assert username
        fork = client.ensure_fork(args.gitlab_project, username, args.gitlab_fork or None)
        fork_id = int(fork["id"])
        fork_name = str(fork["path_with_namespace"])

    safe_version = re.sub(r"[^0-9A-Za-z._-]+", "-", version).strip("-") or "release"
    branch = args.branch or f"blockprobe-{safe_version}"
    client.ensure_branch(fork_id, branch, args.target_branch)

    up_to_date = False
    if remote_text:
        try:
            up_to_date = yaml.safe_load(remote_text) == data
        except yaml.YAMLError:
            up_to_date = False

    if up_to_date:
        print("Metadata is already up to date; no new commit was created.")
    else:
        message = f"BlockProbe {version}" if remote_text else f"New app: BlockProbe {version}"
        client.upsert_file(fork_id, args.file_path, branch, content, message)

    title = f"Update BlockProbe to {version}" if remote_text else "New app: BlockProbe"
    description = mr_description(version, code, args.commit, existing=bool(remote_text))
    url = client.create_or_update_mr(
        upstream_id,
        fork_id,
        branch,
        args.target_branch,
        title,
        description,
    )
    print(f"Fork: {fork_name}")
    print(f"Branch: {branch}")
    print(f"Merge request: {url}")
    return url


def check_no_committed_yml() -> None:
    """Ensure the BlockProbe tree keeps only .yaml metadata."""
    unexpected = sorted(path for path in (ROOT / "fdroid").glob("*.yml"))
    if unexpected:
        relative = ", ".join(str(path.relative_to(ROOT)) for path in unexpected)
        raise SystemExit(f"Unexpected .yml files in fdroid/: {relative}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render BlockProbe metadata for fdroiddata and open/update its merge request."
    )
    parser.add_argument("--metadata", type=Path, default=DEFAULT_METADATA)
    parser.add_argument(
        "--version", default=None, help="Release version (default: src/blockprobe/VERSION)"
    )
    parser.add_argument("--version-code", type=int, default=None)
    parser.add_argument("--commit", default=None, help="Source tag or commit (default: CI ref)")
    parser.add_argument(
        "--existing",
        type=Path,
        default=None,
        help="Existing fdroiddata metadata for --check/local use",
    )
    parser.add_argument("--output", default=None, help="Write generated .yml here, or - for stdout")
    parser.add_argument("--check", action="store_true", help="Validate metadata and exit")
    parser.add_argument("--submit", action="store_true", help="Open or update the fdroiddata merge request")
    parser.add_argument("--gitlab-token", default=None)
    parser.add_argument("--gitlab-username", default=None)
    parser.add_argument("--gitlab-project", default=os.environ.get("GITLAB_PROJECT", DEFAULT_GITLAB_PROJECT))
    parser.add_argument("--gitlab-fork", default=os.environ.get("GITLAB_FORK") or None)
    parser.add_argument("--gitlab-api", default=os.environ.get("GITLAB_API", DEFAULT_GITLAB_API))
    parser.add_argument("--target-branch", default=DEFAULT_TARGET_BRANCH)
    parser.add_argument("--branch", default=None)
    parser.add_argument("--file-path", default=DEFAULT_FILE_PATH)
    parser.add_argument(
        "--no-fork",
        action="store_true",
        help="Commit to the upstream project instead of a fork",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    check_no_committed_yml()

    version = args.version or project_version()
    code = args.version_code if args.version_code is not None else version_code(version)
    args.commit = args.commit or default_commit()

    if version != project_version():
        print(
            f"Warning: --version {version} differs from src/blockprobe/VERSION {project_version()}",
            file=sys.stderr,
        )

    if args.check and args.output and args.output != "-":
        generated = Path(args.output)
        if not generated.is_file():
            raise SystemExit(f"--check requested but {generated} does not exist.")
        data = yaml.safe_load(generated.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise SystemExit(f"{generated} must contain a YAML mapping.")
        validate(data, version, code)
        print(f"Metadata OK: {generated}")
        return 0

    data = render(args.metadata, version, code, args.commit)
    existing_text: str | None = None
    if args.existing:
        existing_text = args.existing.read_text(encoding="utf-8")
        existing = yaml.safe_load(existing_text)
        if not isinstance(existing, dict):
            raise SystemExit(f"{args.existing} must contain a YAML mapping.")
        data = merge_existing(data, existing, code)

    validate(data, version, code)

    if args.check:
        print(f"Metadata OK: {version} ({code})")
        if not args.submit:
            return 0

    content = dump_yaml(data)
    if args.output:
        if args.output == "-":
            print(content, end="")
        else:
            output = Path(args.output)
            output.write_text(content, encoding="utf-8")
            print(f"Wrote {output}")
    elif not args.submit:
        print(content, end="")

    if args.submit:
        submit(data, args, existing_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
