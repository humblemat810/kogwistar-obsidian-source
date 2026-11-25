use serde::{Deserialize, Serialize};
use serde_json::Value;

pub const FORMAT_VERSION: &str = "kogwistar-obsidian-source/v1";

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum EntityKind {
    Node,
    Edge,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum DeletionMode {
    Ignore,
    Tombstone,
}

impl DeletionMode {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Ignore => "ignore",
            Self::Tombstone => "tombstone",
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ManagedRelationRecord {
    pub edge_id: String,
    pub source_entity_id: String,
    pub target_entity_id: String,
    pub relation_type: String,
    pub role: String,
    #[serde(default)]
    pub properties: Value,
    #[serde(default)]
    pub digest: String,
}

impl ManagedRelationRecord {
    pub fn payload(&self) -> Value {
        serde_json::json!({
            "edge_id": self.edge_id,
            "source_entity_id": self.source_entity_id,
            "target_entity_id": self.target_entity_id,
            "relation_type": self.relation_type,
            "role": self.role,
            "properties": self.properties,
        })
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GraphEntityRecord {
    pub entity_id: String,
    pub entity_kind: EntityKind,
    pub title: String,
    pub body: String,
    pub properties: Value,
    pub revision: String,
    #[serde(default)]
    pub payload: Value,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GraphSnapshot {
    pub workspace_id: String,
    pub vault_id: String,
    pub graph_revision: String,
    pub entities: Vec<GraphEntityRecord>,
    #[serde(default)]
    pub relations: Vec<ManagedRelationRecord>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct VaultManifestEntry {
    pub path: String,
    pub entity_id: String,
    pub entity_kind: EntityKind,
    pub base_revision: String,
    pub base_digest: String,
    pub file_digest: String,
    #[serde(default)]
    pub payload: Value,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct VaultManifest {
    pub workspace_id: String,
    pub vault_id: String,
    pub graph_revision: String,
    pub entries: Vec<VaultManifestEntry>,
    pub format_version: String,
    pub digest: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ManagedNoteRecord {
    pub workspace_id: String,
    pub entity_id: String,
    pub entity_kind: EntityKind,
    pub title: String,
    pub body: String,
    pub properties: Value,
    pub base_revision: String,
    pub base_digest: String,
    pub file_digest: String,
    pub format_version: String,
    #[serde(default)]
    pub relations: Vec<ManagedRelationRecord>,
    pub observed_file_digest: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct VaultImportScan {
    pub manifest: Option<VaultManifest>,
    #[serde(default)]
    pub notes: Vec<ManagedNoteRecord>,
    #[serde(default)]
    pub missing_paths: Vec<String>,
    #[serde(default)]
    pub unmanaged_paths: Vec<String>,
    #[serde(default)]
    pub errors: Vec<String>,
}

impl VaultImportScan {
    pub fn valid(&self) -> bool {
        self.manifest.is_some() && self.errors.is_empty()
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ConflictReason {
    GraphChanged,
    VaultChanged,
    BothChanged,
    Invalid,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct VaultConflict {
    pub entity_id: String,
    pub reason: ConflictReason,
    pub message: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SyncIntent {
    pub entity_id: String,
    pub entity_kind: EntityKind,
    pub title: String,
    pub body: String,
    pub properties: Value,
    pub relations: Vec<ManagedRelationRecord>,
    pub expected_revision: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct VaultSyncPlan {
    pub plan_id: String,
    pub workspace_id: String,
    pub vault_id: String,
    pub deletion_mode: DeletionMode,
    pub intents: Vec<SyncIntent>,
    #[serde(default)]
    pub tombstone_ids: Vec<String>,
    #[serde(default)]
    pub conflicts: Vec<VaultConflict>,
}

impl VaultSyncPlan {
    pub fn applyable(&self) -> bool {
        (!self.intents.is_empty() || !self.tombstone_ids.is_empty()) && self.conflicts.is_empty()
    }
}
