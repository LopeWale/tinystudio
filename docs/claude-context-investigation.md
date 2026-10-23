# Claude Desktop context investigation

Investigated 2026-10-23, static shipped artifact only. Claude **3.6556.1**, confirmed in `Info.plist` and packaged `package.json`. No running Claude control, user content, or credentials were read.

## Scope and provenance

Target: `/Applications/Claude.app/Contents/Resources/app.asar`.

REA `analyze_javascript_application` was invoked on the original ASAR. It failed with `artifact_integrity_mismatch` for unpacked `node_modules/@ant/claude-native/claude-native-binding.node`: declared SHA-256 `2f423b6013cec753eefd174418b9517ecc39b0025e26b9c16789d0c0d4cadd86`, calculated `2cd3824a40d38664ca7c701e7f3cf84d291b220073a48e4f10d3679678f64696`. This establishes a hash mismatch, not its cause. No valid REA Evidence ID was returned by that call.

For bounded source inspection, packaged `.vite/build/*.js` and `package.json` were copied into OS temporary storage using ASAR header offsets. Every extracted member with a declared integrity hash was checked against that hash. The original installation was unchanged. A second REA analysis of this partial extracted tree was invoked and ended with `timed out awaiting tools/call after 300s`. It returned no application graph or Evidence ID. Both REA attempts therefore ended without usable analysis Evidence; the bounded findings below come from direct inspection of integrity-checked packaged JavaScript.

All code locations below refer to `.vite/build/index.chunk-CI-UZNsz.js` inside that ASAR, SHA-256 `d5b42ed5da39dd1b0afad78d3c0c51b99fed6c6cbe99386ea012a8ae26f3311c`. Offsets are zero-based decoded JavaScript string offsets from Python, not byte addresses or line numbers. The shipped bundle is minified.

## Observed static call paths

| Question | Observation | Location |
| --- | --- | --- |
| Invoke Claude from elsewhere | Native setup registers `optionDoubleTapped`; its handler invokes Quick Entry activation. Supported macOS paths call Swift `quickAccess.overlay.toggle()`. | approximately 3973530–3975570 and 3976500–3977350 |
| Native or browser overlay? | macOS has a Swift overlay through `@ant/claude-swift`. An alternate path constructs a transparent, frameless, fixed-size Electron panel, always on top; it stores position relative to a display and hides on blur. Runtime capability decisions determine the path. | approximately 3972200–3975570 and 4228600–4236500 |
| Attach desktop context | `desktop.getOpenWindows(...)` supplies windows. Mention search filters titles and application names; selection resolves cached window metadata. | approximately 3979250–3980000 and 3986150–3989610 |
| Screenshot payload | Selecting an on-space window calls `desktop.captureScreenshot(windowId)` when sharing permits. A result becomes a JPEG attachment with base64 `extracted_content`, filename, estimated size and `origin: "user_upload"`. Off-space windows return a chip without this screenshot capture. | approximately 3988850–3990800 |
| Document payload | If the window exposes `representedFile` and file sharing permits, the handler reads the represented file and appends an attachment. | approximately 3989950–3990900 |
| Native Quick Entry submission | The JS listener receives `{prompt, images, filePaths, chatId}`. Images become JPEG base64 entries. Its log describes max-dimension 800px resizing; native resizing itself was not inspected. File paths must resolve to regular files, with a 30 MiB limit, and become base64 payload entries. | approximately 3977570–3979230 |
| Handoff into conversation | Quick Entry dismissal/submission dispatches text/images via `dispatchOnQuickEntrySubmit` into the main web view, supporting a supplied chat ID or a new chat. Submission stamps account identity and later drops the payload if the active account changed. | approximately 4233250–4236500 |
| Sharing policy boundary | Screenshot/file booleans are sent into Swift `desktop.setQuickEntrySharing`; native Quick Entry eligibility checks whether policy settings have been confirmed. | approximately 3973350–3975200; settings getter approximately 3349820–3350090 |

## Inferences and unresolved questions

The observed architecture supports a reusable product design: an invoked panel gathers explicit window/file/image attachments, preserves them as reviewable payloads, then hands the prompt into a selected conversation. This is a design inference, not source equivalence or runtime proof.

Browser windows can participate in the observed window enumeration/screenshot path. This pass does **not** establish semantic browser-page extraction, selected-text capture, automatic reading of the foreground app, or how Swift chooses screenshot/context sources. Native implementation internals, accessibility details, permission prompts, UI appearance, actual shortcut defaults, current remote flags, and runtime success remain unverified. A bounded search did not find a `getSelectedText` implementation in the extracted build JavaScript; that does not establish absence from native code.

This report covers focused shipped JavaScript call paths. It does not claim that Claude Desktop was completely reverse engineered or that any inspected path executed.
