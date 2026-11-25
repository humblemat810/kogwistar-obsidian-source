# ADR 0001: Native Rust Port for Obsidian Source

## Status

Accepted.

## Context

The Python plugin already owns the deterministic managed-vault contract. Rust must reuse that contract so cross-language vaults remain byte-for-byte compatible where possible. Kogwistar-agent work is owned separately and must not be duplicated.

## Decision

- Use an in-repo Cargo workspace under `rust/`.
- Keep Python packaging and entry points as the compatibility layer.
- Implement vault rendering, scanning, canonical JSON, revision fencing, and planning natively in Rust.
- Keep the Python-backed `apply-import` host seam outside Rust for this milestone; production calls may embed Kogwistar later.
- Preserve CLI names, flags, JSON schemas, and error categories.

## Consequences

Rust and Python can be compared with golden vault fixtures. The first `apply-import` implementation is intentionally fail-closed rather than silently bypassing host authorization.
