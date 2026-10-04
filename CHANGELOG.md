# Changes

## 1.0.1

Fixed normal file-picker imports incorrectly showing “Choose PDF, PNG, JPEG or TIFF files” for a supported document. The browser's live file selection is now captured before any asynchronous confirmation or input reset. This also preserves dropped files while the discard-changes confirmation is open and permits selecting the same file again.

No database migration or workspace reset is required. Quit Fieldwork before replacing the app; documents and templates stay in Application Support.

## 1.0.0

Initial offline Mac release with versioned extraction templates, local OCR, source review, approved exports, watch folders, backups and authenticated local API.
