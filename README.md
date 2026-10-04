# Fieldwork

Fieldwork turns recurring PDFs and scanned documents into reviewed spreadsheet data on your Mac. Teach a template from a representative document, reuse it for a batch, check the source alongside the extracted values, then export approved records.

All extraction, OCR, validation and storage run locally. No account, hosted server, paid API, subscription or downloaded AI model is required. The Mac app bundles its Python processing engine and uses Apple's on-device Vision OCR. The included example is fictional and optional; a new workspace starts empty.

## Install on a Mac

The v1 build supports **Apple Silicon Macs running macOS 13 or later**. Intel Macs, Windows and iOS are not included.

1. Open `Fieldwork-1.0.2-mac-arm64.dmg` and drag the Fieldwork icon along the arrow to Applications.
2. Open Fieldwork from Applications. You do not need Python, Node, pip or a terminal.
3. Choose **Import documents** to use your files, or try the optional example.

To update an existing installation, quit Fieldwork, replace only the app in Applications, then reopen it. Documents, templates and settings remain in the separate Application Support workspace. Version 1.0.2 adds an Invoice starter and a clearer installer; it does not change the database format or alter existing templates automatically.

This initial distribution is ad-hoc signed and not notarized. If macOS blocks it, verify that the file came from the intended source and that its SHA256 matches the supplied checksum. Use macOS's per-app **Privacy & Security → Open Anyway** option if available and you trust that build. Do not disable Gatekeeper globally. Local acceptance on the build Mac does not establish downloaded-app acceptance on every Mac.

## The working flow

1. **Documents:** import PDF, PNG, JPEG or TIFF files. Select documents and a template, then process the batch. Progress, failures, cancellation and retry are visible; unfinished jobs resume after the app reopens.
2. **Templates:** start with Invoice, Purchase order or Blank, then create a named template with your own header fields. Supported types are text, number, currency, date and email. Set required fields, literal text anchors, and whether a value appears after or below its label. Click source text to teach an anchor.
3. For a repeating table, enable the table, set its header/end markers and column definitions, and adjust the column boundaries against the source. The Invoice starter detects columns from complete Quantity, Description, Unit price and Amount headings and their configured aliases, including reordered columns; choose fixed columns for other layouts. Repeated headers and configured footer prefixes are excluded across pages. One optional repeating table is supported per template.
4. Enable financial validation when useful. It checks quantity × unit price, the sum of line amounts against subtotal, and subtotal + tax against total using exact decimal arithmetic. Disable it for forms, price lists and other nonfinancial documents.
5. **Review:** compare values with highlighted source evidence. Correct fields or cells, add missing rows or remove spurious rows, then save and recheck. The original file and raw extraction remain preserved. Editing or reprocessing revokes approval. Template updates create a new version; previous documents retain their extraction's template snapshot.
6. Resolve duplicate warnings explicitly with **Keep both** or **Ignore**. Keep both covers the current conflict set only. A later new copy requires another review. Ignoring a document excludes it from export and keeps its original available.
7. **Approve** only after reviewing the result. Unresolved warnings require a written exception reason; these records are labelled **Approved with exceptions**, never mathematically validated. Every OCR result requires review. Empty extractions cannot be approved.
8. **Export:** CSV and Excel support chosen columns and headings; API callers can also set column order. JSON preserves the structured fields, rows, approval label, issues and review note. Only approved, nonarchived, nonignored documents export. If a new duplicate or issue invalidates an earlier approval, the entire export is stopped with named documents to review; nothing is silently omitted.

Search and filter the document library, or archive documents without deleting their originals. Templates can be exported/imported as portable JSON. The activity log retains extraction, edit, approval and duplicate decisions.

For an invoice, import the file and choose **Use invoice template**, or **New template → Invoice**, then **Save & process**. This makes a new template instead of changing a saved purchase-order template. Review Bill to, Invoice number, Invoice date, the rows and the footer values against their highlighted source. The Invoice starter supports literal labels beside other text and wrapped item descriptions. Ambiguous labels or missing headings remain review issues.

Invoice dates use **Detect unambiguous dates** by default: `6/24/26` becomes `2026-06-24`, while `6/7/26` requires an explicit day/month or month/day choice, or a reviewed correction. The starter explicitly interprets two-digit years as 2000–2099; four-digit years avoid that convention. Existing templates remain ISO-only unless you choose another date format. The original source text and normalized date evidence are retained.

Tax and Invoice total are optional in the Invoice starter and remain blank when not printed. Balance due is separate from Invoice total because payments or credits can make them different. VAT registration numbers are not tax amounts. Arithmetic checks only use the printed/configured values; the app does not infer missing tax or a grand total to make a document pass.

## Watch folders, backups and local automation

**Watch folders** imports files directly inside folders you explicitly choose. Fieldwork waits for a stable size/modification time and a readable file before processing. It never moves, edits or deletes the originals and does not scan subfolders or your home directory. Errors remain visible; one malformed file does not prevent other files from importing. Pause, resume, edit or remove watches at any time.

Watching and processing run only while Fieldwork is running. Closing the window keeps the app alive; **Cmd-Q** stops it. Reopening restores the workspace and resumes pending work. No login item, background daemon or cloud service is installed.

Use **Settings → Backup & restore** to download a workspace ZIP. It contains the database, imported originals and normalized OCR source images; access keys are excluded. Restore replaces the current workspace only after making a safety backup. Finish/cancel jobs and pause watches first. Restored watches remain paused until you review their paths and enable them. Backups are sensitive business data; store them somewhere you control.

The default workspace is `~/Library/Application Support/Fieldwork`. Replacing the app does not replace this directory. **File → Show Workspace in Finder** opens it. Do not edit the SQLite database while the app is running.

Every core workflow is available through the authenticated local [JSON API](API.md). **Settings → Agent access → API connection details** shows the current loopback URL; the native app chooses a free port each launch. Reveal the local API key only for a trusted tool. The key grants full workspace access, is stored outside the repository, and is never part of state responses or backups. The [agent skill](agent-skill/SKILL.md) documents safe API use; it does not install or enable any remote agent.

## Supported limits

- Recurring layouts with readable anchors and regular column geometry are the intended use. A template is not an arbitrary-document semantic parser.
- English OCR is included for printed scans/images, with cardinal rotation correction and source evidence. Handwriting, faint/low-resolution scans, merged/irregular tables and unfamiliar layouts may need substantial correction. Confidence scores do not bypass review.
- PDF files must be unlocked, with at most 100 pages. Uploads are limited to 25 MB per file and 20 files per request; batches can queue up to 100 documents. Rendered pages are capped at 40 megapixels and 16,000 pixels per edge.
- Templates support 1–40 header fields and one optional table. Dates must resolve to `YYYY-MM-DD`; currency precision is checked rather than silently rounded into correctness.
- Restore currently accepts a ZIP up to 300 MB and a total uncompressed size up to 1 GB. This is a local single-user workspace, not a multi-user server or synchronized cloud product.
- Validation assists source review; it does not establish a document's authenticity or guarantee every extracted value is correct.

## Run or build from source

These requirements apply to developers, not people installing the DMG. Source startup needs Python 3.12+ and a first-time internet connection to install pinned dependencies. The production Mac build uses managed Python 3.12.13 plus Xcode command-line tools and `uv`.

```sh
git clone git@github.com:ella-zanette/fieldwork.git
cd fieldwork
FIELDWORK_DATA_DIR="$PWD/work/development" FIELDWORK_PORT=4341 ./run.sh
```

Open `http://localhost:4341`. For source development only, use the generated `work/development/api.key` at the sign-in screen; do not commit or share it. `run.sh` creates an isolated virtual environment and repairs/install-checks locked dependencies on each run. It has no runtime network requirement once dependencies are installed. Source OCR needs the compiled helper configured through `FIELDWORK_OCR_BINARY`; the packaged app includes it automatically.

```sh
.venv/bin/python -m unittest discover -s tests
node --test tests/*.mjs
./scripts/package-mac.sh --app-only
.packaging-venv/bin/python tests/acceptance_product.py
./scripts/package-mac.sh
```

The frontend regression command requires Node 20 or later on the developer machine; Node is not an application runtime dependency. The release script writes the app, DMG, SHA256 and binary compatibility audit under ignored `release/`. See [Mac build details](desktop/BUILD.md) and [packaged acceptance](tests/ACCEPTANCE.md). Tests use only synthetic documents and isolated profiles, never the real application workspace. Freshly generated acceptance reports and test profiles stay out of source control.

The application uses Flask, SQLite, pdfplumber/PDFium, Pillow and openpyxl with pinned dependencies in `requirements.lock.txt`; the native shell uses Cocoa/WebKit and Vision. App source is [MIT licensed](LICENSE). Bundles include the upstream runtime/dependency notices and version inventory under `Fieldwork.app/Contents/Resources/licenses`.
