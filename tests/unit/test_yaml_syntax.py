# SPDX-License-Identifier: AGPL-3.0-or-later
"""Keep checked-in YAML files syntactically valid."""

from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

ISSUE_FORM_TYPES = {
    "checkboxes",
    "dropdown",
    "input",
    "markdown",
    "textarea",
    "upload",
}


def test_tracked_yaml_files_parse() -> None:
    """Parse the workflows and packaging metadata without YAML 1.1 coercion."""
    root = Path(__file__).resolve().parents[2]
    tracked_files = subprocess.check_output(
        ["git", "ls-files", "*.yaml", "*.yml"], cwd=root, text=True
    ).splitlines()
    files = [path for path in tracked_files if (root / path).is_file()]

    assert files, "expected tracked YAML/YML configuration files"
    for relative_path in files:
        content = (root / relative_path).read_text(encoding="utf-8")
        documents = list(yaml.compose_all(content, Loader=yaml.BaseLoader))
        assert documents and all(document is not None for document in documents), (
            f"{relative_path} must contain a non-empty YAML document"
        )


def test_github_issue_forms_have_supported_input_fields() -> None:
    """Reject empty/comment-only forms and unsupported issue-form field types."""
    root = Path(__file__).resolve().parents[2]
    form_paths = sorted((root / ".github" / "ISSUE_TEMPLATE").glob("*.yaml"))

    assert form_paths, "expected GitHub issue forms"
    for path in form_paths:
        form = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        assert isinstance(form, dict), f"{path.name} must contain a YAML mapping"
        for key in ("name", "description", "body"):
            assert form.get(key), f"{path.name} needs a non-empty {key}"

        body = form["body"]
        assert isinstance(body, list), f"{path.name} body must be a list"
        ids: set[str] = set()
        has_input = False
        for field in body:
            assert isinstance(field, dict), f"{path.name} has a malformed field"
            field_type = field.get("type")
            assert field_type in ISSUE_FORM_TYPES, (
                f"{path.name} has unsupported field {field_type!r}"
            )
            if field_type == "markdown":
                continue
            has_input = True
            field_id = field.get("id")
            assert field_id and field_id not in ids, (
                f"{path.name} has a missing or duplicate field id"
            )
            ids.add(field_id)
            assert field.get("attributes", {}).get("label"), (
                f"{path.name} field {field_id!r} needs a label"
            )
        assert has_input, f"{path.name} must request at least one user input"
