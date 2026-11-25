use crate::canonical::canonical_digest;
use crate::models::{
    ConflictReason, DeletionMode, GraphSnapshot, ManagedRelationRecord, SyncIntent, VaultConflict,
    VaultImportScan, VaultSyncPlan,
};
use crate::vault::entity_digest;
use serde_json::{json, Value};
use std::collections::{HashMap, HashSet};

fn relations_by_source(snapshot: &GraphSnapshot) -> HashMap<&str, Vec<ManagedRelationRecord>> {
    let mut grouped: HashMap<&str, Vec<ManagedRelationRecord>> = HashMap::new();
    for relation in &snapshot.relations {
        grouped
            .entry(relation.source_entity_id.as_str())
            .or_default()
            .push(relation.clone());
    }
    for relations in grouped.values_mut() {
        relations.sort_by(|left, right| left.edge_id.cmp(&right.edge_id));
    }
    grouped
}

fn plan_payload(plan: &VaultSyncPlan) -> Value {
    json!({
        "workspace_id": plan.workspace_id,
        "vault_id": plan.vault_id,
        "deletion_mode": plan.deletion_mode,
        "intents": plan.intents.iter().map(|intent| json!({
            "entity_id": intent.entity_id,
            "entity_kind": intent.entity_kind,
            "title": intent.title,
            "body": intent.body,
            "properties": intent.properties,
            "relations": intent.relations.iter().map(ManagedRelationRecord::payload).collect::<Vec<_>>(),
            "expected_revision": intent.expected_revision,
        })).collect::<Vec<_>>(),
        "tombstone_ids": plan.tombstone_ids,
        "conflicts": plan.conflicts.iter().map(|conflict| json!({
            "entity_id": conflict.entity_id,
            "reason": conflict.reason,
            "message": conflict.message,
        })).collect::<Vec<_>>(),
    })
}

pub fn plan_to_json(plan: &VaultSyncPlan) -> Value {
    let mut payload = plan_payload(plan);
    if let Value::Object(map) = &mut payload {
        map.insert("plan_id".to_string(), Value::String(plan.plan_id.clone()));
    }
    payload
}

fn finalize_plan(
    snapshot: &GraphSnapshot,
    deletion_mode: DeletionMode,
    mut intents: Vec<SyncIntent>,
    mut tombstones: Vec<String>,
    mut conflicts: Vec<VaultConflict>,
) -> VaultSyncPlan {
    intents.sort_by(|left, right| left.entity_id.cmp(&right.entity_id));
    tombstones.sort();
    conflicts.sort_by(|left, right| {
        (&left.entity_id, format!("{:?}", left.reason), &left.message).cmp(&(
            &right.entity_id,
            format!("{:?}", right.reason),
            &right.message,
        ))
    });
    let provisional = VaultSyncPlan {
        plan_id: "0".repeat(64),
        workspace_id: snapshot.workspace_id.clone(),
        vault_id: snapshot.vault_id.clone(),
        deletion_mode,
        intents,
        tombstone_ids: tombstones,
        conflicts,
    };
    VaultSyncPlan {
        plan_id: canonical_digest(&plan_payload(&provisional)),
        ..provisional
    }
}

pub fn plan_import(
    snapshot: &GraphSnapshot,
    scan: &VaultImportScan,
    deletion_mode: DeletionMode,
) -> VaultSyncPlan {
    let mut conflicts = Vec::new();
    let manifest = match scan.manifest.as_ref() {
        Some(manifest) if scan.valid() => manifest,
        _ => {
            conflicts.push(VaultConflict {
                entity_id: String::new(),
                reason: ConflictReason::Invalid,
                message: "vault scan is invalid; no graph mutation is permitted".to_string(),
            });
            return finalize_plan(snapshot, deletion_mode, Vec::new(), Vec::new(), conflicts);
        }
    };
    if manifest.workspace_id != snapshot.workspace_id || manifest.vault_id != snapshot.vault_id {
        conflicts.push(VaultConflict {
            entity_id: String::new(),
            reason: ConflictReason::Invalid,
            message: "vault identity does not match graph snapshot".to_string(),
        });
        return finalize_plan(snapshot, deletion_mode, Vec::new(), Vec::new(), conflicts);
    }
    let current: HashMap<&str, _> = snapshot
        .entities
        .iter()
        .map(|entity| (entity.entity_id.as_str(), entity))
        .collect();
    let relations = relations_by_source(snapshot);
    let current_ids: HashSet<&str> = current.keys().copied().collect();
    let entries: HashMap<&str, _> = manifest
        .entries
        .iter()
        .map(|entry| (entry.entity_id.as_str(), entry))
        .collect();
    let mut seen_relation_ids = HashSet::new();
    let mut intents = Vec::new();
    for note in &scan.notes {
        let Some(entity) = current.get(note.entity_id.as_str()) else {
            conflicts.push(VaultConflict {
                entity_id: note.entity_id.clone(),
                reason: ConflictReason::Invalid,
                message: "managed entity is absent from the graph snapshot".to_string(),
            });
            continue;
        };
        let Some(entry) = entries.get(note.entity_id.as_str()) else {
            conflicts.push(VaultConflict {
                entity_id: note.entity_id.clone(),
                reason: ConflictReason::Invalid,
                message: "managed entity is absent from the graph snapshot".to_string(),
            });
            continue;
        };
        if note.workspace_id != snapshot.workspace_id {
            conflicts.push(VaultConflict {
                entity_id: note.entity_id.clone(),
                reason: ConflictReason::Invalid,
                message: "managed note workspace does not match the graph snapshot".to_string(),
            });
            continue;
        }
        if note.entity_kind != entity.entity_kind {
            conflicts.push(VaultConflict {
                entity_id: note.entity_id.clone(),
                reason: ConflictReason::Invalid,
                message: "managed note entity kind does not match the graph snapshot".to_string(),
            });
            continue;
        }
        let mut invalid_relations = note
            .relations
            .iter()
            .filter(|relation| {
                !current_ids.contains(relation.source_entity_id.as_str())
                    || !current_ids.contains(relation.target_entity_id.as_str())
            })
            .map(|relation| relation.edge_id.clone())
            .collect::<Vec<_>>();
        invalid_relations.sort();
        if !invalid_relations.is_empty() {
            conflicts.push(VaultConflict {
                entity_id: note.entity_id.clone(),
                reason: ConflictReason::Invalid,
                message: format!(
                    "managed relation endpoint is absent from the graph snapshot: {}",
                    invalid_relations.join(", ")
                ),
            });
            continue;
        }
        let mut duplicate_relations = note
            .relations
            .iter()
            .filter(|relation| seen_relation_ids.contains(&relation.edge_id))
            .map(|relation| relation.edge_id.clone())
            .collect::<Vec<_>>();
        duplicate_relations.sort();
        if !duplicate_relations.is_empty() {
            conflicts.push(VaultConflict {
                entity_id: note.entity_id.clone(),
                reason: ConflictReason::Invalid,
                message: format!(
                    "managed relation ID appears in more than one note: {}",
                    duplicate_relations.join(", ")
                ),
            });
            continue;
        }
        seen_relation_ids.extend(
            note.relations
                .iter()
                .map(|relation| relation.edge_id.clone()),
        );
        let vault_changed = note.observed_file_digest != entry.file_digest;
        let graph_changed = entity.entity_kind != entry.entity_kind
            || entity.revision != entry.base_revision
            || entity_digest(
                entity,
                relations
                    .get(entity.entity_id.as_str())
                    .map(|v| v.as_slice())
                    .unwrap_or(&[]),
            ) != entry.base_digest;
        if graph_changed && vault_changed {
            conflicts.push(VaultConflict {
                entity_id: note.entity_id.clone(),
                reason: ConflictReason::BothChanged,
                message: "graph and vault changed after the exported baseline".to_string(),
            });
        } else if vault_changed {
            intents.push(SyncIntent {
                entity_id: note.entity_id.clone(),
                entity_kind: note.entity_kind,
                title: note.title.clone(),
                body: note.body.clone(),
                properties: note.properties.clone(),
                relations: note.relations.clone(),
                expected_revision: entity.revision.clone(),
            });
        }
    }
    let note_ids: HashSet<&str> = scan
        .notes
        .iter()
        .map(|note| note.entity_id.as_str())
        .collect();
    let mut tombstones = if deletion_mode == DeletionMode::Tombstone {
        entries
            .keys()
            .filter(|id| !note_ids.contains(*id))
            .map(|id| (*id).to_string())
            .collect::<Vec<_>>()
    } else {
        Vec::new()
    };
    tombstones.extend(std::mem::take(&mut Vec::new()));
    finalize_plan(snapshot, deletion_mode, intents, tombstones, conflicts)
}
