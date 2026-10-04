# Mac app packaging

`./scripts/package-mac.sh` builds an Apple Silicon `release/Fieldwork.app` and a versioned DMG. The **source build machine** needs Xcode command-line tools and uv. It installs pinned managed CPython 3.12.13/build packages; end users need none of these. Runtime dependencies come from `requirements.lock.txt`.

The native Swift shell starts a private PyInstaller one-folder backend at an ephemeral loopback port. A random per-launch, one-use token bootstraps an HttpOnly session; the webview uses a nonpersistent cookie store. Only the exact local origin may navigate or invoke native folder selection. External links are opened only after explicit user link activation. CSV/Excel/JSON/backup downloads use a native Save dialog. File imports use the native Open panel.

Data persists at `~/Library/Application Support/Fieldwork`, outside the application bundle. For isolated acceptance only, `FIELDWORK_TEST_DATA_DIR` overrides that path before startup/locking. Do not point tests at a real workspace. Closing the window leaves processing alive; Dock reopen restores it. Cmd-Q terminates the backend; pending work is recovered by the service on next launch. No login item or system daemon is installed.

`desktop/VisionOCR.swift` builds the offline English OCR helper. It normalizes EXIF and cardinal rotation and emits text/word boxes relative to the upright normalized PNG. Every OCR result requires source review: Vision confidence is not a guarantee of correctness. Images larger than 40 megapixels or 16,000 pixels on an edge are rejected. The backend additionally enforces upload/page/job limits.

The packaging script audits every bundled Mach-O's architecture/minimum OS and rejects developer-path dynamic dependencies. The declared minimum is macOS 13; actual runtime acceptance occurs on the build host and is reported separately. This is Apple Silicon only, not Intel or iOS.

No Developer ID signing identity is configured on the build machine. The artifact is ad-hoc signed and **not notarized**. The build verifies local signatures, but this does not prove clean-download Gatekeeper acceptance. Never disable Gatekeeper globally. Product distribution signing/notarization requires the maintainer's separately configured Apple Developer credentials, not an end-user subscription.

Original dependency licenses and version inventory are copied into `Fieldwork.app/Contents/Resources/licenses`. Apple Vision/WebKit/Cocoa are system frameworks. Runtime processing does not contact an AI, OCR or hosting service.
