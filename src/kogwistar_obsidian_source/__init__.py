"""Bidirectional managed-vault contracts for the optional Obsidian plugin."""

from .canonical import canonical_digest, canonical_json
from .models import (
    FORMAT_VERSION,
    GraphEntityRecord,
    GraphSnapshot,
    ManagedNoteRecord,
    ManagedRelationRecord,
    SyncIntent,
    VaultManifestEntry,
    VaultConflict,
    VaultImportScan,
    VaultManifest,
    VaultSyncPlan,
    VaultSyncResult,
)
from .sync import plan_import, plan_to_json
from .vault import VaultFormatError, entity_digest, scan_vault, write_snapshot

__all__ = [
    "FORMAT_VERSION",
    "GraphEntityRecord",
    "GraphSnapshot",
    "ManagedNoteRecord",
    "ManagedRelationRecord",
    "SyncIntent",
    "VaultConflict",
    "VaultImportScan",
    "VaultManifest",
    "VaultManifestEntry",
    "VaultSyncPlan",
    "VaultSyncResult",
    "canonical_digest",
    "canonical_json",
    "plan_import",
    "plan_to_json",
    "VaultFormatError",
    "entity_digest",
    "scan_vault",
    "write_snapshot",
]
