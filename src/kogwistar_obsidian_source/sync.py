"""Conflict-aware planning between managed vaults and graph snapshots."""

from __future__ import annotations

from .canonical import canonical_digest
from .models import (
    DeletionMode,
    GraphSnapshot,
    JsonObject,
    ManagedRelationRecord,
    SyncIntent,
    VaultConflict,
    VaultImportScan,
    VaultSyncPlan,
)
from .vault import entity_digest


def _relations_by_source(snapshot: GraphSnapshot) -> dict[str, tuple[ManagedRelationRecord, ...]]:
    grouped: dict[str, list[ManagedRelationRecord]] = {}
    for relation in snapshot.relations:
        grouped.setdefault(relation.source_entity_id, []).append(relation)
    return {
        entity_id: tuple(sorted(relations, key=lambda item: item.edge_id))
        for entity_id, relations in grouped.items()
    }


def _plan_payload(plan: VaultSyncPlan) -> JsonObject:
    return {
        "workspace_id": plan.workspace_id,
        "vault_id": plan.vault_id,
        "deletion_mode": plan.deletion_mode,
        "intents": [
            {
                "entity_id": intent.entity_id,
                "entity_kind": intent.entity_kind,
                "title": intent.title,
                "body": intent.body,
                "properties": intent.properties,
                "relations": [relation.payload() for relation in intent.relations],
                "expected_revision": intent.expected_revision,
            }
            for intent in plan.intents
        ],
        "tombstone_ids": list(plan.tombstone_ids),
        "conflicts": [
            {
                "entity_id": conflict.entity_id,
                "reason": conflict.reason,
                "message": conflict.message,
            }
            for conflict in plan.conflicts
        ],
    }


def plan_to_json(plan: VaultSyncPlan) -> JsonObject:
    payload = _plan_payload(plan)
    payload["plan_id"] = plan.plan_id
    return payload


def plan_import(
    snapshot: GraphSnapshot,
    scan: VaultImportScan,
    *,
    deletion_mode: DeletionMode = "ignore",
) -> VaultSyncPlan:
    """Build a read-only, revision-fenced import plan."""
    if deletion_mode not in {"ignore", "tombstone"}:
        raise ValueError("deletion_mode must be ignore or tombstone")
    conflicts: list[VaultConflict] = []
    intents: list[SyncIntent] = []
    tombstones: list[str] = []
    if not scan.valid or scan.manifest is None:
        conflicts.append(
            VaultConflict(
                entity_id="",
                reason="invalid",
                message="vault scan is invalid; no graph mutation is permitted",
            )
        )
    elif scan.manifest.workspace_id != snapshot.workspace_id or scan.manifest.vault_id != snapshot.vault_id:
        conflicts.append(
            VaultConflict(
                entity_id="",
                reason="invalid",
                message="vault identity does not match graph snapshot",
            )
        )
    current = {entity.entity_id: entity for entity in snapshot.entities}
    relations = _relations_by_source(snapshot)
    current_ids = set(current)
    entries = {entry.entity_id: entry for entry in (scan.manifest.entries if scan.manifest else ())}
    seen_relation_ids: set[str] = set()
    for note in scan.notes:
        entity = current.get(note.entity_id)
        entry = entries.get(note.entity_id)
        if entity is None or entry is None:
            conflicts.append(
                VaultConflict(note.entity_id, "invalid", "managed entity is absent from the graph snapshot")
            )
            continue
        if note.workspace_id != snapshot.workspace_id:
            conflicts.append(
                VaultConflict(note.entity_id, "invalid", "managed note workspace does not match the graph snapshot")
            )
            continue
        if note.entity_kind != entity.entity_kind:
            conflicts.append(
                VaultConflict(note.entity_id, "invalid", "managed note entity kind does not match the graph snapshot")
            )
            continue
        invalid_relations = [
            relation.edge_id
            for relation in note.relations
            if relation.source_entity_id not in current_ids or relation.target_entity_id not in current_ids
        ]
        if invalid_relations:
            conflicts.append(
                VaultConflict(
                    note.entity_id,
                    "invalid",
                    "managed relation endpoint is absent from the graph snapshot: "
                    + ", ".join(sorted(invalid_relations)),
                )
            )
            continue
        duplicate_relations = [
            relation.edge_id for relation in note.relations if relation.edge_id in seen_relation_ids
        ]
        if duplicate_relations:
            conflicts.append(
                VaultConflict(
                    note.entity_id,
                    "invalid",
                    "managed relation ID appears in more than one note: "
                    + ", ".join(sorted(duplicate_relations)),
                )
            )
            continue
        seen_relation_ids.update(relation.edge_id for relation in note.relations)
        vault_changed = note.observed_file_digest != entry.file_digest
        graph_changed = entity_digest(entity, relations.get(entity.entity_id, ())) != entry.base_digest
        if graph_changed and vault_changed:
            conflicts.append(
                VaultConflict(
                    note.entity_id,
                    "both_changed",
                    "graph and vault changed after the exported baseline",
                )
            )
        elif vault_changed:
            intents.append(
                SyncIntent(
                    entity_id=note.entity_id,
                    entity_kind=note.entity_kind,
                    title=note.title,
                    body=note.body,
                    properties=note.properties,
                    relations=note.relations,
                    expected_revision=entity.revision,
                )
            )

    missing_ids = set(entries) - {note.entity_id for note in scan.notes}
    if deletion_mode == "tombstone":
        tombstones.extend(sorted(missing_ids))

    provisional = VaultSyncPlan(
        plan_id="0" * 64,
        workspace_id=snapshot.workspace_id,
        vault_id=snapshot.vault_id,
        deletion_mode=deletion_mode,
        intents=tuple(sorted(intents, key=lambda item: item.entity_id)),
        tombstone_ids=tuple(tombstones),
        conflicts=tuple(sorted(conflicts, key=lambda item: (item.entity_id, item.reason))),
    )
    return VaultSyncPlan(
        plan_id=canonical_digest(_plan_payload(provisional)),
        workspace_id=provisional.workspace_id,
        vault_id=provisional.vault_id,
        deletion_mode=provisional.deletion_mode,
        intents=provisional.intents,
        tombstone_ids=provisional.tombstone_ids,
        conflicts=provisional.conflicts,
    )

