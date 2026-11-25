use anyhow::{anyhow, bail, Context, Result};
use kogwistar_obsidian_source::canonical::FORMAT_VERSION;
use kogwistar_obsidian_source::host::validate_host_spec;
use kogwistar_obsidian_source::models::{DeletionMode, GraphSnapshot};
use kogwistar_obsidian_source::sync::{plan_import, plan_to_json};
use kogwistar_obsidian_source::vault::{json_pretty_sorted, scan_vault, write_snapshot};
use serde::Deserialize;
use serde_json::{json, Value};
use std::path::PathBuf;

#[derive(Deserialize)]
struct SnapshotDocument {
    workspace_id: String,
    vault_id: String,
    graph_revision: String,
    entities: Vec<kogwistar_obsidian_source::models::GraphEntityRecord>,
    #[serde(default)]
    relations: Vec<kogwistar_obsidian_source::models::ManagedRelationRecord>,
}

fn load_snapshot(path: &str) -> Result<GraphSnapshot> {
    let raw = std::fs::read_to_string(path)?;
    let value: Value =
        serde_json::from_str(&raw).with_context(|| format!("invalid snapshot JSON: {path}"))?;
    if value.get("format_version").and_then(Value::as_str) != Some(FORMAT_VERSION) {
        bail!("unsupported snapshot format version");
    }
    let document: SnapshotDocument = serde_json::from_value(value)?;
    Ok(GraphSnapshot {
        workspace_id: document.workspace_id,
        vault_id: document.vault_id,
        graph_revision: document.graph_revision,
        entities: document.entities,
        relations: document.relations,
    })
}

fn print_json(value: &Value) {
    println!("{}", json_pretty_sorted(value));
}

fn usage() -> ! {
    eprintln!("usage: kogwistar-obsidian-source <export|scan|plan-import|conflicts|apply-import> [options]");
    std::process::exit(2);
}

fn main() -> Result<()> {
    let arguments = std::env::args().skip(1).collect::<Vec<_>>();
    if arguments.is_empty() {
        usage();
    }
    match arguments[0].as_str() {
        "export" => {
            if arguments.len() != 5 || arguments[1] != "--snapshot" || arguments[3] != "--vault" {
                usage();
            }
            let snapshot = load_snapshot(&arguments[2])?;
            let manifest = write_snapshot(&snapshot, &PathBuf::from(&arguments[4]))?;
            print_json(&json!({"status": "exported", "digest": manifest.digest}));
        }
        "scan" => {
            if arguments.len() != 3 || arguments[1] != "--vault" {
                usage();
            }
            let result = scan_vault(&PathBuf::from(&arguments[2]))?;
            print_json(&json!({
                "valid": result.valid(),
                "missing_paths": result.missing_paths,
                "unmanaged_paths": result.unmanaged_paths,
                "errors": result.errors,
            }));
            if !result.valid() {
                std::process::exit(2);
            }
        }
        "plan-import" => {
            if arguments.len() < 5 || arguments[1] != "--snapshot" || arguments[3] != "--vault" {
                usage();
            }
            let mut deletion_mode = DeletionMode::Ignore;
            let mut output = "-";
            let mut index = 5;
            while index < arguments.len() {
                match arguments[index].as_str() {
                    "--deletion-mode" => {
                        deletion_mode = match arguments.get(index + 1).map(String::as_str) {
                            Some("ignore") => DeletionMode::Ignore,
                            Some("tombstone") => DeletionMode::Tombstone,
                            _ => bail!("invalid deletion mode"),
                        };
                        index += 2;
                    }
                    "--output" => {
                        output = arguments
                            .get(index + 1)
                            .map(String::as_str)
                            .ok_or_else(|| anyhow!("--output requires a value"))?;
                        index += 2;
                    }
                    _ => bail!("unknown argument: {}", arguments[index]),
                }
            }
            let snapshot = load_snapshot(&arguments[2])?;
            let scan = scan_vault(&PathBuf::from(&arguments[4]))?;
            let plan = plan_import(&snapshot, &scan, deletion_mode);
            let encoded = format!("{}\n", json_pretty_sorted(&plan_to_json(&plan)));
            if output == "-" {
                print!("{encoded}");
            } else {
                std::fs::write(output, encoded)?;
            }
            if !plan.applyable() {
                std::process::exit(3);
            }
        }
        "conflicts" => {
            if arguments.len() != 3 || arguments[1] != "--plan" {
                usage();
            }
            let raw: Value = serde_json::from_str(&std::fs::read_to_string(&arguments[2])?)?;
            print_json(&json!({
                "plan_id": raw.get("plan_id"),
                "conflicts": raw.get("conflicts").cloned().unwrap_or(Value::Array(Vec::new())),
            }));
            if raw
                .get("conflicts")
                .and_then(Value::as_array)
                .is_some_and(|conflicts| !conflicts.is_empty())
            {
                std::process::exit(3);
            }
        }
        "apply-import" => {
            if arguments.len() != 3 || arguments[1] != "--host" {
                usage();
            }
            validate_host_spec(&arguments[2])?;
            bail!("Rust apply-import requires an embedded Kogwistar host; Python compatibility CLI remains authoritative");
        }
        _ => usage(),
    }
    Ok(())
}
