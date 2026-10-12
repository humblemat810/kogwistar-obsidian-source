from __future__ import annotations

import json
from pathlib import Path

import pytest

from kogwistar_obsidian_source.cli import load_snapshot


def test_snapshot_rejects_numeric_identity_fields(tmp_path: Path) -> None:
    path = tmp_path / "snapshot.json"
    path.write_text(
        json.dumps(
            {
                "workspace_id": 1,
                "vault_id": "vault-a",
                "graph_revision": "graph-1",
                "entities": [],
                "relations": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="workspace_id"):
        load_snapshot(path)


def test_snapshot_preserves_json_body_without_string_coercion(tmp_path: Path) -> None:
    path = tmp_path / "snapshot.json"
    path.write_text(
        json.dumps(
            {
                "workspace_id": "workspace-a",
                "vault_id": "vault-a",
                "graph_revision": "graph-1",
                "entities": [
                    {
                        "entity_id": "node:a",
                        "entity_kind": "node",
                        "title": "A",
                        "body": "",
                        "properties": {},
                        "revision": "rev-1",
                        "payload": {},
                    }
                ],
                "relations": [],
            }
        ),
        encoding="utf-8",
    )

    snapshot = load_snapshot(path)

    assert snapshot.entities[0].body == ""


def test_snapshot_rejects_non_string_body_instead_of_coercing_it(tmp_path: Path) -> None:
    path = tmp_path / "snapshot.json"
    path.write_text(
        json.dumps(
            {
                "workspace_id": "workspace-a",
                "vault_id": "vault-a",
                "graph_revision": "graph-1",
                "entities": [
                    {
                        "entity_id": "node:a",
                        "entity_kind": "node",
                        "title": "A",
                        "body": 0,
                        "properties": {},
                        "revision": "rev-1",
                        "payload": {},
                    }
                ],
                "relations": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="body"):
        load_snapshot(path)
