# Fieldwork v1 JSON API contract

All `/api` paths except health/session require a signed local session cookie or `Authorization: Bearer <api.key>`. Cookie mutations require `X-CSRF-Token` from authenticated state. Host must be localhost/127.0.0.1 and Origin, when present, must match the loopback origin. Browser UI first launch receives a one-time launcher token through `/?token=...`; it is consumed, sets an HttpOnly SameSiteStrict cookie and redirects. An explicit local API key can alternatively be entered through POST `/api/session` `{token}`. No network calls or remotely reachable listener.

GET `/api/state` => `{csrfToken,version,documents,templates,defaultTemplate,jobs,watches,settings,samples,ocrAvailable}`. Collections include archived records for client filtering. Document result payloads supplied in detail/state for v1. Source pages `{page,width,height,lines:[{text,bbox}],ocr}`. Result `{fields,items,evidence,issues:[{code,path,message,severity}],status,text_pdf,ocr}`. Document `{id,name,status,archived,templateId,templateVersion,createdAt,updatedAt,pages,result,reviewRevision,duplicateOf,duplicateDecision,approvalNote,job}`. Job `{id,documentId,templateId,status:'queued'|'running'|'completed'|'failed'|'cancelled',progress,error,createdAt}`.

Templates use `{id?,name,version?,fields:[{name,label,type:'text'|'number'|'date'|'currency'|'email',required,anchor,position:'after'|'below'}],table:{enabled,header_anchor,end_anchors:[string],ignore_prefixes:[string],columns:[{name,label,type,required,x0,x1}]},validation:{enabled,quantity,unit_price,line_total,subtotal,tax,total},duplicate_fields:[fieldName],export_columns:[{source:'fields.name'|'items.name'|'source_file',label}]}`. Geometry is normalized page width0..1. One optional repeating table per template. Versions immutable per extraction. Header/table names are simple stable identifiers; labels editable. Validation roles reference configured field/column names; arithmetic checks optional for nonfinancial documents.

| Method/path | Body/response |
|---|---|
| GET `/api/health` | Minimal `{application:'fieldwork',version,ready}` |
| POST `/api/session` | `{token}`; creates local cookie |
| POST `/api/logout` | Clears session |
| POST `/api/upload` | Multipart `files` PDF/PNG/JPEG/TIFF; optional `templateId` queues extraction; `{ids}` |
| POST `/api/samples` | `{name}` from state's advertised names; `{id}` |
| GET `/api/documents/:id` | Full document DTO |
| POST `/api/documents/:id/archive` | `{archived:boolean}`; queued work cancelled before archiving |
| POST `/api/documents/:id/review` | `{fields,items,revision}`; rows may add/delete; rejects stale revision409; approval revoked |
| POST `/api/documents/:id/approve` | `{revision,override?:boolean,note?:string}`; unresolved issues require explicit reason, empty extraction not approvable |
| POST `/api/documents/:id/revoke` | `{revision}`; returns to validation/review state |
| POST `/api/documents/:id/duplicate` | `{decision:'keep'|'ignore',note}`; keep is explicit and auditable; ignore excludes export |
| GET `/api/documents/:id/page/:n` | Source PNG (upright if OCR normalized) |
| GET `/api/documents/:id/source` | Original download |
| GET `/api/documents/:id/audit` | Audit history including extraction/review/approval/duplicate events |
| POST `/api/templates` | `{id?,template}`; updates create next version; `{id,version}` |
| POST `/api/templates/:id/archive` | `{archived:boolean}`; past versions retained |
| GET `/api/templates/:id/export` | Portable versioned template JSON |
| POST `/api/templates/import` | `{template}` same validation; always creates new template |
| POST `/api/jobs` | `{templateId,documentIds}` max100; queues durable jobs; `{jobs}` |
| POST `/api/jobs/:id/cancel` | Cooperative cancellation; never exports partial results |
| POST `/api/jobs/:id/retry` | Requeues failed/cancelled job against saved template snapshot |
| POST `/api/export` | `{format:'csv'|'xlsx'|'json',documentIds?:[],columns?:[{source,label}]}`; returns downloadable file; ONLY approved, nonarchived/nonignored records; if omitted select all approved |
| POST `/api/watches` | `{id?,path,templateId,enabled}`; explicit existing chosen absolute folder; shallow polling only, never moves/deletes originals |
| DELETE `/api/watches/:id` | Disables/removes watcher configuration, retains imports |
| POST `/api/settings` | `{onboardingDone?,defaultTemplateId?,exportColumns?}` |
| GET `/api/backup` | ZIP of DB+source+normalized artifacts, no credentials; authenticated download |
| POST `/api/backup/restore` | Multipart `backup`, `confirm=RESTORE`; validates manifest/paths/size; automatic pre-restore snapshot, requires no running jobs; preserves current API key |
| POST `/api/security/key` | Explicit user action returns `{token}` current agent API key (never part of state) |

Duplicate `keep` means Keep both for the current conflict membership/identifier fingerprint, not all future copies. Approval stores the accepted issue fingerprint. Export revalidates under the operation lock; new duplicate conflicts or changed issues revoke affected approvals and return409 `{error,blockedDocuments:[{id,name,issues}]}` after committing the review status. The entire requested export is cancelled, with no partial file or silent omission. Existing explicit exception approvals cover only their recorded issue set.

CSV/XLSX honor column mappings. JSON preserves original structured field names, items, approval labels, issues and review notes. `review_note` is also a supported CSV/XLSX column source. Document DTO includes `templateSnapshot` for immutable extraction labels/columns. All OCR results require review; intentional overrides receive `approved_with_exceptions`, not `approved`.

Errors JSON `{error}`. No exported rows leave the Mac automatically. Jobs resume as queued after restart; cancelled jobs remain cancelled. Watch polling happens only while app runs. UI polls state every2seconds while jobs active (slower otherwise).
