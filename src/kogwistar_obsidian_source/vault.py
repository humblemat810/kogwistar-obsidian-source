"""Read and write the deterministic managed Obsidian vault format."""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from .canonical import canonical_digest
from .models import (
    FORMAT_VERSION,
    GraphEntityRecord,
    GraphSnapshot,
    EntityKind,
    JsonObject,
    ManagedNoteRecord,
    ManagedRelationRecord,
    VaultImportScan,
    VaultManifest,
    VaultManifestEntry,
)

MANIFEST_RELATIVE_PATH = Path(".kogwistar") / "obsidian-source" / "manifest.json"
_RELATION_START = "<!-- KOGWISTAR-RELATIONS-START -->"
_RELATION_END = "<!-- KOGWISTAR-RELATIONS-END -->"
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._ -]+")


class VaultFormatError(ValueError):
    """Raised when a managed vault violates the v1 contract."""


class ManagedNoteMissing(VaultFormatError):
    """Raised only when a manifest-owned managed note was deleted."""


def _json_object(value: object, name: str) -> JsonObject:
    if not isinstance(value, dict):
        raise VaultFormatError(f"{name} must be a JSON object")
    typed = cast(dict[str, object], value)
    return cast(JsonObject, typed)


def _json_list(value: object, name: str) -> list[JsonObject]:
    if not isinstance(value, list):
        raise VaultFormatError(f"{name} must be a JSON array")
    return [_json_object(item, name) for item in cast(list[object], value)]


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise VaultFormatError(f"{name} must be a non-empty string")
    return value


def _entity_payload(entity: GraphEntityRecord, relations: Sequence[ManagedRelationRecord]) -> JsonObject:
    return {
        "entity_id": entity.entity_id,
        "entity_kind": entity.entity_kind,
        "title": entity.title,
        "body": entity.body,
        "properties": entity.properties,
        "payload": entity.payload,
        "relations": [relation.payload() for relation in relations],
    }


def entity_digest(entity: GraphEntityRecord, relations: Sequence[ManagedRelationRecord]) -> str:
    return canonical_digest(_entity_payload(entity, relations))


def _safe_note_path(entity: GraphEntityRecord) -> str:
    return _safe_note_path_values(entity.entity_id, entity.entity_kind, entity.title)


def _safe_note_path_values(entity_id: str, entity_kind: EntityKind, title: str) -> str:
    safe_title = _SAFE_NAME.sub("", title).strip(" .") or "untitled"
    # Keep paths portable on Windows and below common filesystem path limits.
    if safe_title.upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{index}" for index in range(1, 10)), *(f"LPT{index}" for index in range(1, 10))}:
        safe_title = "untitled"
    safe_title = safe_title[:96].rstrip(" .") or "untitled"
    suffix = canonical_digest({"id": entity_id})[:12]
    return f"Notes/{entity_kind}/{safe_title}--{suffix}.md"


def _is_canonical_ledger_path(relative_path: str, entity_id: str, entity_kind: EntityKind) -> bool:
    """Validate a stable path without coupling it to an editable current title."""
    relative = Path(relative_path)
    if relative_path != relative.as_posix() or len(relative.parts) != 3:
        return False
    if relative.parts[:2] != ("Notes", entity_kind):
        return False
    suffix = f"--{canonical_digest({'id': entity_id})[:12]}.md"
    label = relative.name.removesuffix(suffix)
    return (
        relative.name.endswith(suffix)
        and bool(label)
        and _SAFE_NAME.sub("", label) == label
        and label == label.strip(" .")
    )


def _relation_from_json(value: JsonObject) -> ManagedRelationRecord:
    relation = ManagedRelationRecord(
        edge_id=_text(value.get("edge_id"), "edge_id"),
        source_entity_id=_text(value.get("source_entity_id"), "source_entity_id"),
        target_entity_id=_text(value.get("target_entity_id"), "target_entity_id"),
        relation_type=_text(value.get("relation_type"), "relation_type"),
        role=_text(value.get("role"), "role"),
        properties=_json_object(value.get("properties", {}), "relation properties"),
        digest=_text(value.get("digest"), "relation digest"),
    )
    expected = canonical_digest(relation.payload())
    if relation.digest != expected:
        raise VaultFormatError(f"invalid relation digest: {relation.edge_id}")
    return ManagedRelationRecord(
        edge_id=relation.edge_id,
        source_entity_id=relation.source_entity_id,
        target_entity_id=relation.target_entity_id,
        relation_type=relation.relation_type,
        role=relation.role,
        properties=relation.properties,
        digest=expected,
    )


def _relation_json(relation: ManagedRelationRecord) -> JsonObject:
    payload = relation.payload()
    payload["digest"] = canonical_digest(payload)
    return payload


def _note_payload(note: ManagedNoteRecord) -> JsonObject:
    return {
        "workspace_id": note.workspace_id,
        "entity_id": note.entity_id,
        "entity_kind": note.entity_kind,
        "title": note.title,
        "body": note.body,
        "properties": note.properties,
        "base_revision": note.base_revision,
        "base_digest": note.base_digest,
        "format_version": note.format_version,
        "relations": [_relation_json(relation) for relation in note.relations],
    }


def _note_file_digest(note: ManagedNoteRecord) -> str:
    return canonical_digest(_note_payload(note))


def _render_note(note: ManagedNoteRecord) -> str:
    if _RELATION_START in note.body or _RELATION_END in note.body:
        raise VaultFormatError("managed note body cannot contain canonical relation markers")
    frontmatter: JsonObject = {
        "format_version": note.format_version,
        "workspace_id": note.workspace_id,
        "entity_id": note.entity_id,
        "entity_kind": note.entity_kind,
        "title": note.title,
        "base_revision": note.base_revision,
        "base_digest": note.base_digest,
        "file_digest": note.file_digest,
        "properties": note.properties,
    }
    relations = [_relation_json(relation) for relation in note.relations]
    return (
        "---\n"
        + json.dumps(frontmatter, ensure_ascii=False, sort_keys=True, indent=2)
        + "\n---\n\n"
        + note.body.rstrip()
        + "\n\n"
        + _RELATION_START
        + "\n"
        + json.dumps(relations, ensure_ascii=False, sort_keys=True, indent=2)
        + "\n"
        + _RELATION_END
        + "\n"
    )


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _manifest_payload(manifest: VaultManifest) -> JsonObject:
    return {
        "format_version": manifest.format_version,
        "workspace_id": manifest.workspace_id,
        "vault_id": manifest.vault_id,
        "graph_revision": manifest.graph_revision,
        "entries": [
            {
                "path": entry.path,
                "entity_id": entry.entity_id,
                "entity_kind": entry.entity_kind,
                "base_revision": entry.base_revision,
                "base_digest": entry.base_digest,
                "file_digest": entry.file_digest,
                "payload": entry.payload,
            }
            for entry in manifest.entries
        ],
    }


def _manifest_json(manifest: VaultManifest) -> JsonObject:
    payload = _manifest_payload(manifest)
    payload["digest"] = canonical_digest(payload)
    return payload


def write_snapshot(snapshot: GraphSnapshot, vault_root: str | Path) -> VaultManifest:
    """Materialize one graph snapshot without deleting unmanaged vault files."""
    root = Path(vault_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    existing_entries: dict[str, VaultManifestEntry] = {}
    manifest_path = root / MANIFEST_RELATIVE_PATH
    if manifest_path.exists() or manifest_path.is_symlink():
        _ensure_safe_write_path(root, MANIFEST_RELATIVE_PATH.as_posix())
        existing_manifest = _load_manifest(root)
        if (
            existing_manifest.workspace_id != snapshot.workspace_id
            or existing_manifest.vault_id != snapshot.vault_id
        ):
            raise VaultFormatError("existing managed vault identity does not match graph snapshot")
        existing_entries = {entry.entity_id: entry for entry in existing_manifest.entries}
    grouped: dict[str, list[ManagedRelationRecord]] = {}
    for relation in snapshot.relations:
        grouped.setdefault(relation.source_entity_id, []).append(relation)

    entries: list[VaultManifestEntry] = []
    for entity in sorted(snapshot.entities, key=lambda item: item.entity_id):
        relations = tuple(sorted(grouped.get(entity.entity_id, ()), key=lambda item: item.edge_id))
        base_digest = entity_digest(entity, relations)
        note = ManagedNoteRecord(
            workspace_id=snapshot.workspace_id,
            entity_id=entity.entity_id,
            entity_kind=entity.entity_kind,
            title=entity.title,
            body=entity.body,
            properties=entity.properties,
            base_revision=entity.revision,
            base_digest=base_digest,
            file_digest="0" * 64,
            relations=relations,
        )
        file_digest = _note_file_digest(note)
        note = ManagedNoteRecord(
            workspace_id=note.workspace_id,
            entity_id=note.entity_id,
            entity_kind=note.entity_kind,
            title=note.title,
            body=note.body,
            properties=note.properties,
            base_revision=note.base_revision,
            base_digest=note.base_digest,
            file_digest=file_digest,
            relations=note.relations,
        )
        existing_entry = existing_entries.get(entity.entity_id)
        if existing_entry is not None:
            if existing_entry.entity_kind != entity.entity_kind or not _is_canonical_ledger_path(
                existing_entry.path, entity.entity_id, entity.entity_kind
            ):
                raise VaultFormatError(f"existing managed path is invalid: {existing_entry.path}")
            relative_path = existing_entry.path
        else:
            relative_path = _safe_note_path(entity)
        _ensure_safe_write_path(root, relative_path)
        target = root / relative_path
        if target.exists() and existing_entry is None:
            raise VaultFormatError(f"refusing to overwrite unmanaged vault file: {relative_path}")
        _atomic_write(target, _render_note(note))
        entries.append(
            VaultManifestEntry(
                path=relative_path,
                entity_id=entity.entity_id,
                entity_kind=entity.entity_kind,
                base_revision=entity.revision,
                base_digest=base_digest,
                file_digest=file_digest,
                payload=_entity_payload(entity, relations),
            )
        )

    manifest = VaultManifest(
        workspace_id=snapshot.workspace_id,
        vault_id=snapshot.vault_id,
        graph_revision=snapshot.graph_revision,
        entries=tuple(entries),
    )
    _ensure_safe_write_path(root, MANIFEST_RELATIVE_PATH.as_posix())
    _atomic_write(
        root / MANIFEST_RELATIVE_PATH,
        json.dumps(_manifest_json(manifest), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
    )
    return VaultManifest(
        workspace_id=manifest.workspace_id,
        vault_id=manifest.vault_id,
        graph_revision=manifest.graph_revision,
        entries=manifest.entries,
        format_version=manifest.format_version,
        digest=canonical_digest(_manifest_payload(manifest)),
    )


def _load_manifest(root: Path) -> VaultManifest:
    path = root / MANIFEST_RELATIVE_PATH
    raw = _json_object(json.loads(path.read_text(encoding="utf-8")), "manifest")
    if raw.get("format_version") != FORMAT_VERSION:
        raise VaultFormatError("unsupported manifest format version")
    entries: list[VaultManifestEntry] = []
    for item in _json_list(raw.get("entries"), "manifest entries"):
        entries.append(
            VaultManifestEntry(
                path=_text(item.get("path"), "entry path"),
                entity_id=_text(item.get("entity_id"), "entry entity_id"),
                entity_kind=cast(EntityKind, _text(item.get("entity_kind"), "entry entity_kind")),
                base_revision=_text(item.get("base_revision"), "entry base_revision"),
                base_digest=_text(item.get("base_digest"), "entry base_digest"),
                file_digest=_text(item.get("file_digest"), "entry file_digest"),
                payload=_json_object(item.get("payload", {}), "entry payload"),
            )
        )
    manifest = VaultManifest(
        workspace_id=_text(raw.get("workspace_id"), "manifest workspace_id"),
        vault_id=_text(raw.get("vault_id"), "manifest vault_id"),
        graph_revision=_text(raw.get("graph_revision"), "manifest graph_revision"),
        entries=tuple(entries),
        format_version=FORMAT_VERSION,
        digest=_text(raw.get("digest"), "manifest digest"),
    )
    if manifest.digest != canonical_digest(_manifest_payload(manifest)):
        raise VaultFormatError("manifest digest does not match content")
    if len({entry.path for entry in entries}) != len(entries):
        raise VaultFormatError("manifest contains duplicate paths")
    if len({entry.entity_id for entry in entries}) != len(entries):
        raise VaultFormatError("manifest contains duplicate entity IDs")
    return manifest


def _safe_path(root: Path, relative_path: str) -> Path:
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise VaultFormatError(f"managed path is not canonical: {relative_path}")
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise VaultFormatError(f"managed path uses a symlink: {relative_path}")
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise VaultFormatError(f"managed path escapes vault root: {relative_path}") from exc
    if not candidate.is_file():
        raise ManagedNoteMissing(f"managed note does not exist: {relative_path}")
    return candidate


def _ensure_safe_write_path(root: Path, relative_path: str) -> None:
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise VaultFormatError(f"managed path is not canonical: {relative_path}")
    current = root
    for part in relative.parts[:-1]:
        current /= part
        if current.is_symlink():
            raise VaultFormatError(f"managed path uses a symlink: {relative_path}")
    if (root / relative_path).is_symlink():
        raise VaultFormatError(f"managed path uses a symlink: {relative_path}")


def _parse_note(path: Path, manifest_entry: VaultManifestEntry) -> ManagedNoteRecord:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise VaultFormatError(f"managed note lacks frontmatter: {path}")
    separator = text.find("\n---\n", 4)
    if separator < 0:
        raise VaultFormatError(f"managed note has invalid frontmatter: {path}")
    frontmatter = _json_object(json.loads(text[4:separator]), "note frontmatter")
    remainder = text[separator + len("\n---\n"):]
    if remainder.count(_RELATION_START) != 1 or remainder.count(_RELATION_END) != 1:
        raise VaultFormatError(f"managed note has ambiguous canonical relation blocks: {path}")
    start = remainder.find(_RELATION_START)
    end = remainder.find(_RELATION_END)
    if start < 0 or end < start:
        raise VaultFormatError(f"managed note lacks canonical relation block: {path}")
    body = remainder[:start].lstrip("\r\n").rstrip()
    try:
        relations_payload = json.loads(remainder[start + len(_RELATION_START):end].strip())
    except json.JSONDecodeError as exc:
        raise VaultFormatError(f"note relations are not valid JSON: {path}") from exc
    relations_raw = _json_list(relations_payload, "note relations")
    relations = tuple(_relation_from_json(item) for item in relations_raw)
    note = ManagedNoteRecord(
        workspace_id=_text(frontmatter.get("workspace_id"), "note workspace_id"),
        entity_id=_text(frontmatter.get("entity_id"), "note entity_id"),
        entity_kind=cast(EntityKind, _text(frontmatter.get("entity_kind"), "note entity_kind")),
        title=_text(frontmatter.get("title"), "note title"),
        body=body,
        properties=_json_object(frontmatter.get("properties", {}), "note properties"),
        base_revision=_text(frontmatter.get("base_revision"), "note base_revision"),
        base_digest=_text(frontmatter.get("base_digest"), "note base_digest"),
        file_digest=_text(frontmatter.get("file_digest"), "note file_digest"),
        format_version=_text(frontmatter.get("format_version"), "note format_version"),
        relations=relations,
        observed_file_digest="0" * 64,
    )
    if (
        note.entity_id != manifest_entry.entity_id
        or note.entity_kind != manifest_entry.entity_kind
        or note.base_revision != manifest_entry.base_revision
        or note.base_digest != manifest_entry.base_digest
        or note.file_digest != manifest_entry.file_digest
    ):
        raise VaultFormatError(f"manifest and note identity disagree: {path}")
    if any(relation.source_entity_id != note.entity_id for relation in note.relations):
        raise VaultFormatError(f"note relation source does not match note identity: {path}")
    return ManagedNoteRecord(
        workspace_id=note.workspace_id,
        entity_id=note.entity_id,
        entity_kind=note.entity_kind,
        title=note.title,
        body=note.body,
        properties=note.properties,
        base_revision=note.base_revision,
        base_digest=note.base_digest,
        file_digest=note.file_digest,
        format_version=note.format_version,
        relations=note.relations,
        observed_file_digest=_note_file_digest(note),
    )


def scan_vault(vault_root: str | Path) -> VaultImportScan:
    """Read only manifest-owned files and return bounded diagnostics."""
    root = Path(vault_root).expanduser().resolve()
    try:
        manifest = _load_manifest(root)
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        return VaultImportScan(manifest=None, errors=(str(exc),))
    notes: list[ManagedNoteRecord] = []
    missing: list[str] = []
    errors: list[str] = []
    for entry in manifest.entries:
        try:
            note = _parse_note(_safe_path(root, entry.path), entry)
            if not _is_canonical_ledger_path(entry.path, note.entity_id, note.entity_kind):
                raise VaultFormatError(f"managed note path is not canonical: {entry.path}")
            notes.append(note)
        except ManagedNoteMissing:
            missing.append(entry.path)
        except (OSError, TypeError, ValueError) as exc:
            errors.append(str(exc))
    managed_paths = {entry.path for entry in manifest.entries}
    unmanaged = tuple(
        sorted(
            path.relative_to(root).as_posix()
            for path in root.rglob("*.md")
            if path.is_file() and path.relative_to(root).as_posix() not in managed_paths
        )
    )
    return VaultImportScan(
        manifest=manifest,
        notes=tuple(sorted(notes, key=lambda item: item.entity_id)),
        missing_paths=tuple(sorted(missing)),
        unmanaged_paths=unmanaged,
        errors=tuple(sorted(errors)),
    )

