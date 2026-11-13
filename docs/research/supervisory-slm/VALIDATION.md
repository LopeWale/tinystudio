# Correction validation

12 November 2026. This records checks on the corrected documents, not experimental support for the paper or a newly qualified Tiny Studio runtime.

- Confirmed that all three original attachment checksums remain unchanged and all corrected output checksums match `correction-manifest.json`.
- Retained the PDF's 23 pages. Checked extracted replacement text and rendered the changed safety/arbitration, interlock, code, thermal/diagram and protocol pages for layout inspection. The build records 12 text-region replacements. Unedited content retains its original provenance and limitations.
- Checked that the code listing retains the upper clamp and `return params`; removed its overstated safety comment. Confirmed removal of the asserted panel-cooling sentence/caption and defect-free-execution claim, and inclusion of runtime SunSpec discovery and prompt/bounds limitations.
- Checked the guide's nominal 60.9 V calculation, cold-corrected apparatus distinction, per-panel voltage/common-current/bypass-state observations, and explicit treatment of sample counts as planning proposals.
- Verified Explorer JavaScript syntax with `node --check`.
- Executed the Explorer in local Chromium 156.0.8078.79 over a temporary loopback HTTP server. Confirmed default clamp, out-of-range clamping, optional rejection, malformed-output dropping under both policies, the exact eight request bytes, and calculated valid CRC display. Changed the last byte to `C4` in an in-memory counterexample and confirmed both displays report `[CRC-INVALID]`. No JavaScript page errors occurred. The installed Playwright browser bundle was unavailable and the system Chromium was used; direct `file:` navigation was disallowed by browser policy, so validation used loopback HTTP.
- `git diff --check` passed for tracked edits. No runtime application code, authentication or device authority behavior was changed. No commit or push was made.

Statistical planning and analysis, including assistant-supported sample-size and power calculations, are recorded as priority general capabilities in the experimentation principles and revised review. They have not been implemented by this documentation correction. No sample-size result, live controller result or new hardware measurement is claimed.
