use crate::canonical::{canonical_digest, FORMAT_VERSION};
use crate::models::{
    EntityKind, GraphEntityRecord, GraphSnapshot, ManagedNoteRecord, ManagedRelationRecord,
    VaultImportScan, VaultManifest, VaultManifestEntry,
};
use anyhow::{anyhow, bail, Context, Result};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};

pub const MANIFEST_RELATIVE_PATH: &str = ".kogwistar/obsidian-source/manifest.json";
pub const RELATION_START: &str = "<!-- KOGWISTAR-RELATIONS-START -->";
pub const RELATION_END: &str = "<!-- KOGWISTAR-RELATIONS-END -->";

const ZERO_DIGEST: &str = "0000000000000000000000000000000000000000000000000000000000000000";

fn text(value: Option<&Value>, field: &str) -> Result<String> {
    let value = value
        .and_then(Value::as_str)
        .ok_or_else(|| anyhow!("{field} must be a non-empty string"))?;
    if value.trim().is_empty() {
        bail!("{field} must be a non-empty string");
    }
    Ok(value.to_string())
}

fn object(value: Option<&Value>, field: &str) -> Result<Value> {
    match value {
        Some(Value::Object(_)) => Ok(value.unwrap().clone()),
        _ => bail!("{field} must be a JSON object"),
    }
}

fn relation_array(value: Option<&Value>, field: &str) -> Result<Vec<Value>> {
    let values = value
        .and_then(Value::as_array)
        .ok_or_else(|| anyhow!("{field} must be a JSON array"))?;
    values
        .iter()
        .map(|value| object(Some(value), field))
        .collect()
}

fn validate_sha(value: &str, field: &str) -> Result<()> {
    if value.len() != 64
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        bail!("{field} must be a lowercase SHA-256 digest");
    }
    Ok(())
}

pub fn entity_payload(entity: &GraphEntityRecord, relations: &[ManagedRelationRecord]) -> Value {
    json!({
        "entity_id": entity.entity_id,
        "entity_kind": entity.entity_kind,
        "title": entity.title,
        "body": entity.body,
        "properties": entity.properties,
        "payload": entity.payload,
        "relations": relations.iter().map(ManagedRelationRecord::payload).collect::<Vec<_>>(),
    })
}

pub fn entity_digest(entity: &GraphEntityRecord, relations: &[ManagedRelationRecord]) -> String {
    canonical_digest(&entity_payload(entity, relations))
}

pub fn relation_payload_with_digest(relation: &ManagedRelationRecord) -> Value {
    let mut payload = relation.payload();
    if let Value::Object(map) = &mut payload {
        map.insert(
            "digest".to_string(),
            Value::String(canonical_digest(&relation.payload())),
        );
    }
    payload
}

fn relation_from_json(value: &Value) -> Result<ManagedRelationRecord> {
    let relation = ManagedRelationRecord {
        edge_id: text(value.get("edge_id"), "edge_id")?,
        source_entity_id: text(value.get("source_entity_id"), "source_entity_id")?,
        target_entity_id: text(value.get("target_entity_id"), "target_entity_id")?,
        relation_type: text(value.get("relation_type"), "relation_type")?,
        role: text(value.get("role"), "role")?,
        properties: object(value.get("properties"), "relation properties")?,
        digest: text(value.get("digest"), "relation digest")?,
    };
    if relation.source_entity_id == relation.target_entity_id {
        bail!("relation endpoints must be distinct");
    }
    let expected = canonical_digest(&relation.payload());
    if relation.digest != expected {
        bail!("invalid relation digest: {}", relation.edge_id);
    }
    Ok(relation)
}

fn safe_title(title: &str) -> String {
    let mut safe: String = title
        .chars()
        .filter(|character| {
            character.is_ascii_alphanumeric() || matches!(character, '.' | '_' | ' ' | '-')
        })
        .collect();
    safe = safe
        .trim_matches(|character| character == ' ' || character == '.')
        .to_string();
    if safe.is_empty() {
        safe = "untitled".to_string();
    }
    let upper = safe.to_uppercase();
    if matches!(
        upper.as_str(),
        "CON"
            | "PRN"
            | "AUX"
            | "NUL"
            | "COM1"
            | "COM2"
            | "COM3"
            | "COM4"
            | "COM5"
            | "COM6"
            | "COM7"
            | "COM8"
            | "COM9"
            | "LPT1"
            | "LPT2"
            | "LPT3"
            | "LPT4"
            | "LPT5"
            | "LPT6"
            | "LPT7"
            | "LPT8"
            | "LPT9"
    ) {
        safe = "untitled".to_string();
    }
    let mut safe = safe.chars().take(96).collect::<String>();
    safe = safe.trim_end_matches([' ', '.']).to_string();
    if safe.is_empty() {
        safe = "untitled".to_string();
    }
    safe
}

pub fn safe_note_path(entity: &GraphEntityRecord) -> String {
    let suffix = canonical_digest(&json!({"id": entity.entity_id}));
    format!(
        "Notes/{}/{}--{:.12}.md",
        entity_kind_label(entity.entity_kind),
        safe_title(&entity.title),
        suffix
    )
}

fn entity_kind_label(kind: EntityKind) -> &'static str {
    match kind {
        EntityKind::Node => "node",
        EntityKind::Edge => "edge",
    }
}

pub fn is_canonical_ledger_path(
    relative_path: &str,
    entity_id: &str,
    entity_kind: EntityKind,
) -> bool {
    if relative_path != relative_path.replace('\\', "/") {
        return false;
    }
    let suffix = format!("--{:.12}.md", canonical_digest(&json!({"id": entity_id})));
    let mut parts = relative_path.split('/');
    let expected_kind = entity_kind_label(entity_kind);
    match (parts.next(), parts.next(), parts.next(), parts.next()) {
        (Some("Notes"), Some(kind), Some(label), None) if kind == expected_kind => {
            if !label.ends_with(&suffix) {
                return false;
            }
            let name = &label[..label.len() - suffix.len()];
            !name.is_empty()
                && !name.starts_with(' ')
                && !name.starts_with('.')
                && !name.ends_with(' ')
                && !name.ends_with('.')
                && name.chars().all(|character| {
                    character.is_ascii_alphanumeric() || matches!(character, '.' | '_' | ' ' | '-')
                })
        }
        _ => false,
    }
}

fn note_payload(note: &ManagedNoteRecord) -> Value {
    json!({
        "workspace_id": note.workspace_id,
        "entity_id": note.entity_id,
        "entity_kind": note.entity_kind,
        "title": note.title,
        "body": note.body,
        "properties": note.properties,
        "base_revision": note.base_revision,
        "base_digest": note.base_digest,
        "format_version": note.format_version,
        "relations": note.relations.iter().map(relation_payload_with_digest).collect::<Vec<_>>(),
    })
}

fn note_file_digest(note: &ManagedNoteRecord) -> String {
    canonical_digest(&note_payload(note))
}

pub fn json_pretty_sorted(value: &Value) -> String {
    let mut output = String::new();
    write_pretty(value, 0, &mut output);
    output
}

fn write_pretty(value: &Value, indent: usize, output: &mut String) {
    match value {
        Value::Array(values) if values.is_empty() => output.push_str("[]"),
        Value::Array(values) => {
            output.push_str("[\n");
            for (index, item) in values.iter().enumerate() {
                if index > 0 {
                    output.push_str(",\n");
                }
                output.push_str(&"  ".repeat(indent + 1));
                write_pretty(item, indent + 1, output);
            }
            output.push('\n');
            output.push_str(&"  ".repeat(indent));
            output.push(']');
        }
        Value::Object(map) if map.is_empty() => output.push_str("{}"),
        Value::Object(map) => {
            let mut keys: Vec<&String> = map.keys().collect();
            keys.sort();
            output.push_str("{\n");
            for (index, key) in keys.iter().enumerate() {
                if index > 0 {
                    output.push_str(",\n");
                }
                output.push_str(&"  ".repeat(indent + 1));
                write_pretty(&Value::String((*key).clone()), indent + 1, output);
                output.push_str(": ");
                write_pretty(&map[*key], indent + 1, output);
            }
            output.push('\n');
            output.push_str(&"  ".repeat(indent));
            output.push('}');
        }
        value => output.push_str(&crate::canonical::canonical_json(value)),
    }
}

fn render_note(note: &ManagedNoteRecord) -> Result<String> {
    if note.body.contains(RELATION_START) || note.body.contains(RELATION_END) {
        bail!("managed note body cannot contain canonical relation markers");
    }
    let frontmatter = json!({
        "format_version": note.format_version,
        "workspace_id": note.workspace_id,
        "entity_id": note.entity_id,
        "entity_kind": note.entity_kind,
        "title": note.title,
        "base_revision": note.base_revision,
        "base_digest": note.base_digest,
        "file_digest": note.file_digest,
        "properties": note.properties,
    });
    let relations = note
        .relations
        .iter()
        .map(relation_payload_with_digest)
        .collect::<Value>();
    Ok(format!(
        "---\n{}\n---\n\n{}\n\n{}\n{}\n{}\n",
        json_pretty_sorted(&frontmatter),
        note.body.trim_end(),
        RELATION_START,
        json_pretty_sorted(&relations),
        RELATION_END,
    ))
}

fn atomic_write(path: &Path, content: &str) -> Result<()> {
    let parent = path.parent().ok_or_else(|| anyhow!("path has no parent"))?;
    fs::create_dir_all(parent)?;
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)?
        .as_nanos();
    let temporary = parent.join(format!(
        ".{}.{}.tmp",
        path.file_name().unwrap_or_default().to_string_lossy(),
        nanos
    ));
    fs::write(&temporary, content)?;
    fs::rename(&temporary, path).with_context(|| format!("replace {}", path.display()))?;
    Ok(())
}

fn manifest_payload(manifest: &VaultManifest) -> Value {
    json!({
        "format_version": manifest.format_version,
        "workspace_id": manifest.workspace_id,
        "vault_id": manifest.vault_id,
        "graph_revision": manifest.graph_revision,
        "entries": manifest.entries.iter().map(|entry| json!({
            "path": entry.path,
            "entity_id": entry.entity_id,
            "entity_kind": entry.entity_kind,
            "base_revision": entry.base_revision,
            "base_digest": entry.base_digest,
            "file_digest": entry.file_digest,
            "payload": entry.payload,
        })).collect::<Vec<_>>(),
    })
}

fn manifest_json(manifest: &VaultManifest) -> Value {
    let mut payload = manifest_payload(manifest);
    if let Value::Object(map) = &mut payload {
        map.insert(
            "digest".to_string(),
            Value::String(canonical_digest(&manifest_payload(manifest))),
        );
    }
    payload
}

fn validate_snapshot(snapshot: &GraphSnapshot) -> Result<()> {
    for (field, value) in [
        ("workspace_id", &snapshot.workspace_id),
        ("vault_id", &snapshot.vault_id),
        ("graph_revision", &snapshot.graph_revision),
    ] {
        if value.trim().is_empty() {
            bail!("{field} must be a non-empty string");
        }
    }
    let mut ids = std::collections::HashSet::new();
    for entity in &snapshot.entities {
        for (field, value) in [
            ("entity_id", &entity.entity_id),
            ("title", &entity.title),
            ("revision", &entity.revision),
        ] {
            if value.trim().is_empty() {
                bail!("{field} must be a non-empty string");
            }
        }
        if !ids.insert(&entity.entity_id) {
            bail!("graph snapshot contains duplicate entity IDs");
        }
    }
    let mut edge_ids = std::collections::HashSet::new();
    for relation in &snapshot.relations {
        for (field, value) in [
            ("edge_id", &relation.edge_id),
            ("source_entity_id", &relation.source_entity_id),
            ("target_entity_id", &relation.target_entity_id),
            ("relation_type", &relation.relation_type),
            ("role", &relation.role),
        ] {
            if value.trim().is_empty() {
                bail!("{field} must be a non-empty string");
            }
        }
        if relation.source_entity_id == relation.target_entity_id {
            bail!("relation endpoints must be distinct");
        }
        if !edge_ids.insert(&relation.edge_id) {
            bail!("graph snapshot contains duplicate relation IDs");
        }
        if !ids.contains(&relation.source_entity_id) || !ids.contains(&relation.target_entity_id) {
            bail!("graph snapshot contains a relation with an unknown endpoint");
        }
    }
    Ok(())
}

fn load_manifest(root: &Path) -> Result<VaultManifest> {
    let raw = fs::read_to_string(root.join(MANIFEST_RELATIVE_PATH))?;
    let raw: Value = serde_json::from_str(&raw).context("manifest is not valid JSON")?;
    if raw.get("format_version").and_then(Value::as_str) != Some(FORMAT_VERSION) {
        bail!("unsupported manifest format version");
    }
    let entries = raw
        .get("entries")
        .and_then(Value::as_array)
        .ok_or_else(|| anyhow!("manifest entries must be a JSON array"))?
        .iter()
        .map(|item| {
            Ok(VaultManifestEntry {
                path: text(item.get("path"), "entry path")?,
                entity_id: text(item.get("entity_id"), "entry entity_id")?,
                entity_kind: serde_json::from_value(
                    item.get("entity_kind").cloned().unwrap_or(Value::Null),
                )?,
                base_revision: text(item.get("base_revision"), "entry base_revision")?,
                base_digest: text(item.get("base_digest"), "entry base_digest")?,
                file_digest: text(item.get("file_digest"), "entry file_digest")?,
                payload: object(item.get("payload"), "entry payload")?,
            })
        })
        .collect::<Result<Vec<_>>>()?;
    let manifest = VaultManifest {
        workspace_id: text(raw.get("workspace_id"), "manifest workspace_id")?,
        vault_id: text(raw.get("vault_id"), "manifest vault_id")?,
        graph_revision: text(raw.get("graph_revision"), "manifest graph_revision")?,
        entries,
        format_version: FORMAT_VERSION.to_string(),
        digest: text(raw.get("digest"), "manifest digest")?,
    };
    let expected = canonical_digest(&manifest_payload(&manifest));
    if manifest.digest != expected {
        bail!("manifest digest does not match content");
    }
    let mut paths = std::collections::HashSet::new();
    let mut entity_ids = std::collections::HashSet::new();
    for entry in &manifest.entries {
        if !paths.insert(&entry.path) {
            bail!("manifest contains duplicate paths");
        }
        if !entity_ids.insert(&entry.entity_id) {
            bail!("manifest contains duplicate entity IDs");
        }
        validate_sha(&entry.base_digest, "entry base_digest")?;
        validate_sha(&entry.file_digest, "entry file_digest")?;
    }
    Ok(manifest)
}

fn safe_path(root: &Path, relative_path: &str) -> Result<PathBuf> {
    if relative_path.starts_with('/')
        || relative_path
            .split('/')
            .any(|part| part == ".." || part.is_empty())
    {
        bail!("managed path escapes vault root: {relative_path}");
    }
    Ok(root.join(relative_path))
}

fn parse_note(path: &Path, manifest_entry: &VaultManifestEntry) -> Result<ManagedNoteRecord> {
    let parsed = fs::read_to_string(path)
        .with_context(|| format!("managed note was deleted: {}", path.display()))
        .map_err(|error| {
            if error.to_string().contains("managed note was deleted") {
                anyhow!("MANAGED_NOTE_MISSING")
            } else {
                error
            }
        })?;
    if !parsed.starts_with("---\n") {
        bail!("managed note lacks frontmatter: {}", path.display());
    }
    let separator = parsed[4..]
        .find("\n---\n")
        .map(|index| index + 4)
        .ok_or_else(|| anyhow!("managed note has invalid frontmatter: {}", path.display()))?;
    let frontmatter: Value = serde_json::from_str(&parsed[4..separator])
        .with_context(|| format!("note frontmatter is not valid JSON: {}", path.display()))?;
    let remainder = &parsed[separator + 5..];
    let start = remainder.find(RELATION_START).ok_or_else(|| {
        anyhow!(
            "managed note lacks canonical relation block: {}",
            path.display()
        )
    })?;
    let end = remainder.find(RELATION_END).ok_or_else(|| {
        anyhow!(
            "managed note has ambiguous canonical relation blocks: {}",
            path.display()
        )
    })?;
    if start > end
        || remainder.matches(RELATION_START).count() != 1
        || remainder.matches(RELATION_END).count() != 1
    {
        bail!(
            "managed note has ambiguous canonical relation blocks: {}",
            path.display()
        );
    }
    let body = remainder[..start]
        .trim_matches(|character| character == '\r' || character == '\n')
        .trim_end()
        .to_string();
    let relations_value: Value =
        serde_json::from_str(remainder[start + RELATION_START.len()..end].trim())
            .with_context(|| format!("note relations are not valid JSON: {}", path.display()))?;
    let relations = relation_array(Some(&relations_value), "note relations")?
        .iter()
        .map(relation_from_json)
        .collect::<Result<Vec<_>>>()?;
    let note = ManagedNoteRecord {
        workspace_id: text(frontmatter.get("workspace_id"), "note workspace_id")?,
        entity_id: text(frontmatter.get("entity_id"), "note entity_id")?,
        entity_kind: serde_json::from_value(
            frontmatter
                .get("entity_kind")
                .cloned()
                .unwrap_or(Value::Null),
        )?,
        title: text(frontmatter.get("title"), "note title")?,
        body,
        properties: object(frontmatter.get("properties"), "note properties")?,
        base_revision: text(frontmatter.get("base_revision"), "note base_revision")?,
        base_digest: text(frontmatter.get("base_digest"), "note base_digest")?,
        file_digest: text(frontmatter.get("file_digest"), "note file_digest")?,
        format_version: text(frontmatter.get("format_version"), "note format_version")?,
        relations,
        observed_file_digest: ZERO_DIGEST.to_string(),
    };
    if note.entity_id != manifest_entry.entity_id
        || note.entity_kind != manifest_entry.entity_kind
        || note.base_revision != manifest_entry.base_revision
        || note.base_digest != manifest_entry.base_digest
        || note.file_digest != manifest_entry.file_digest
    {
        bail!("manifest and note identity disagree: {}", path.display());
    }
    if note
        .relations
        .iter()
        .any(|relation| relation.source_entity_id != note.entity_id)
    {
        bail!(
            "note relation source does not match note identity: {}",
            path.display()
        );
    }
    let mut parsed = note;
    parsed.observed_file_digest = note_file_digest(&parsed);
    Ok(parsed)
}

fn unmanaged_markdown(root: &Path) -> Result<Vec<String>> {
    let mut result = Vec::new();
    let mut stack = vec![root.to_path_buf()];
    while let Some(directory) = stack.pop() {
        for entry in fs::read_dir(directory)? {
            let entry = entry?;
            let path = entry.path();
            let metadata = fs::symlink_metadata(&path)?;
            if metadata.file_type().is_dir() {
                stack.push(path);
            } else if path.extension().and_then(|extension| extension.to_str()) == Some("md") {
                result.push(
                    path.strip_prefix(root)?
                        .to_string_lossy()
                        .replace('\\', "/"),
                );
            }
        }
    }
    result.sort();
    Ok(result)
}

pub fn scan_vault(vault_root: &Path) -> Result<VaultImportScan> {
    let root = fs::canonicalize(vault_root)?;
    let manifest = match load_manifest(&root) {
        Ok(manifest) => Some(manifest),
        Err(error) => {
            return Ok(VaultImportScan {
                manifest: None,
                notes: Vec::new(),
                missing_paths: Vec::new(),
                unmanaged_paths: Vec::new(),
                errors: vec![error.to_string()],
            })
        }
    };
    let manifest_value = manifest.clone().expect("manifest is present");
    let mut notes = Vec::new();
    let mut missing = Vec::new();
    let mut errors = Vec::new();
    for entry in &manifest_value.entries {
        let path = safe_path(&root, &entry.path)?;
        match parse_note(&path, entry) {
            Ok(note) => {
                if !is_canonical_ledger_path(&entry.path, &note.entity_id, note.entity_kind) {
                    errors.push(format!(
                        "managed note path is not canonical: {}",
                        entry.path
                    ));
                } else {
                    notes.push(note);
                }
            }
            Err(error) if error.to_string() == "MANAGED_NOTE_MISSING" => {
                missing.push(entry.path.clone())
            }
            Err(error) => errors.push(error.to_string()),
        }
    }
    notes.sort_by(|left, right| left.entity_id.cmp(&right.entity_id));
    missing.sort();
    errors.sort();
    Ok(VaultImportScan {
        manifest: Some(manifest_value),
        notes,
        missing_paths: missing,
        unmanaged_paths: unmanaged_markdown(&root)?,
        errors,
    })
}

pub fn write_snapshot(snapshot: &GraphSnapshot, vault_root: &Path) -> Result<VaultManifest> {
    validate_snapshot(snapshot)?;
    fs::create_dir_all(vault_root)?;
    let root = fs::canonicalize(vault_root)?;
    let mut existing_entries = HashMap::new();
    if root.join(MANIFEST_RELATIVE_PATH).try_exists()? {
        let existing = load_manifest(&root)?;
        if existing.workspace_id != snapshot.workspace_id || existing.vault_id != snapshot.vault_id
        {
            bail!("existing managed vault identity does not match graph snapshot");
        }
        for entry in existing.entries {
            existing_entries.insert(entry.entity_id.clone(), entry);
        }
    }
    let mut grouped: HashMap<&str, Vec<&ManagedRelationRecord>> = HashMap::new();
    for relation in &snapshot.relations {
        grouped
            .entry(relation.source_entity_id.as_str())
            .or_default()
            .push(relation);
    }
    for relations in grouped.values_mut() {
        relations.sort_by(|left, right| left.edge_id.cmp(&right.edge_id));
    }
    let mut entities = snapshot.entities.clone();
    entities.sort_by(|left, right| left.entity_id.cmp(&right.entity_id));
    let mut entries = Vec::new();
    for entity in &entities {
        let relations = grouped
            .get(entity.entity_id.as_str())
            .map(|values| {
                values
                    .iter()
                    .map(|value| (*value).clone())
                    .collect::<Vec<_>>()
            })
            .unwrap_or_default();
        let base_digest = entity_digest(entity, &relations);
        let note = ManagedNoteRecord {
            workspace_id: snapshot.workspace_id.clone(),
            entity_id: entity.entity_id.clone(),
            entity_kind: entity.entity_kind,
            title: entity.title.clone(),
            body: entity.body.clone(),
            properties: entity.properties.clone(),
            base_revision: entity.revision.clone(),
            base_digest: base_digest.clone(),
            file_digest: ZERO_DIGEST.to_string(),
            format_version: FORMAT_VERSION.to_string(),
            relations: relations.clone(),
            observed_file_digest: ZERO_DIGEST.to_string(),
        };
        let file_digest = note_file_digest(&note);
        let note = ManagedNoteRecord {
            file_digest: file_digest.clone(),
            ..note
        };
        let existing_entry = existing_entries.get(&entity.entity_id);
        let relative_path = if let Some(existing_entry) = existing_entry {
            if existing_entry.entity_kind != entity.entity_kind
                || !is_canonical_ledger_path(
                    &existing_entry.path,
                    &entity.entity_id,
                    entity.entity_kind,
                )
            {
                bail!("existing managed path is invalid: {}", existing_entry.path);
            }
            existing_entry.path.clone()
        } else {
            safe_note_path(entity)
        };
        let target = safe_path(&root, &relative_path)?;
        if target.try_exists()? && existing_entry.is_none() {
            bail!("refusing to overwrite unmanaged vault file: {relative_path}");
        }
        atomic_write(&target, &render_note(&note)?)?;
        entries.push(VaultManifestEntry {
            path: relative_path,
            entity_id: entity.entity_id.clone(),
            entity_kind: entity.entity_kind,
            base_revision: entity.revision.clone(),
            base_digest,
            file_digest,
            payload: entity_payload(entity, &relations),
        });
    }
    let manifest = VaultManifest {
        workspace_id: snapshot.workspace_id.clone(),
        vault_id: snapshot.vault_id.clone(),
        graph_revision: snapshot.graph_revision.clone(),
        entries,
        format_version: FORMAT_VERSION.to_string(),
        digest: String::new(),
    };
    let final_manifest = VaultManifest {
        digest: canonical_digest(&manifest_payload(&manifest)),
        ..manifest
    };
    atomic_write(
        &root.join(MANIFEST_RELATIVE_PATH),
        &format!("{}\n", json_pretty_sorted(&manifest_json(&final_manifest))),
    )?;
    Ok(final_manifest)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::models::{EntityKind, GraphEntityRecord};
    use serde_json::json;

    fn entity(id: &str) -> GraphEntityRecord {
        GraphEntityRecord {
            entity_id: id.to_string(),
            entity_kind: EntityKind::Node,
            title: format!("Test {id}"),
            body: "Body".to_string(),
            properties: json!({}),
            revision: "r1".to_string(),
            payload: json!({}),
        }
    }

    #[test]
    fn stable_paths_use_id_digest() {
        assert!(safe_note_path(&entity("a")).starts_with("Notes/node/Test a--"));
        assert!(is_canonical_ledger_path(
            &safe_note_path(&entity("a")),
            "a",
            EntityKind::Node
        ));
    }

    #[test]
    fn writes_and_scans_round_trip() {
        let root =
            std::env::temp_dir().join(format!("kogwistar-source-test-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        let snapshot = GraphSnapshot {
            workspace_id: "w".into(),
            vault_id: "v".into(),
            graph_revision: "g1".into(),
            entities: vec![entity("a")],
            relations: vec![],
        };
        let manifest = write_snapshot(&snapshot, &root).unwrap();
        let scan = scan_vault(&root).unwrap();
        assert!(scan.valid());
        assert_eq!(scan.manifest.unwrap().digest, manifest.digest);
        fs::remove_dir_all(&root).unwrap();
    }
}
