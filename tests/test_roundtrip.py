from __future__ import annotations

import json
from dataclasses import replace
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
from kogwistar_obsidian_source.models import VaultImportScan
from kogwistar_obsidian_source.vault import VaultFormatError


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
    note = next((tmp_path / "Notes" / "node").glob("*Alpha*.md"))
    note.write_text(note.read_text(encoding="utf-8").replace("A managed note.", "Edited note."), encoding="utf-8")

    plan = plan_import(_snapshot(), scan_vault(tmp_path))

    assert plan.applyable
    assert [intent.entity_id for intent in plan.intents] == ["node:alpha"]
    assert plan.intents[0].body == "Edited note."


def test_concurrent_graph_and_vault_edits_are_conflicts(tmp_path: Path) -> None:
    write_snapshot(_snapshot(), tmp_path)
    note = next((tmp_path / "Notes" / "node").glob("*Alpha*.md"))
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


def test_revision_only_graph_change_conflicts_with_a_vault_edit(tmp_path: Path) -> None:
    write_snapshot(_snapshot(), tmp_path)
    note = next((tmp_path / "Notes" / "node").glob("*Alpha*.md"))
    note.write_text(note.read_text(encoding="utf-8").replace("A managed note.", "Edited note."), encoding="utf-8")
    original = _snapshot()
    changed = GraphSnapshot(
        workspace_id=original.workspace_id,
        vault_id=original.vault_id,
        graph_revision="graph-2",
        entities=(replace(original.entities[0], revision="node-rev-2"), original.entities[1]),
        relations=original.relations,
    )

    plan = plan_import(changed, scan_vault(tmp_path))

    assert not plan.applyable
    assert plan.intents == ()
    assert plan.conflicts[0].reason == "both_changed"


def test_invalid_scan_never_carries_mutation_intents(tmp_path: Path) -> None:
    write_snapshot(_snapshot(), tmp_path)
    note = next((tmp_path / "Notes" / "node").glob("*Alpha*.md"))
    note.write_text(note.read_text(encoding="utf-8").replace("A managed note.", "Edited note."), encoding="utf-8")
    manifest = tmp_path / ".kogwistar" / "obsidian-source" / "manifest.json"
    manifest.write_text("{not json", encoding="utf-8")

    plan = plan_import(_snapshot(), scan_vault(tmp_path))

    assert not plan.applyable
    assert plan.intents == ()
    assert plan.tombstone_ids == ()
    assert plan.conflicts[0].reason == "invalid"


def test_plan_identity_is_independent_of_scan_note_order(tmp_path: Path) -> None:
    write_snapshot(_snapshot(), tmp_path)
    for note in (tmp_path / "Notes" / "node").glob("*.md"):
        note.write_text(note.read_text(encoding="utf-8").replace("note.", "note, edited."), encoding="utf-8")
    scan = scan_vault(tmp_path)
    reordered = VaultImportScan(
        manifest=scan.manifest,
        notes=tuple(reversed(scan.notes)),
        missing_paths=scan.missing_paths,
        unmanaged_paths=scan.unmanaged_paths,
        errors=scan.errors,
    )

    first = plan_import(_snapshot(), scan)
    second = plan_import(_snapshot(), reordered)

    assert first.applyable
    assert first.plan_id == second.plan_id
    assert first.intents == second.intents


def test_snapshot_identity_mismatch_never_carries_mutation_intents(tmp_path: Path) -> None:
    write_snapshot(_snapshot(), tmp_path)
    note = next((tmp_path / "Notes" / "node").glob("*Alpha*.md"))
    note.write_text(note.read_text(encoding="utf-8").replace("A managed note.", "Edited note."), encoding="utf-8")
    original = _snapshot()
    wrong_snapshot = GraphSnapshot(
        workspace_id=original.workspace_id,
        vault_id="other-vault",
        graph_revision=original.graph_revision,
        entities=original.entities,
        relations=original.relations,
    )

    plan = plan_import(wrong_snapshot, scan_vault(tmp_path))

    assert not plan.applyable
    assert plan.intents == ()
    assert plan.tombstone_ids == ()
    assert plan.conflicts[0].reason == "invalid"


def test_missing_managed_file_only_tombstones_when_opted_in(tmp_path: Path) -> None:
    write_snapshot(_snapshot(), tmp_path)
    note = next((tmp_path / "Notes" / "node").glob("*Alpha*.md"))
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
    note = next((tmp_path / "Notes" / "node").glob("*Alpha*.md"))
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
    note = next((tmp_path / "Notes" / "node").glob("*Alpha*.md"))
    text = note.read_text(encoding="utf-8")
    start = text.index("<!-- KOGWISTAR-RELATIONS-START -->")
    end = text.index("<!-- KOGWISTAR-RELATIONS-END -->")
    relations = json.loads(text[start + len("<!-- KOGWISTAR-RELATIONS-START -->") : end].strip())
    relations[0]["target_entity_id"] = "node:missing"
    relation_payload = {key: value for key, value in relations[0].items() if key != "digest"}
    relations[0]["digest"] = canonical_digest(relation_payload)
    replacement = json.dumps(relations, ensure_ascii=False, sort_keys=True, indent=2)
    note.write_text(text[: start + len("<!-- KOGWISTAR-RELATIONS-START -->")] + "\n" + replacement + "\n" + text[end:], encoding="utf-8")

    plan = plan_import(_snapshot(), scan_vault(tmp_path))

    assert not plan.applyable
    assert plan.conflicts[0].reason == "invalid"
    assert "edge:alpha-beta" in plan.conflicts[0].message


def test_relation_digest_is_required(tmp_path: Path) -> None:
    write_snapshot(_snapshot(), tmp_path)
    note = next((tmp_path / "Notes" / "node").glob("*Alpha*.md"))
    text = note.read_text(encoding="utf-8")
    start = text.index("<!-- KOGWISTAR-RELATIONS-START -->")
    end = text.index("<!-- KOGWISTAR-RELATIONS-END -->")
    relations = json.loads(text[start + len("<!-- KOGWISTAR-RELATIONS-START -->") : end].strip())
    relations[0].pop("digest")
    replacement = json.dumps(relations, ensure_ascii=False, sort_keys=True, indent=2)
    note.write_text(text[: start + len("<!-- KOGWISTAR-RELATIONS-START -->")] + "\n" + replacement + "\n" + text[end:], encoding="utf-8")

    scan = scan_vault(tmp_path)

    assert not scan.valid
    assert any("relation digest" in error for error in scan.errors)


def test_duplicate_canonical_relation_markers_are_rejected(tmp_path: Path) -> None:
    write_snapshot(_snapshot(), tmp_path)
    note = next((tmp_path / "Notes" / "node").glob("*Alpha*.md"))
    note.write_text(
        note.read_text(encoding="utf-8") + "\n<!-- KOGWISTAR-RELATIONS-START -->\n[]\n<!-- KOGWISTAR-RELATIONS-END -->\n",
        encoding="utf-8",
    )

    scan = scan_vault(tmp_path)

    assert not scan.valid
    assert any("ambiguous" in error for error in scan.errors)


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


def test_reexport_keeps_the_ledger_path_when_title_changes(tmp_path: Path) -> None:
    first = write_snapshot(_snapshot(), tmp_path)
    original = _snapshot()
    renamed = GraphSnapshot(
        workspace_id=original.workspace_id,
        vault_id=original.vault_id,
        graph_revision="graph-2",
        entities=(replace(original.entities[0], title="Alpha Renamed", revision="node-rev-2"), original.entities[1]),
        relations=original.relations,
    )

    second = write_snapshot(renamed, tmp_path)

    assert second.entries[0].path == first.entries[0].path
    assert scan_vault(tmp_path).valid


def test_export_refuses_to_overwrite_unmanaged_canonical_path(tmp_path: Path) -> None:
    entity_id = "node:alpha"
    suffix = canonical_digest({"id": entity_id})[:12]
    collision = tmp_path / "Notes" / "node" / f"Alpha--{suffix}.md"
    collision.parent.mkdir(parents=True)
    collision.write_text("# User-owned note", encoding="utf-8")

    with pytest.raises(VaultFormatError, match="unmanaged"):
        write_snapshot(_snapshot(), tmp_path)


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

