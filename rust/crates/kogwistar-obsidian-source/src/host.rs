use crate::models::{DeletionMode, VaultSyncPlan};
use anyhow::{bail, Context, Result};
use serde_json::{json, Value};
use std::path::Path;

pub trait SourceHost {
    fn apply_import(
        &self,
        workspace_id: &str,
        vault_id: &str,
        plan_id: &str,
        deletion_mode: DeletionMode,
    ) -> Result<Value>;

    fn list_conflicts(&self, workspace_id: &str, vault_id: &str) -> Result<Value> {
        let _ = (workspace_id, vault_id);
        Ok(json!({"conflicts": []}))
    }
}

pub fn validate_host_spec(spec: &str) -> Result<(&str, &str)> {
    let Some((module, factory)) = spec.split_once(':') else {
        bail!("--host must use module:factory");
    };
    if module.is_empty() || factory.is_empty() {
        bail!("--host must use module:factory");
    }
    Ok((module, factory))
}

pub fn apply_import(
    host: &dyn SourceHost,
    plan_path: &Path,
    deletion_mode: DeletionMode,
) -> Result<Value> {
    let raw = std::fs::read_to_string(plan_path)?;
    let plan: VaultSyncPlan = serde_json::from_str(&raw).context("invalid import plan JSON")?;
    if plan.plan_id.trim().is_empty() {
        bail!("the import plan must contain an explicit plan_id");
    }
    if plan.deletion_mode != deletion_mode {
        bail!("--deletion-mode must match the persisted import plan");
    }
    if !plan.conflicts.is_empty() {
        return Ok(json!({"status": "conflict", "plan_id": plan.plan_id}));
    }
    if plan.intents.is_empty() && plan.tombstone_ids.is_empty() {
        bail!("the import plan has no mutations to apply");
    }
    host.apply_import(
        &plan.workspace_id,
        &plan.vault_id,
        &plan.plan_id,
        deletion_mode,
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn validates_host_spec() {
        assert_eq!(
            validate_host_spec("host:factory").unwrap(),
            ("host", "factory")
        );
        assert!(validate_host_spec("host").is_err());
        assert!(validate_host_spec(":factory").is_err());
        assert!(validate_host_spec("host:").is_err());
    }
}
