use serde_json::{Map, Value};
use sha2::{Digest, Sha256};
use std::collections::HashSet;

pub const FORMAT_VERSION: &str = "kogwistar-obsidian-source/v1";

fn write_canonical(value: &Value, output: &mut String) {
    match value {
        Value::Null => output.push_str("null"),
        Value::Bool(value) => output.push_str(if *value { "true" } else { "false" }),
        Value::Number(value) => output.push_str(&value.to_string()),
        Value::String(value) => {
            output.push('"');
            for character in value.chars() {
                match character {
                    '"' => output.push_str("\\\""),
                    '\\' => output.push_str("\\\\"),
                    '\u{08}' => output.push_str("\\b"),
                    '\u{0C}' => output.push_str("\\f"),
                    '\n' => output.push_str("\\n"),
                    '\r' => output.push_str("\\r"),
                    '\t' => output.push_str("\\t"),
                    value if value <= '\u{1F}' => {
                        output.push_str(&format!("\\u{:04x}", value as u32));
                    }
                    value => output.push(value),
                }
            }
            output.push('"');
        }
        Value::Array(values) => {
            output.push('[');
            for (index, value) in values.iter().enumerate() {
                if index > 0 {
                    output.push(',');
                }
                write_canonical(value, output);
            }
            output.push(']');
        }
        Value::Object(map) => {
            let mut keys: Vec<&String> = map.keys().collect();
            keys.sort();
            output.push('{');
            for (index, key) in keys.iter().enumerate() {
                if index > 0 {
                    output.push(',');
                }
                write_canonical(&Value::String((*key).clone()), output);
                output.push(':');
                write_canonical(&map[*key], output);
            }
            output.push('}');
        }
    }
}

pub fn canonical_json(value: &Value) -> String {
    let mut output = String::new();
    write_canonical(value, &mut output);
    output
}

pub fn canonical_digest(value: &Value) -> String {
    let mut digest = Sha256::new();
    digest.update(canonical_json(value).as_bytes());
    hex(&digest.finalize())
}

pub fn canonical_digest_omitting(value: &Value, omitted: &[&str]) -> anyhow::Result<String> {
    let object = value
        .as_object()
        .ok_or_else(|| anyhow::anyhow!("digest omission requires an object"))?;
    let omitted = omitted.iter().copied().collect::<HashSet<_>>();
    let filtered = object
        .iter()
        .filter(|(key, _)| !omitted.contains(key.as_str()))
        .map(|(key, value)| (key.clone(), value.clone()))
        .collect::<Map<String, Value>>();
    Ok(canonical_digest(&Value::Object(filtered)))
}

pub fn require_digest(payload: &Value, field: &str) -> anyhow::Result<()> {
    let supplied = payload
        .get(field)
        .and_then(Value::as_str)
        .ok_or_else(|| anyhow::anyhow!("invalid {field}"))?;
    let expected = canonical_digest_omitting(payload, &[field])?;
    if supplied != expected {
        anyhow::bail!("invalid {field}");
    }
    Ok(())
}

pub(crate) fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn canonical_json_is_key_sorted_and_unicode_preserving() {
        let value = json!({"b": 1, "a": "文"});
        assert_eq!(canonical_json(&value), r#"{"a":"文","b":1}"#);
    }

    #[test]
    fn digest_omits_only_top_level_field() {
        let value = json!({"digest": "ignored", "a": 1});
        let digest = canonical_digest_omitting(&value, &["digest"]).unwrap();
        assert_eq!(digest, canonical_digest(&json!({"a": 1})));
    }
}
