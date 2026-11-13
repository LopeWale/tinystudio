# Corrected supervisory SLM documents

Editorial revision: 12 November 2026. The original three attachments are preserved at their upload locations. These corrected editions address the five issues selected by the user; they do not report new experimental results or hardware qualification.

- [Corrected paper (PDF)](Supervisory-Small-Language-Models-for-Hardware-Corrected.pdf)
- [Corrected lab guide](Lab-Guide-Supervisory-SLM-Testbed-Corrected.md)
- [Corrected interactive Explorer](Supervisory-SLM-Explorer-Corrected.html)
- [General experimentation principles](../../experimentation-principles.md)
- [Revised alignment review](Tiny-Studio-Alignment-Review-Revised.md)
- [Source/output checksums and exact PDF text replacements](correction-manifest.json)
- [Correction validation](VALIDATION.md)

## Corrections

| Issue | Correction |
| --- | --- |
| Explorer's incorrect Modbus CRC | Request ends in `C5 C1`; the Explorer computes CRC-16/Modbus before labeling it valid. RTU and Modbus TCP framing are distinguished. |
| Fixed SunSpec control address | Paper §6.3, Explorer and guide use runtime model-chain discovery, Model 123 field definitions, scaling, enable state and device capabilities. Absolute address 40021 is no longer prescribed as a control register. |
| Bounds/prompt claims | Paper H1, §§3.2–3.3 and the illustrative listing distinguish numeric validation from stability and safety; prompt priority is measured. Explorer defaults to clamp/drop behavior and labels reject as an optional comparison. |
| Thermal pre-cooling | Paper text and diagram, Explorer and guide treat cooling as unproven. Measure panel/converter temperature and whole-window net energy; retain warming and inconclusive results. The guide distinguishes a timed detuning intervention from an open-circuit diagnostic. |
| Series-string apparatus and sensing | The guide distinguishes the 24 V reference bench from a nominal 60.9 V three-panel series rig; provides simulation, lower-voltage-module and separately rated apparatus variants; uses per-panel voltage/common current/bypass state rather than a lowest-series-current rule. It identifies isolation, rated switching and load-channel requirements that were absent from the original parts list. |

## Product interpretation

These documents are one editable use case for Tiny Studio. Their models, controller limits, sample counts and endpoints are not universal platform rules. The researcher owns the study. General tracking/data collection and auditable statistical planning/analysis, especially sample-size and power computation with assistant support, are priority tooling requirements. The product principles record this direction without claiming the tooling is already implemented.

## Rebuilding

The PDF source was supplied as a PDF, not an editable authoring document. The build script replaces bounded text regions while preserving the original 23 pages and diagrams, corrects the thermal diagram caption, and adds a revision line. It also builds the Markdown and HTML editions from their original sources. The original sources are identified by SHA-256 in the manifest.

```bash
python build_editions.py --source-dir /path/to/original-attachments --output-dir /path/to/output
```

Use an environment with PyMuPDF installed. The source directory must contain the original numbered filenames recorded by the build script. Content assertions stop the build if a textual source region does not match. Outputs never overwrite the original sources. Verify generated text and rendered PDF/HTML when changing the editorial script.

The prior vendor citations and price observations are retained from the original guide; this revision did not independently revalidate the entire vendor list. Newly identified apparatus requirements are explicit, without invented part selections or prices.
