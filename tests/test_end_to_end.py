from __future__ import annotations

import json
from dataclasses import dataclass, replace
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
import sys
from typing import Protocol, cast

import pytest

from kogwistar_obsidian_source import (
    GraphEntityRecord,
    GraphSnapshot,
    ManagedRelationRecord,
    VaultSyncPlan,
    plan_import,
    plan_to_json,
    scan_vault,
    write_snapshot,
)
from kogwistar_obsidian_source.canonical import canonical_digest
from kogwistar_obsidian_source.models import JsonObject
from kogwistar_obsidian_source.plugin import build_workbench_extension
from kogwistar_obsidian_source.typing_contracts import WorkbenchRequest


@pytest.fixture
def source_workbench_contracts(monkeypatch: pytest.MonkeyPatch) -> None:
    @dataclass(frozen=True)
    class Response:
        body: object

    @dataclass(frozen=True)
    class Route:
        method: str
        path: str
        required_scope: str
        workspace_id: object
        handler: object

    @dataclass(frozen=True)
    class Extension:
        extension_id: str
        routes: tuple[Route, ...]

    contracts = ModuleType("kogwistar_llm_wiki.app_contracts")
    setattr(contracts, "WorkbenchExtension", Extension)
    setattr(contracts, "WorkbenchExtensionResponse", Response)
    setattr(contracts, "WorkbenchExtensionRoute", Route)
    monkeypatch.setitem(sys.modules, "kogwistar_llm_wiki.app_contracts", contracts)


def _graph() -> GraphSnapshot:
    return GraphSnapshot(
        workspace_id="workspace-a",
        vault_id="vault-a",
        graph_revision="graph-1",
        entities=(
            GraphEntityRecord(
                entity_id="node:alpha",
                entity_kind="node",
                title="Alpha",
                body="Original body.",
                properties={"tag": "one"},
                revision="alpha-rev-1",
            ),
            GraphEntityRecord(
                entity_id="node:beta",
                entity_kind="node",
                title="Beta",
                body="Beta body.",
                properties={},
                revision="beta-rev-1",
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


class _GraphHost:
    """Small host double that preserves the real callback/revision contract."""

    def __init__(self, vault_root: Path) -> None:
        self.vault_root = vault_root
        self.snapshot = _graph()
        self.plans: dict[str, VaultSyncPlan] = {}
        self.write_allowed = True

    def export_snapshot(self, workspace_id: str, vault_id: str) -> JsonObject:
        if (workspace_id, vault_id) != (self.snapshot.workspace_id, self.snapshot.vault_id):
            raise PermissionError("graph scope is not authorized")
        manifest = write_snapshot(self.snapshot, self.vault_root)
        return {"status": "exported", "digest": manifest.digest}

    def scan_import(self, workspace_id: str, vault_id: str, _payload: JsonObject) -> JsonObject:
        if (workspace_id, vault_id) != (self.snapshot.workspace_id, self.snapshot.vault_id):
            raise PermissionError("graph scope is not authorized")
        scan = scan_vault(self.vault_root)
        return {"valid": scan.valid, "errors": list(scan.errors), "missing": list(scan.missing_paths)}

    def plan_import(self, workspace_id: str, vault_id: str, _payload: JsonObject) -> JsonObject:
        if (workspace_id, vault_id) != (self.snapshot.workspace_id, self.snapshot.vault_id):
            raise PermissionError("graph scope is not authorized")
        plan = plan_import(self.snapshot, scan_vault(self.vault_root))
        self.plans[plan.plan_id] = plan
        return plan_to_json(plan)

    def apply_import(self, workspace_id: str, vault_id: str, plan_id: str, deletion_mode: str) -> JsonObject:
        if not self.write_allowed:
            raise PermissionError("graph write is not authorized")
        if (workspace_id, vault_id) != (self.snapshot.workspace_id, self.snapshot.vault_id):
            raise PermissionError("graph scope is not authorized")
        plan = self.plans.get(plan_id)
        if plan is None or getattr(plan, "deletion_mode") != deletion_mode:
            return {"status": "rejected", "reason": "unknown plan"}
        if plan.conflicts:
            return {"status": "conflict", "plan_id": plan_id}

        entities = list(self.snapshot.entities)
        relations = list(self.snapshot.relations)
        for intent in plan.intents:
            entity_index = next(
                (index for index, entity in enumerate(entities) if entity.entity_id == intent.entity_id),
                None,
            )
            if entity_index is None:
                return {"status": "rejected", "reason": "missing entity"}
            entity = entities[entity_index]
            if entity.revision != intent.expected_revision:
                return {"status": "rejected", "reason": "stale revision"}
            entities[entity_index] = replace(
                entity,
                title=intent.title,
                body=intent.body,
                properties=intent.properties,
                revision=f"{entity.revision}:imported",
            )
            relations = [relation for relation in relations if relation.source_entity_id != intent.entity_id]
            relations.extend(intent.relations)
        self.snapshot = GraphSnapshot(
            workspace_id=self.snapshot.workspace_id,
            vault_id=self.snapshot.vault_id,
            graph_revision=f"{self.snapshot.graph_revision}:imported",
            entities=tuple(entities),
            relations=tuple(relations),
        )
        return {"status": "applied", "plan_id": plan_id}

    def list_conflicts(self, workspace_id: str, vault_id: str) -> JsonObject:
        if (workspace_id, vault_id) != (self.snapshot.workspace_id, self.snapshot.vault_id):
            raise PermissionError("graph scope is not authorized")
        return {"conflicts": []}


class _Response(Protocol):
    body: JsonObject


def _routes(host: _GraphHost, source_workbench_contracts: None) -> dict[str, Callable[[WorkbenchRequest], _Response]]:
    extension = build_workbench_extension(host)
    return {
        route.path.rsplit("/", 1)[-1]: cast(Callable[[WorkbenchRequest], _Response], route.handler)
        for route in extension.routes
    }


def test_plugin_routes_round_trip_graph_data_through_a_managed_vault(
    tmp_path: Path, source_workbench_contracts: None
) -> None:
    host = _GraphHost(tmp_path)
    routes = _routes(host, source_workbench_contracts)

    def request(payload: JsonObject) -> WorkbenchRequest:
        return WorkbenchRequest(payload=payload)

    routes["export"](request({"workspace_id": "workspace-a", "vault_id": "vault-a"}))
    note = next((tmp_path / "Notes" / "node").glob("*alpha*.md"))
    text = note.read_text(encoding="utf-8").replace("Original body.", "Edited through Obsidian.")
    start = text.index("<!-- KOGWISTAR-RELATIONS-START -->")
    end = text.index("<!-- KOGWISTAR-RELATIONS-END -->")
    relations = json.loads(text[start + len("<!-- KOGWISTAR-RELATIONS-START -->") : end].strip())
    relations[0]["role"] = "context"
    relations[0]["digest"] = canonical_digest(
        {key: value for key, value in relations[0].items() if key != "digest"}
    )
    rendered_relations = json.dumps(relations, ensure_ascii=False, sort_keys=True, indent=2)
    note.write_text(
        text[: start + len("<!-- KOGWISTAR-RELATIONS-START -->")]
        + "\n"
        + rendered_relations
        + "\n"
        + text[end:],
        encoding="utf-8",
    )

    scan_response = routes["scan"](request({"workspace_id": "workspace-a", "vault_id": "vault-a"}))
    assert scan_response.body["valid"] is True
    plan_response = routes["plan-import"](request({"workspace_id": "workspace-a", "vault_id": "vault-a"}))
    plan_value = plan_response.body.get("plan_id")
    assert isinstance(plan_value, str)
    plan_id = plan_value
    apply_response = routes["apply-import"](
        request(
            {
                "workspace_id": "workspace-a",
                "vault_id": "vault-a",
                "plan_id": plan_id,
                "deletion_mode": "ignore",
            }
        )
    )

    assert apply_response.body == {"status": "applied", "plan_id": plan_id}
    updated = next(entity for entity in host.snapshot.entities if entity.entity_id == "node:alpha")
    assert updated.body == "Edited through Obsidian."
    assert host.snapshot.relations[0].role == "context"

    host.export_snapshot("workspace-a", "vault-a")
    final_plan = plan_import(host.snapshot, scan_vault(tmp_path))
    assert final_plan.applyable is False
    assert final_plan.intents == ()
    assert final_plan.conflicts == ()


def test_plugin_apply_rejects_a_stale_revision_at_the_host_boundary(
    tmp_path: Path, source_workbench_contracts: None
) -> None:
    host = _GraphHost(tmp_path)
    routes = _routes(host, source_workbench_contracts)

    def request(payload: JsonObject) -> WorkbenchRequest:
        return WorkbenchRequest(payload=payload)
    routes["export"](request({"workspace_id": "workspace-a", "vault_id": "vault-a"}))
    note = next((tmp_path / "Notes" / "node").glob("*alpha*.md"))
    note.write_text(note.read_text(encoding="utf-8").replace("Original body.", "Edited."), encoding="utf-8")
    plan_response = routes["plan-import"](request({"workspace_id": "workspace-a", "vault_id": "vault-a"}))
    plan_value = plan_response.body.get("plan_id")
    assert isinstance(plan_value, str)
    plan_id = plan_value
    host.snapshot = replace(
        host.snapshot,
        entities=(replace(host.snapshot.entities[0], revision="alpha-rev-concurrent"), host.snapshot.entities[1]),
    )

    response = routes["apply-import"](
        request(
            {
                "workspace_id": "workspace-a",
                "vault_id": "vault-a",
                "plan_id": plan_id,
                "deletion_mode": "ignore",
            }
        )
    )

    assert response.body == {"status": "rejected", "reason": "stale revision"}
