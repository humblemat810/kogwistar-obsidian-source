from __future__ import annotations

import json
from pathlib import Path

import pytest

from kogwistar_obsidian_source import (
    GraphEntityRecord,
    GraphSnapshot,
    ManagedRelationRecord,
    plan_import,
    scan_vault,
    write_snapshot,
)
from kogwistar_obsidian_source.canonical import canonical_digest


def _snapshot() -> GraphSnapshot:
    return GraphSnapshot(
        workspace_id="workspace-a",
        vault_id="vault-a",
        graph_revision="graph-1",
        entities=(
            GraphEntityRecord(
                entity_id="node:alpha",
                entity_kind="node",
                title="Alpha",
                body="A managed note.",
                properties={"tags": ["one", "two"]},
                revision="node-rev-1",
                payload={"opaque": {"kept": True}},
            ),
            GraphEntityRecord(
                entity_id="node:beta",
                entity_kind="node",
                title="Beta",
                body="Second note.",
                properties={},
                revision="node-rev-2",
            ),
        ),
        relations=(
            ManagedRelationRecord(
                edge_id="edge:alpha-beta",
                source_entity_id="node:alpha",
                target_entity_id="node:beta",
                relation_type="supports",
                role="evidence",
                properties={"weight": 1},
            ),
        ),
    )


def test_export_scan_import_plan_is_lossless_for_unchanged_vault(tmp_path: Path) -> None:
    manifest = write_snapshot(_snapshot(), tmp_path)
    scan = scan_vault(tmp_path)

    assert scan.valid
    assert scan.manifest is not None
    assert scan.manifest.digest == manifest.digest
    assert scan.unmanaged_paths == ()
    plan = plan_import(_snapshot(), scan)
    assert plan.applyable is False
    assert plan.intents == ()
    assert plan.conflicts == ()


def test_user_note_edit_creates_one_typed_intent(tmp_path: Path) -> None:
    write_snapshot(_snapshot(), tmp_path)
    note = next((tmp_path / "Notes" / "node").glob("*.md"))
    note.write_text(note.read_text(encoding="utf-8").replace("A managed note.", "Edited note."), encoding="utf-8")

    plan = plan_import(_snapshot(), scan_vault(tmp_path))

    assert plan.applyable
    assert [intent.entity_id for intent in plan.intents] == ["node:alpha"]
    assert plan.intents[0].body == "Edited note."


def test_concurrent_graph_and_vault_edits_are_conflicts(tmp_path: Path) -> None:
    write_snapshot(_snapshot(), tmp_path)
    note = next((tmp_path / "Notes" / "node").glob("*.md"))
    note.write_text(note.read_text(encoding="utf-8").replace("A managed note.", "Edited note."), encoding="utf-8")
    changed = _snapshot()
    changed_entity = GraphEntityRecord(
        entity_id="node:alpha",
        entity_kind="node",
        title="Alpha changed in graph",
        body="A managed note.",
        properties={"tags": ["one", "two"]},
        revision="node-rev-new",
        payload={"opaque": {"kept": True}},
    )
    changed = GraphSnapshot(
        workspace_id=changed.workspace_id,
        vault_id=changed.vault_id,
        graph_revision="graph-2",
        entities=(changed_entity, changed.entities[1]),
        relations=changed.relations,
    )

    plan = plan_import(changed, scan_vault(tmp_path))

    assert not plan.applyable
    assert plan.conflicts[0].reason == "both_changed"


def test_missing_managed_file_only_tombstones_when_opted_in(tmp_path: Path) -> None:
    write_snapshot(_snapshot(), tmp_path)
    note = next((tmp_path / "Notes" / "node").glob("*.md"))
    note.unlink()
    scan = scan_vault(tmp_path)

    ignored = plan_import(_snapshot(), scan, deletion_mode="ignore")
    tombstoned = plan_import(_snapshot(), scan, deletion_mode="tombstone")

    assert ignored.tombstone_ids == ()
    assert tombstoned.tombstone_ids == ("node:alpha",)


def test_tampered_manifest_and_unmanaged_markdown_are_reported(tmp_path: Path) -> None:
    write_snapshot(_snapshot(), tmp_path)
    (tmp_path / "Notes" / "unmanaged.md").write_text("# Unmanaged", encoding="utf-8")
    manifest_path = tmp_path / ".kogwistar" / "obsidian-source" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["vault_id"] = "wrong-vault"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    scan = scan_vault(tmp_path)

    assert not scan.valid
    assert scan.errors
    assert scan.unmanaged_paths == ()


def test_note_workspace_mismatch_is_rejected_before_planning(tmp_path: Path) -> None:
    write_snapshot(_snapshot(), tmp_path)
    note = next((tmp_path / "Notes" / "node").glob("*.md"))
    note.write_text(
        note.read_text(encoding="utf-8").replace('"workspace_id": "workspace-a"', '"workspace_id": "other-workspace"'),
        encoding="utf-8",
    )

    plan = plan_import(_snapshot(), scan_vault(tmp_path))

    assert not plan.applyable
    assert plan.conflicts[0].reason == "invalid"
    assert "workspace" in plan.conflicts[0].message


def test_relation_endpoint_outside_snapshot_is_rejected(tmp_path: Path) -> None:
    write_snapshot(_snapshot(), tmp_path)
    note = next((tmp_path / "Notes" / "node").glob("*alpha*.md"))
    text = note.read_text(encoding="utf-8")
    start = text.index("<!-- KOGWISTAR-RELATIONS-START -->")
    end = text.index("<!-- KOGWISTAR-RELATIONS-END -->")
    relations = json.loads(text[start + len("<!-- KOGWISTAR-RELATIONS-START -->") : end].strip())
    relations[0]["target_entity_id"] = "node:missing"
    relations[0].pop("digest", None)
    replacement = json.dumps(relations, ensure_ascii=False, sort_keys=True, indent=2)
    note.write_text(text[: start + len("<!-- KOGWISTAR-RELATIONS-START -->")] + "\n" + replacement + "\n" + text[end:], encoding="utf-8")

    plan = plan_import(_snapshot(), scan_vault(tmp_path))

    assert not plan.applyable
    assert plan.conflicts[0].reason == "invalid"
    assert "edge:alpha-beta" in plan.conflicts[0].message


def test_manifest_note_path_must_match_canonical_identity_path(tmp_path: Path) -> None:
    manifest = write_snapshot(_snapshot(), tmp_path)
    original = tmp_path / manifest.entries[0].path
    wrong_relative = "Notes/node/not-the-canonical-name.md"
    wrong = tmp_path / wrong_relative
    wrong.parent.mkdir(parents=True, exist_ok=True)
    original.replace(wrong)
    manifest_path = tmp_path / ".kogwistar" / "obsidian-source" / "manifest.json"
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw["entries"][0]["path"] = wrong_relative
    raw["digest"] = ""
    raw["digest"] = canonical_digest({key: value for key, value in raw.items() if key != "digest"})
    manifest_path.write_text(json.dumps(raw), encoding="utf-8")

    scan = scan_vault(tmp_path)

    assert not scan.valid
    assert any("canonical" in error for error in scan.errors)


def test_export_rejects_symlinked_note_directory(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    notes = tmp_path / "Notes"
    try:
        notes.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    with pytest.raises(ValueError, match="symlink"):
        write_snapshot(_snapshot(), tmp_path)


@pytest.mark.parametrize("relative", ["../escape.md", "/absolute.md", "Notes/../escape.md"])
def test_path_escape_is_rejected(tmp_path: Path, relative: str) -> None:
    write_snapshot(_snapshot(), tmp_path)
    manifest_path = tmp_path / ".kogwistar" / "obsidian-source" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["entries"][0]["path"] = relative
    manifest["digest"] = ""  # A malformed manifest must fail before filesystem access.
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    scan = scan_vault(tmp_path)

    assert not scan.valid
    assert scan.errors

