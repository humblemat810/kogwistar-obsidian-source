# Kogwistar Obsidian Source

`kogwistar-obsidian-source` is an optional LLM-Wiki plugin for deterministic,
revision-safe synchronization of a managed Obsidian vault.

The existing `kogwistar-obsidian-sink` remains the one-way projection package.
This package is the separate source-side contract. It does not treat arbitrary
Markdown, canvases, or missing files as graph mutations.

## Safety model

The normal flow is:

```text
export -> scan -> plan-import -> explicit apply-import
```

Only files listed by the signed-in host's managed manifest are considered.
Graph and vault edits made from the same baseline are reported as conflicts.
The default deletion mode is `ignore`; `tombstone` must be explicitly selected.
The plugin never accesses a Kogwistar engine directly. LLM-Wiki supplies the
host callbacks that perform ACL checks and revision-fenced maintenance patches.

Managed notes contain JSON frontmatter between YAML delimiters. JSON is valid
YAML frontmatter and lets this package parse the format without a YAML runtime
dependency.

The Workbench entry point is named `obsidian-source` and is activated only when
`LLM_WIKI_WORKBENCH_EXTENSIONS=obsidian-source` is configured.

