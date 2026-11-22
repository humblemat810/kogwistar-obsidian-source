use anyhow::{bail, Result};
use serde_json::{Map, Value};

use crate::models::DeletionMode;

pub trait WorkbenchHost {
    fn export_snapshot(&self, workspace_id: &str, vault_id: &str) -> Result<Value>;
    fn scan_import(&self, workspace_id: &str, vault_id: &str, payload: &Value) -> Result<Value>;
    fn plan_import(&self, workspace_id: &str, vault_id: &str, payload: &Value) -> Result<Value>;
    fn apply_import(
        &self,
        workspace_id: &str,
        vault_id: &str,
        plan_id: &str,
        deletion_mode: DeletionMode,
    ) -> Result<Value>;
    fn list_conflicts(&self, workspace_id: &str, vault_id: &str) -> Result<Value>;
}

pub type WorkbenchHandler<'a> = Box<dyn Fn(&Map<String, Value>) -> Result<Value> + 'a>;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RouteScope {
    Read,
    Write,
}

pub struct WorkbenchRoute<'a> {
    pub method: &'static str,
    pub path: &'static str,
    pub required_scope: RouteScope,
    pub handler: WorkbenchHandler<'a>,
}

pub struct WorkbenchExtension<'a> {
    pub extension_id: &'static str,
    pub routes: Vec<WorkbenchRoute<'a>>,
}

pub fn workspace_id(payload: &Map<String, Value>) -> Result<String> {
    let Some(value) = payload.get("workspace_id").and_then(Value::as_str) else {
        bail!("workspace_id must be a non-empty bounded string");
    };
    let value = value.trim();
    if value.is_empty() || value.len() > 256 {
        bail!("workspace_id must be a non-empty bounded string");
    }
    Ok(value.to_string())
}

pub fn request_ids(payload: &Map<String, Value>) -> Result<(String, String)> {
    let workspace = workspace_id(payload)?;
    let vault = payload
        .get("vault_id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .trim();
    if vault.is_empty() || vault.len() > 256 {
        bail!("workspace_id and vault_id must be non-empty bounded strings");
    }
    Ok((workspace, vault.to_string()))
}

pub fn build_workbench_extension<'a>(host: &'a dyn WorkbenchHost) -> WorkbenchExtension<'a> {
    WorkbenchExtension {
        extension_id: "obsidian-source",
        routes: vec![
            WorkbenchRoute {
                method: "POST",
                path: "/plugins/obsidian-source/export",
                required_scope: RouteScope::Read,
                handler: Box::new(|payload| {
                    let (workspace, vault) = request_ids(payload)?;
                    host.export_snapshot(&workspace, &vault)
                }),
            },
            WorkbenchRoute {
                method: "POST",
                path: "/plugins/obsidian-source/scan",
                required_scope: RouteScope::Read,
                handler: Box::new(|payload| {
                    let (workspace, vault) = request_ids(payload)?;
                    host.scan_import(&workspace, &vault, &Value::Object(payload.clone()))
                }),
            },
            WorkbenchRoute {
                method: "POST",
                path: "/plugins/obsidian-source/plan-import",
                required_scope: RouteScope::Read,
                handler: Box::new(|payload| {
                    let (workspace, vault) = request_ids(payload)?;
                    host.plan_import(&workspace, &vault, &Value::Object(payload.clone()))
                }),
            },
            WorkbenchRoute {
                method: "POST",
                path: "/plugins/obsidian-source/apply-import",
                required_scope: RouteScope::Write,
                handler: Box::new(|payload| {
                    let (workspace, vault) = request_ids(payload)?;
                    let plan_id = payload
                        .get("plan_id")
                        .and_then(Value::as_str)
                        .unwrap_or_default()
                        .trim();
                    let deletion_mode = payload
                        .get("deletion_mode")
                        .and_then(Value::as_str)
                        .unwrap_or("ignore");
                    let deletion_mode = match deletion_mode {
                        "ignore" => DeletionMode::Ignore,
                        "tombstone" => DeletionMode::Tombstone,
                        _ => bail!("deletion_mode must be ignore or tombstone"),
                    };
                    if plan_id.is_empty() || plan_id.len() > 256 {
                        bail!("plan_id must be a non-empty bounded string");
                    }
                    host.apply_import(&workspace, &vault, plan_id, deletion_mode)
                }),
            },
            WorkbenchRoute {
                method: "POST",
                path: "/plugins/obsidian-source/conflicts",
                required_scope: RouteScope::Read,
                handler: Box::new(|payload| {
                    let (workspace, vault) = request_ids(payload)?;
                    host.list_conflicts(&workspace, &vault)
                }),
            },
        ],
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::cell::RefCell;
    use std::collections::HashMap;

    struct RecordingHost {
        calls: RefCell<HashMap<String, usize>>,
    }

    impl RecordingHost {
        fn new() -> Self {
            Self {
                calls: RefCell::new(HashMap::new()),
            }
        }

        fn record(&self, name: &str) {
            *self.calls.borrow_mut().entry(name.to_string()).or_default() += 1;
        }
    }

    impl WorkbenchHost for RecordingHost {
        fn export_snapshot(&self, _workspace_id: &str, _vault_id: &str) -> Result<Value> {
            self.record("export");
            Ok(json!({}))
        }

        fn scan_import(
            &self,
            _workspace_id: &str,
            _vault_id: &str,
            _payload: &Value,
        ) -> Result<Value> {
            self.record("scan");
            Ok(json!({}))
        }

        fn plan_import(
            &self,
            _workspace_id: &str,
            _vault_id: &str,
            _payload: &Value,
        ) -> Result<Value> {
            self.record("plan");
            Ok(json!({}))
        }

        fn apply_import(
            &self,
            _workspace_id: &str,
            _vault_id: &str,
            plan_id: &str,
            deletion_mode: DeletionMode,
        ) -> Result<Value> {
            self.record("apply");
            assert_eq!(plan_id, "plan-a");
            assert_eq!(deletion_mode, DeletionMode::Tombstone);
            Ok(json!({}))
        }

        fn list_conflicts(&self, _workspace_id: &str, _vault_id: &str) -> Result<Value> {
            self.record("conflicts");
            Ok(json!({}))
        }
    }

    fn payload() -> Map<String, Value> {
        json!({
            "workspace_id": " workspace-a ",
            "vault_id": "vault-a",
            "plan_id": "plan-a",
            "deletion_mode": "tombstone"
        })
        .as_object()
        .cloned()
        .unwrap()
    }

    #[test]
    fn routes_host_methods_and_apply_scope() {
        let host = RecordingHost::new();
        let extension = build_workbench_extension(&host);
        assert_eq!(extension.routes.len(), 5);
        let apply = extension
            .routes
            .iter()
            .find(|route| route.path.ends_with("/apply-import"))
            .unwrap();
        assert_eq!(apply.required_scope, RouteScope::Write);
        (apply.handler)(&payload()).unwrap();
        assert_eq!(host.calls.borrow().get("apply"), Some(&1));
        for route in &extension.routes {
            if route.path.ends_with("/apply-import") {
                continue;
            }
            (route.handler)(&payload()).unwrap();
        }
        for name in ["export", "scan", "plan", "conflicts"] {
            assert_eq!(host.calls.borrow().get(name), Some(&1));
        }
    }

    #[test]
    fn rejects_unbounded_apply_plan() {
        let host = RecordingHost::new();
        let extension = build_workbench_extension(&host);
        let mut payload = payload();
        payload.insert("plan_id".into(), Value::from("x".repeat(257)));
        let apply = extension
            .routes
            .iter()
            .find(|route| route.path.ends_with("/apply-import"))
            .unwrap();
        assert!((apply.handler)(&payload).is_err());
        assert_eq!(host.calls.borrow().get("apply"), None);
    }
}
