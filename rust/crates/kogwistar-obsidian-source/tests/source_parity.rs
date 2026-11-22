use kogwistar_obsidian_source::host::{apply_import, SourceHost};
use kogwistar_obsidian_source::models::{
    DeletionMode, EntityKind, GraphEntityRecord, GraphSnapshot, ManagedRelationRecord,
};
use kogwistar_obsidian_source::sync::{plan_import, plan_to_json};
use kogwistar_obsidian_source::vault::{scan_vault, write_snapshot};
use serde_json::json;
use std::cell::RefCell;

fn base_snapshot() -> GraphSnapshot {
    GraphSnapshot {
        workspace_id: "workspace-a".to_string(),
        vault_id: "vault-a".to_string(),
        graph_revision: "graph-1".to_string(),
        entities: vec![
            GraphEntityRecord {
                entity_id: "node:alpha".to_string(),
                entity_kind: EntityKind::Node,
                title: "Alpha".to_string(),
                body: "A managed note.".to_string(),
                properties: json!({"tags": ["one", "two"]}),
                revision: "node-rev-1".to_string(),
                payload: json!({"opaque": {"kept": true}}),
            },
            GraphEntityRecord {
                entity_id: "node:beta".to_string(),
                entity_kind: EntityKind::Node,
                title: "Beta".to_string(),
                body: "Second note.".to_string(),
                properties: json!({}),
                revision: "node-rev-2".to_string(),
                payload: json!({}),
            },
        ],
        relations: vec![ManagedRelationRecord {
            edge_id: "edge:alpha-beta".to_string(),
            source_entity_id: "node:alpha".to_string(),
            target_entity_id: "node:beta".to_string(),
            relation_type: "supports".to_string(),
            role: "evidence".to_string(),
            properties: json!({"weight": 1}),
            digest: String::new(),
        }],
    }
}

#[test]
fn export_scan_plan_lossless_for_unchanged_vault() {
    let root = tempfile::tempdir().unwrap();
    let snapshot = base_snapshot();
    let manifest = write_snapshot(&snapshot, root.path()).unwrap();
    let scan = scan_vault(root.path()).unwrap();
    assert!(scan.valid());
    assert_eq!(scan.manifest.as_ref().unwrap().digest, manifest.digest);
    assert!(scan.missing_paths.is_empty());
    assert!(scan.errors.is_empty());
    let plan = plan_import(&snapshot, &scan, DeletionMode::Ignore);
    assert!(!plan.applyable());
    assert!(plan.intents.is_empty());
    assert!(plan.tombstone_ids.is_empty());
    assert!(plan.conflicts.is_empty());
}

#[test]
fn user_note_edit_creates_one_typed_intent() {
    let root = tempfile::tempdir().unwrap();
    let snapshot = base_snapshot();
    write_snapshot(&snapshot, root.path()).unwrap();
    let scan = scan_vault(root.path()).unwrap();
    let note_path = scan
        .manifest
        .as_ref()
        .unwrap()
        .entries
        .iter()
        .find(|entry| entry.entity_id == "node:alpha")
        .map(|entry| root.path().join(&entry.path))
        .unwrap();
    let text = std::fs::read_to_string(&note_path).unwrap();
    std::fs::write(&note_path, text.replace("A managed note.", "Edited body.")).unwrap();
    let scan = scan_vault(root.path()).unwrap();
    let plan = plan_import(&snapshot, &scan, DeletionMode::Ignore);
    assert!(plan.applyable());
    assert_eq!(plan.intents.len(), 1);
    assert_eq!(plan.intents[0].entity_id, "node:alpha");
    assert_eq!(plan.intents[0].body, "Edited body.");
    assert_eq!(plan.intents[0].expected_revision, "node-rev-1");
}

#[test]
fn concurrent_graph_and_vault_edits_conflict() {
    let root = tempfile::tempdir().unwrap();
    let snapshot = base_snapshot();
    write_snapshot(&snapshot, root.path()).unwrap();
    let scan = scan_vault(root.path()).unwrap();
    let note_path = scan
        .manifest
        .as_ref()
        .unwrap()
        .entries
        .iter()
        .find(|entry| entry.entity_id == "node:alpha")
        .map(|entry| root.path().join(&entry.path))
        .unwrap();
    let text = std::fs::read_to_string(&note_path).unwrap();
    std::fs::write(&note_path, text.replace("A managed note.", "Edited body.")).unwrap();
    let mut changed = base_snapshot();
    changed.entities[0].body = "Concurrent graph edit.".to_string();
    changed.entities[0].revision = "node-rev-1".to_string();
    let scan = scan_vault(root.path()).unwrap();
    let plan = plan_import(&changed, &scan, DeletionMode::Ignore);
    assert!(!plan.applyable());
    assert_eq!(plan.conflicts.len(), 1);
    assert_eq!(
        plan.conflicts[0].reason,
        kogwistar_obsidian_source::models::ConflictReason::BothChanged
    );
}

#[test]
fn revision_only_graph_change_conflicts_with_vault_edit() {
    let root = tempfile::tempdir().unwrap();
    let snapshot = base_snapshot();
    write_snapshot(&snapshot, root.path()).unwrap();
    let scan = scan_vault(root.path()).unwrap();
    let note_path = scan
        .manifest
        .as_ref()
        .unwrap()
        .entries
        .iter()
        .find(|entry| entry.entity_id == "node:alpha")
        .map(|entry| root.path().join(&entry.path))
        .unwrap();
    let text = std::fs::read_to_string(&note_path).unwrap();
    std::fs::write(&note_path, text.replace("A managed note.", "Edited body.")).unwrap();
    let mut changed = base_snapshot();
    changed.entities[0].revision = "node-rev-1-imported".to_string();
    let scan = scan_vault(root.path()).unwrap();
    let plan = plan_import(&changed, &scan, DeletionMode::Ignore);
    assert!(!plan.applyable());
    assert_eq!(plan.conflicts.len(), 1);
    assert_eq!(
        plan.conflicts[0].reason,
        kogwistar_obsidian_source::models::ConflictReason::BothChanged
    );
}

#[test]
fn invalid_scan_never_carries_mutation_intents() {
    let root = tempfile::tempdir().unwrap();
    std::fs::write(root.path().join("not-manifest.md"), "# unmanaged\n").unwrap();
    let scan = scan_vault(root.path()).unwrap();
    assert!(!scan.valid());
    let plan = plan_import(&base_snapshot(), &scan, DeletionMode::Ignore);
    assert!(!plan.applyable());
    assert!(plan.intents.is_empty());
    assert_eq!(plan.conflicts.len(), 1);
    assert_eq!(
        plan.conflicts[0].reason,
        kogwistar_obsidian_source::models::ConflictReason::Invalid
    );
}

#[test]
fn plan_identity_is_independent_of_scan_note_order() {
    let root = tempfile::tempdir().unwrap();
    let snapshot = base_snapshot();
    write_snapshot(&snapshot, root.path()).unwrap();
    let scan = scan_vault(root.path()).unwrap();
    let note_path = scan
        .manifest
        .as_ref()
        .unwrap()
        .entries
        .iter()
        .find(|entry| entry.entity_id == "node:alpha")
        .map(|entry| root.path().join(&entry.path))
        .unwrap();
    let text = std::fs::read_to_string(&note_path).unwrap();
    std::fs::write(&note_path, text.replace("A managed note.", "Edited body.")).unwrap();
    let first_scan = scan_vault(root.path()).unwrap();
    let mut second_scan = scan_vault(root.path()).unwrap();
    second_scan.notes.reverse();
    let first = plan_import(&snapshot, &first_scan, DeletionMode::Ignore);
    let second = plan_import(&snapshot, &second_scan, DeletionMode::Ignore);
    assert_eq!(first.plan_id, second.plan_id);
}

#[test]
fn missing_managed_file_only_tombstones_when_opted_in() {
    let root = tempfile::tempdir().unwrap();
    let snapshot = base_snapshot();
    write_snapshot(&snapshot, root.path()).unwrap();
    let scan = scan_vault(root.path()).unwrap();
    let note_path = scan
        .manifest
        .as_ref()
        .unwrap()
        .entries
        .iter()
        .find(|entry| entry.entity_id == "node:beta")
        .map(|entry| root.path().join(&entry.path))
        .unwrap();
    std::fs::remove_file(&note_path).unwrap();
    let scan = scan_vault(root.path()).unwrap();
    let ignored = plan_import(&snapshot, &scan, DeletionMode::Ignore);
    assert!(ignored.tombstone_ids.is_empty());
    let tombstoned = plan_import(&snapshot, &scan, DeletionMode::Tombstone);
    assert_eq!(tombstoned.tombstone_ids, vec!["node:beta".to_string()]);
}

#[test]
fn export_refuses_overwrite_unmanaged_canonical_path() {
    let root = tempfile::tempdir().unwrap();
    let snapshot = base_snapshot();
    let path = kogwistar_obsidian_source::vault::safe_note_path(&snapshot.entities[0]);
    let target = root.path().join(&path);
    std::fs::create_dir_all(target.parent().unwrap()).unwrap();
    std::fs::write(&target, "# unmanaged\n").unwrap();
    assert!(write_snapshot(&snapshot, root.path()).is_err());
}

#[test]
fn path_escape_is_rejected() {
    let root = tempfile::tempdir().unwrap();
    let mut snapshot = base_snapshot();
    snapshot.entities[0].title = "../escape".to_string();
    snapshot.entities[0].entity_id = "../escape".to_string();
    assert!(write_snapshot(&snapshot, root.path()).is_err());
}

struct RecordingHost {
    applied: RefCell<Vec<(String, String, String, DeletionMode)>>,
}

impl SourceHost for RecordingHost {
    fn apply_import(
        &self,
        workspace_id: &str,
        vault_id: &str,
        plan_id: &str,
        deletion_mode: DeletionMode,
    ) -> Result<serde_json::Value, anyhow::Error> {
        self.applied.borrow_mut().push((
            workspace_id.to_string(),
            vault_id.to_string(),
            plan_id.to_string(),
            deletion_mode,
        ));
        Ok(json!({"status": "applied", "plan_id": plan_id}))
    }
}

#[test]
fn apply_import_forwards_valid_plan_to_host() {
    let root = tempfile::tempdir().unwrap();
    let snapshot = base_snapshot();
    write_snapshot(&snapshot, root.path()).unwrap();
    let scan = scan_vault(root.path()).unwrap();
    let note_path = scan
        .manifest
        .as_ref()
        .unwrap()
        .entries
        .iter()
        .find(|entry| entry.entity_id == "node:alpha")
        .map(|entry| root.path().join(&entry.path))
        .unwrap();
    let text = std::fs::read_to_string(&note_path).unwrap();
    std::fs::write(&note_path, text.replace("A managed note.", "Edited body.")).unwrap();
    let scan = scan_vault(root.path()).unwrap();
    let plan = plan_import(&snapshot, &scan, DeletionMode::Ignore);
    assert!(plan.applyable());
    let plan_path = root.path().join("plan.json");
    std::fs::write(
        &plan_path,
        serde_json::to_vec_pretty(&plan_to_json(&plan)).unwrap(),
    )
    .unwrap();
    let host = RecordingHost {
        applied: RefCell::new(Vec::new()),
    };
    let result = apply_import(&host, &plan_path, DeletionMode::Ignore).unwrap();
    assert_eq!(result["status"], "applied");
    assert_eq!(result["plan_id"], plan.plan_id);
    assert_eq!(host.applied.borrow().len(), 1);
}
