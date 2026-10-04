# Independent packaged-product acceptance

Build the app with `./scripts/package-mac.sh --app-only`, then run:

```
.packaging-venv/bin/python tests/acceptance_product.py
```

The test starts the actual bundled backend with PATH restricted to system tools. It creates uniquely named isolated profiles under ignored `work/`, never the real Application Support workspace. All inputs in `tests/fixtures` are synthetic. Tests check exact fields/ordered rows against an independent expected manifest, authentication, custom nonfinancial templates, OCR review, row changes, revision conflicts, export mapping/formula escaping, duplicate decisions, cancellation/retry, watch ingestion, restart recovery, and backup restore with separate credentials. Raw outputs/reports go to ignored `qa/`. The first prototype report and first product failure evidence remain unchanged in the experiment/QA directories.

These fixtures are regression cases after the first observed product pass, not a fresh blind benchmark on every run. OCR labels in the image are clear English; low-quality text is explicitly held for review. This suite is not an arbitrary-PDF accuracy claim.

`tests/fixtures/product_cases.py` regenerates the synthetic files using reportlab plus the runtime PDFium/Pillow packages. reportlab is a developer test-generation dependency, not bundled with the user app. Native GUI acceptance and DMG mount/copy/quit/reopen tests are separate evidence in the final release report.
