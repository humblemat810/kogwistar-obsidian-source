# Rust Port Fine Tracklist

| Task | Implementation Detail | Status | Gate |
| --- | --- | --- | --- |
| Canonical JSON and digest | Mirror `canonical.py` in `canonical.rs`; sorted keys, compact separators, UTF-8-safe JSON, SHA-256, top-level `digest` omission | DONE | Canonical unit tests |
| Model validation | Mirror `models.py`; required strings, lower-case SHA-256, entity kind, format version, distinct relation endpoints | DONE | Model validation tests |
| Snapshot load and validation | Mirror `models.py` plus `cli.py::load_snapshot`; format version, required identity fields, unique entity/relation IDs, known endpoints | DONE | Snapshot fixtures |
| Stable paths | Mirror `vault.py`; sanitized titles, Windows-safe names, entity-id digest suffix, canonical ledger path validation | DONE | Path tests |
| Note render and parse | Mirror `vault.py`; sorted frontmatter, canonical relation block, body trim, relation digest, marker conflict detection | DONE | Round-trip test |
| Manifest write and scan | Mirror `vault.py`; atomic writes, digest validation, duplicate path/entity detection, missing/unmanaged diagnostics, path-safety checks | DONE | Scan and round-trip tests |
| Revision-aware planning | Mirror `sync.py`; grouped/sorted relations, graph versus vault change detection, typed intents, tombstones | DONE | Planning test pending |
| Conflict classification | Mirror `sync.py`; invalid vault, identity mismatch, both-changed, invalid relation endpoints, duplicate relation IDs | DONE | Planning test pending |
| Plan payload and identity | Mirror `sync.py`; canonical payload, provisional zero digest, content-addressed `plan_id` | DONE | Golden payload test pending |
| CLI parity | Mirror `cli.py` commands and exit codes for `export`, `scan`, `plan-import`, `conflicts`, `apply-import` | IN PROGRESS | CLI fixture suite |
| Host apply-import seam | Define embedded/native Kogwistar host contract; keep fail-closed behavior; validate plan identity, deletion mode, workspace and vault | DONE | Host contract plus integration test |
| Workbench extension | Mirror `plugin.py` namespaced route and write-scoped apply handler | DONE | Python plugin contract parity |
| Cross-language parity | Golden fixtures for canonical digest, manifest, note rendering, scan diagnostics, plans and conflicts | TODO | Python/Rust fixture suite |
| CI | Add fmt, clippy, test and cross-language parity workflow | TODO | Workflow green |
