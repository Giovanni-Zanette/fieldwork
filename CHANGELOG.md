# Changes

## 1.0.3

Approval guidance now distinguishes results that cannot be approved from review issues that permit an explicit exception. Empty extractions and unresolved duplicates no longer offer a misleading reason override. Conflicts refresh the document without automatically reopening the approval dialog. The backend's approval and export safeguards are unchanged. Added a small Created by Zanette.ai credit. This is the first public GitHub release; existing workspaces are preserved.

## 1.0.2

Added an opt-in Invoice starter with invoice-specific labels, whole-label inline anchors, inferred table columns and wrapped-description handling. Unambiguous numeric dates normalize to ISO; ambiguous dates require a configured order or review. Missing tax and invoice total remain blank, and Balance due stays a separate field. VAT registration labels and item descriptions containing “Total” are excluded from financial-summary matching. Existing templates keep their previous matching and geometry.

Added a styled drag-to-Applications installer with saved Finder layout. Packaging verifies the copied app and final read-only disk image signatures without modifying the application bundle. No database migration or automatic changes to saved templates, documents or approvals.

## 1.0.1

Fixed normal file-picker imports incorrectly showing “Choose PDF, PNG, JPEG or TIFF files” for a supported document. The browser's live file selection is now captured before any asynchronous confirmation or input reset. This also preserves dropped files while the discard-changes confirmation is open and permits selecting the same file again.

No database migration or workspace reset is required. Quit Fieldwork before replacing the app; documents and templates stay in Application Support.

## 1.0.0

Initial offline Mac release with versioned extraction templates, local OCR, source review, approved exports, watch folders, backups and authenticated local API.
