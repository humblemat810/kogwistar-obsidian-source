"""Small explicit CLI for managed-vault export, scan, and planning."""

from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path
from typing import Protocol, cast

from .models import EntityKind, GraphEntityRecord, GraphSnapshot, JsonObject, ManagedRelationRecord
from .sync import plan_import, plan_to_json
from .vault import scan_vault, write_snapshot


class _ApplyHost(Protocol):
    def apply_import(
        self, workspace_id: str, vault_id: str, plan_id: str, deletion_mode: str, /
    ) -> JsonObject: ...


def _object(value: object, name: str) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return cast(JsonObject, value)


def _snapshot(path: str | Path) -> GraphSnapshot:
    raw = _object(json.loads(Path(path).read_text(encoding="utf-8")), "snapshot")
    entities: list[GraphEntityRecord] = []
    raw_entities = raw.get("entities", [])
    if not isinstance(raw_entities, list):
        raise ValueError("snapshot entities must be an array")
    for item in raw_entities:
        entity = _object(item, "entity")
        entities.append(
            GraphEntityRecord(
                entity_id=str(entity["entity_id"]),
                entity_kind=cast(EntityKind, str(entity["entity_kind"])),
                title=str(entity["title"]),
                body=str(entity.get("body", "")),
                properties=_object(entity.get("properties", {}), "entity properties"),
                revision=str(entity["revision"]),
                payload=_object(entity.get("payload", {}), "entity payload"),
            )
        )
    relations: list[ManagedRelationRecord] = []
    raw_relations = raw.get("relations", [])
    if not isinstance(raw_relations, list):
        raise ValueError("snapshot relations must be an array")
    for item in raw_relations:
        relation = _object(item, "relation")
        relations.append(
            ManagedRelationRecord(
                edge_id=str(relation["edge_id"]),
                source_entity_id=str(relation["source_entity_id"]),
                target_entity_id=str(relation["target_entity_id"]),
                relation_type=str(relation["relation_type"]),
                role=str(relation["role"]),
                properties=_object(relation.get("properties", {}), "relation properties"),
            )
        )
    return GraphSnapshot(
        workspace_id=str(raw["workspace_id"]),
        vault_id=str(raw["vault_id"]),
        graph_revision=str(raw["graph_revision"]),
        entities=tuple(entities),
        relations=tuple(relations),
    )


def main() -> int:
    parser = argparse.ArgumentParser(prog="kogwistar-obsidian-source")
    commands = parser.add_subparsers(dest="command", required=True)

    export = commands.add_parser("export")
    export.add_argument("--snapshot", required=True)
    export.add_argument("--vault", required=True)

    scan = commands.add_parser("scan")
    scan.add_argument("--vault", required=True)

    plan = commands.add_parser("plan-import")
    plan.add_argument("--snapshot", required=True)
    plan.add_argument("--vault", required=True)
    plan.add_argument("--deletion-mode", choices=("ignore", "tombstone"), default="ignore")
    plan.add_argument("--output", default="-")

    apply = commands.add_parser("apply-import")
    apply.add_argument("--plan", required=True)
    apply.add_argument("--host", required=True, help="trusted module:factory returning an LLM-Wiki host")
    apply.add_argument("--deletion-mode", choices=("ignore", "tombstone"), default="ignore")

    conflicts = commands.add_parser("conflicts")
    conflicts.add_argument("--plan", required=True)

    args = parser.parse_args()
    if args.command == "export":
        manifest = write_snapshot(_snapshot(args.snapshot), args.vault)
        print(json.dumps({"status": "exported", "digest": manifest.digest}, indent=2))
        return 0
    if args.command == "scan":
        result = scan_vault(args.vault)
        print(json.dumps({
            "valid": result.valid,
            "missing_paths": list(result.missing_paths),
            "unmanaged_paths": list(result.unmanaged_paths),
            "errors": list(result.errors),
        }, indent=2))
        return 0 if result.valid else 2
    if args.command == "plan-import":
        snapshot = _snapshot(args.snapshot)
        result = plan_import(snapshot, scan_vault(args.vault), deletion_mode=args.deletion_mode)
        encoded = json.dumps(plan_to_json(result), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        if args.output == "-":
            print(encoded, end="")
        else:
            Path(args.output).write_text(encoded, encoding="utf-8", newline="\n")
        return 0 if result.applyable else 3

    raw_plan = _object(json.loads(Path(args.plan).read_text(encoding="utf-8")), "plan")
    if args.command == "conflicts":
        print(json.dumps({"plan_id": raw_plan.get("plan_id"), "conflicts": raw_plan.get("conflicts", [])}, indent=2))
        return 0 if not raw_plan.get("conflicts") else 3

    module_name, separator, attribute = args.host.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError("--host must use module:factory")
    factory = getattr(importlib.import_module(module_name), attribute, None)
    if not callable(factory):
        raise TypeError("--host factory is not callable")
    host = cast(_ApplyHost, factory())
    deletion_mode = args.deletion_mode
    plan_id = str(raw_plan.get("plan_id") or "").strip()
    if not plan_id:
        raise ValueError("the import plan must contain an explicit plan_id")
    encoded_mode = raw_plan.get("deletion_mode")
    if encoded_mode != deletion_mode:
        raise ValueError("--deletion-mode must match the persisted import plan")
    if raw_plan.get("conflicts"):
        print(json.dumps({"status": "conflict", "plan_id": plan_id}, indent=2))
        return 3
    if not raw_plan.get("intents") and not raw_plan.get("tombstone_ids"):
        raise ValueError("the import plan has no mutations to apply")
    result = host.apply_import(
        str(raw_plan.get("workspace_id") or ""),
        str(raw_plan.get("vault_id") or ""),
        plan_id,
        deletion_mode,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0

