from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from kogwistar_obsidian_source.cli import load_snapshot
from kogwistar_obsidian_source.models import JsonObject
from kogwistar_obsidian_source.sync import plan_import, plan_to_json
from kogwistar_obsidian_source.vault import scan_vault, write_snapshot


SNAPSHOT: JsonObject = {
    "format_version": "kogwistar-obsidian-source/v1",
    "workspace_id": "workspace-a",
    "vault_id": "vault-a",
    "graph_revision": "graph-1",
    "entities": [
        {
            "entity_id": "node:alpha",
            "entity_kind": "node",
            "title": "Alpha",
            "body": "Managed body one.",
            "properties": {"tags": ["one", "two"]},
            "revision": "alpha-rev-1",
            "payload": {"opaque": {"kept": True}},
        },
        {
            "entity_id": "node:beta",
            "entity_kind": "node",
            "title": "Beta",
            "body": "Managed body two.",
            "properties": {},
            "revision": "beta-rev-1",
            "payload": {},
        },
    ],
    "relations": [
        {
            "edge_id": "edge:alpha-beta",
            "source_entity_id": "node:alpha",
            "target_entity_id": "node:beta",
            "relation_type": "supports",
            "role": "evidence",
            "properties": {"weight": 1},
        }
    ],
}


def _run_rust(*arguments: str, check: bool = True) -> str:
    if shutil.which("cargo") is None:
        pytest.skip("cargo is unavailable")
    result = subprocess.run(
        [
            "cargo",
            "run",
            "--quiet",
            "--manifest-path",
            "rust/Cargo.toml",
            "--bin",
            "kogwistar-obsidian-source",
            "--",
            *arguments,
        ],
        check=check,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
    )
    return result.stdout
    return result.stdout


def _relative_files(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def _edit_alpha(vault_root: Path) -> None:
    note = next(path for path in vault_root.rglob("*.md") if path.name.startswith("Alpha"))
    note.write_text(
        note.read_text(encoding="utf-8").replace("Managed body one.", "Cross-language edit."),
        encoding="utf-8",
        newline="\n",
    )


def test_python_and_rust_export_scan_plan_match() -> None:
    with tempfile.TemporaryDirectory(prefix=".tmp_cross_language_", dir=Path.cwd()) as work:
        work_path = Path(work)
        snapshot_path = work_path / "snapshot.json"
        snapshot_path.write_text(json.dumps(SNAPSHOT), encoding="utf-8")
        python_vault = work_path / "python-vault"
        rust_vault = work_path / "rust-vault"

        write_snapshot(load_snapshot(snapshot_path), python_vault)
        _run_rust("export", "--snapshot", str(snapshot_path), "--vault", str(rust_vault))
        assert _relative_files(python_vault) == _relative_files(rust_vault)

        _edit_alpha(python_vault)
        _edit_alpha(rust_vault)

        python_scan = scan_vault(python_vault)
        python_plan = plan_import(load_snapshot(snapshot_path), python_scan)
        expected_plan = json.dumps(plan_to_json(python_plan), sort_keys=True, indent=2)

        plan_path = work_path / "rust-plan.json"
        _run_rust(
            "plan-import",
            "--snapshot",
            str(snapshot_path),
            "--vault",
            str(rust_vault),
            "--deletion-mode",
            "ignore",
            "--output",
            str(plan_path),
        )
        actual_plan = json.dumps(json.loads(plan_path.read_text(encoding="utf-8")), sort_keys=True, indent=2)
        assert actual_plan == expected_plan


def test_python_and_rust_conflicts_match() -> None:
    with tempfile.TemporaryDirectory(prefix=".tmp_cross_language_conflicts_", dir=Path.cwd()) as work:
        work_path = Path(work)
        snapshot_path = work_path / "snapshot.json"
        snapshot_path.write_text(json.dumps(SNAPSHOT), encoding="utf-8")
        vault_path = work_path / "vault"
        vault_path.mkdir()
        (vault_path / "not-manifest.md").write_text("# unmanaged\n", encoding="utf-8", newline="\n")

        rust_plan = work_path / "rust-plan.json"
        _run_rust(
            "plan-import",
            "--snapshot",
            str(snapshot_path),
            "--vault",
            str(vault_path),
            "--deletion-mode",
            "ignore",
            "--output",
            str(rust_plan),
            check=False,
        )
        rust_conflicts = json.loads(
            _run_rust("conflicts", "--plan", str(rust_plan), check=False)
        )

        python_plan = plan_import(load_snapshot(snapshot_path), scan_vault(vault_path))
        python_conflicts = {
            "plan_id": python_plan.plan_id,
            "conflicts": plan_to_json(python_plan)["conflicts"],
        }
        assert rust_conflicts == python_conflicts
