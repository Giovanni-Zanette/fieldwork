---
name: fieldwork-local-documents
description: Use Fieldwork's authenticated local API to import recurring business documents, manage extraction templates, review results and export approved data. Applies when the user asks to work with their Fieldwork workspace.
---

# Fieldwork local workspace

Use the running application's JSON API, not direct SQLite or source-file edits. Read [the API contract](../API.md) for the specific endpoint and payload. The native app chooses a free loopback port at each launch; get its current base URL through Settings → Agent access → API connection details. Do not assume port 4341 outside a source-development session.

The user can reveal a key through Show local API key and configure it in a private local credential store. Authenticate with `Authorization: Bearer …`. Never write the key into this skill, a repository, shared note, command output or exported document. If access is unavailable, ask the user to open Fieldwork and configure access; do not bypass its authentication.

1. Read `/api/state` to identify the actual documents/templates and current job state.
2. For an invoice, consider the advertised Invoice preset in `templatePresets`; create a new template copy instead of renaming or overwriting a purchase-order template. Its inline labels and header-based columns are opt-in. Set an explicit date order for ambiguous dates; the starter interprets two-digit years as 2000–2099. Treat Bill to, Tax, Invoice total and Balance due as distinct source facts, and leave absent optional values blank.
3. Import only the user's selected files and use the intended saved template. Queue jobs, then inspect completion and issues. Retries use the original template snapshot; reprocessing with a changed template is a distinct request and revokes approval.
4. Compare extracted values with source evidence before proposing corrections. Keep absent/uncertain data visible; never invent values to make validation pass. Send the current `reviewRevision` with edits/approval and reread after a 409 conflict.
5. Duplicate decisions are explicit Keep both or Ignore with a reason. Keep both does not authorize future copies. Do not ignore records merely to obtain a clean export.
6. Approval is a meaningful action: obtain the user's instruction for approval, and never use an exception override to conceal a wrong amount. OCR always requires review; an approved exception remains labelled as such. Edits revoke approval.
7. Export only after the requested documents are approved. A 409 with `blockedDocuments` means the entire export was cancelled and named approvals were revoked due to new issues. Show those issues; do not retry by silently shrinking the document selection.

CSV/XLSX mappings use `columns`; JSON retains structured data, issues and approval notes. Save outputs to the user's intended local destination. Watch-folder setup must use a specifically chosen folder, never a broad home-directory crawl. Originals remain untouched. Backup restore replaces the workspace and requires explicit restore intent, paused watches and no active work; it retains the local credential and creates a safety backup. No cloud deployment or remote-agent installation is part of this skill.
