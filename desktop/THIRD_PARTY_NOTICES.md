# Fieldwork bundled software notices

Fieldwork application code is MIT licensed; see the repository LICENSE. A built application contains a full notice collection at `Contents/Resources/licenses`, including the exact installed Python package versions and original license files.

- CPython 3.12.13, from Astral's python-build-standalone 20260610 Apple Silicon build, under the PSF license and component licenses. The complete upstream distribution notice set is retained in `desktop/licenses`; it is intentionally broader than the subset of CPython modules actually frozen into Fieldwork. The package also retains CPython's third-party acknowledgements.
- Flask, Werkzeug, Jinja2, MarkupSafe, click, blinker, itsdangerous: BSD/MIT family licenses; see copied package files for exact terms and versions.
- pdfplumber, pdfminer.six, openpyxl, et_xmlfile: MIT licenses. pypdfium2 is Apache-2.0/BSD-3-Clause; its bundled PDFium has BSD and transitive notices, all copied from the wheel's license tree.
- Pillow uses the HPND license and bundled image-library notices. cryptography uses Apache-2.0/BSD-3-Clause and statically includes OpenSSL; OpenSSL 4.0.3's Apache-2.0 text is retained. CPython's bundled OpenSSL 3.5.7 notices are also included. cffi, pycparser and charset-normalizer original notices are copied from their installed distributions.
- The PyInstaller bootloader is used under its GPL license with the exception permitting distribution of frozen applications under their own license. Its COPYING.txt is retained. Build-tool package notices are included for completeness and do not change Fieldwork's MIT license.
- Cocoa, WebKit, Vision and Core Image are Apple macOS system frameworks. Their binaries are not redistributed by this repository. Offline OCR uses the operating system's Vision APIs, not a separately downloaded AI model.

`desktop/licenses/sources.json` records upstream source URLs and SHA256 values of retrieved notice texts. The packaging script copies source notices without rewriting their terms. This notice index summarizes the collection; the original full texts govern.
