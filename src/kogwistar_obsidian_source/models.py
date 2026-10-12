"""Typed v1 managed-vault models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, TypeAlias

JsonPrimitive: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = JsonPrimitive | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]

FORMAT_VERSION = "kogwistar-obsidian-source/v1"
EntityKind: TypeAlias = Literal["node", "edge"]
DeletionMode: TypeAlias = Literal["ignore", "tombstone"]


def _required(value: str, name: str) -> str:
    if not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _empty_object() -> JsonObject:
    return {}


def _validate_sha(value: str, name: str) -> str:
    value = _required(value, name)
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return value


@dataclass(frozen=True, slots=True)
class ManagedRelationRecord:
    edge_id: str
    source_entity_id: str
    target_entity_id: str
    relation_type: str
    role: str
    properties: JsonObject = field(default_factory=_empty_object)
    digest: str = ""

    def __post_init__(self) -> None:
        for value, name in (
            (self.edge_id, "edge_id"),
            (self.source_entity_id, "source_entity_id"),
            (self.target_entity_id, "target_entity_id"),
            (self.relation_type, "relation_type"),
            (self.role, "role"),
        ):
            _required(value, name)
        if self.source_entity_id == self.target_entity_id:
            raise ValueError("relation endpoints must be distinct")

    def payload(self) -> JsonObject:
        return {
            "edge_id": self.edge_id,
            "source_entity_id": self.source_entity_id,
            "target_entity_id": self.target_entity_id,
            "relation_type": self.relation_type,
            "role": self.role,
            "properties": self.properties,
        }


@dataclass(frozen=True, slots=True)
class ManagedNoteRecord:
    workspace_id: str
    entity_id: str
    entity_kind: EntityKind
    title: str
    body: str
    properties: JsonObject
    base_revision: str
    base_digest: str
    file_digest: str
    format_version: str = FORMAT_VERSION
    relations: tuple[ManagedRelationRecord, ...] = ()
    observed_file_digest: str = ""

    def __post_init__(self) -> None:
        _required(self.workspace_id, "workspace_id")
        _required(self.entity_id, "entity_id")
        _required(self.title, "title")
        _required(self.base_revision, "base_revision")
        _validate_sha(self.base_digest, "base_digest")
        _validate_sha(self.file_digest, "file_digest")
        if self.observed_file_digest:
            _validate_sha(self.observed_file_digest, "observed_file_digest")
        if self.entity_kind not in {"node", "edge"}:
            raise ValueError("entity_kind must be node or edge")
        if self.format_version != FORMAT_VERSION:
            raise ValueError(f"unsupported format version: {self.format_version}")


@dataclass(frozen=True, slots=True)
class GraphEntityRecord:
    entity_id: str
    entity_kind: EntityKind
    title: str
    body: str
    properties: JsonObject
    revision: str
    payload: JsonObject = field(default_factory=_empty_object)

    def __post_init__(self) -> None:
        _required(self.entity_id, "entity_id")
        _required(self.title, "title")
        _required(self.revision, "revision")
        if self.entity_kind not in {"node", "edge"}:
            raise ValueError("entity_kind must be node or edge")


@dataclass(frozen=True, slots=True)
class GraphSnapshot:
    workspace_id: str
    vault_id: str
    graph_revision: str
    entities: tuple[GraphEntityRecord, ...]
    relations: tuple[ManagedRelationRecord, ...] = ()

    def __post_init__(self) -> None:
        _required(self.workspace_id, "workspace_id")
        _required(self.vault_id, "vault_id")
        _required(self.graph_revision, "graph_revision")
        ids = [entity.entity_id for entity in self.entities]
        if len(ids) != len(set(ids)):
            raise ValueError("graph snapshot contains duplicate entity IDs")
        entity_ids = set(ids)
        edge_ids = [relation.edge_id for relation in self.relations]
        if len(edge_ids) != len(set(edge_ids)):
            raise ValueError("graph snapshot contains duplicate relation IDs")
        if any(
            relation.source_entity_id not in entity_ids or relation.target_entity_id not in entity_ids
            for relation in self.relations
        ):
            raise ValueError("graph snapshot contains a relation with an unknown endpoint")


@dataclass(frozen=True, slots=True)
class VaultManifestEntry:
    path: str
    entity_id: str
    entity_kind: EntityKind
    base_revision: str
    base_digest: str
    file_digest: str
    payload: JsonObject = field(default_factory=_empty_object)

    def __post_init__(self) -> None:
        _required(self.path, "entry path")
        _required(self.entity_id, "entry entity_id")
        _required(self.base_revision, "entry base_revision")
        _validate_sha(self.base_digest, "entry base_digest")
        _validate_sha(self.file_digest, "entry file_digest")
        if self.entity_kind not in {"node", "edge"}:
            raise ValueError("entry entity_kind must be node or edge")


@dataclass(frozen=True, slots=True)
class VaultManifest:
    workspace_id: str
    vault_id: str
    graph_revision: str
    entries: tuple[VaultManifestEntry, ...]
    format_version: str = FORMAT_VERSION
    digest: str = ""

    def __post_init__(self) -> None:
        _required(self.workspace_id, "manifest workspace_id")
        _required(self.vault_id, "manifest vault_id")
        _required(self.graph_revision, "manifest graph_revision")
        if self.format_version != FORMAT_VERSION:
            raise ValueError(f"unsupported manifest format version: {self.format_version}")
        if self.digest:
            _validate_sha(self.digest, "manifest digest")


@dataclass(frozen=True, slots=True)
class VaultImportScan:
    manifest: VaultManifest | None
    notes: tuple[ManagedNoteRecord, ...] = ()
    missing_paths: tuple[str, ...] = ()
    unmanaged_paths: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    @property
    def valid(self) -> bool:
        return self.manifest is not None and not self.errors


@dataclass(frozen=True, slots=True)
class VaultConflict:
    entity_id: str
    reason: Literal["graph_changed", "vault_changed", "both_changed", "invalid"]
    message: str


@dataclass(frozen=True, slots=True)
class SyncIntent:
    entity_id: str
    entity_kind: EntityKind
    title: str
    body: str
    properties: JsonObject
    relations: tuple[ManagedRelationRecord, ...]
    expected_revision: str


@dataclass(frozen=True, slots=True)
class VaultSyncPlan:
    plan_id: str
    workspace_id: str
    vault_id: str
    deletion_mode: DeletionMode
    intents: tuple[SyncIntent, ...]
    tombstone_ids: tuple[str, ...] = ()
    conflicts: tuple[VaultConflict, ...] = ()

    @property
    def applyable(self) -> bool:
        return bool(self.intents or self.tombstone_ids) and not self.conflicts


@dataclass(frozen=True, slots=True)
class VaultSyncResult:
    plan_id: str
    status: Literal["applied", "rejected", "conflict", "dry_run"]
    applied_ids: tuple[str, ...] = ()
    conflicts: tuple[VaultConflict, ...] = ()

