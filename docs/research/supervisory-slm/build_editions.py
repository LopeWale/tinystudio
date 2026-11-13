"""Build corrected editions from the three originally supplied documents.

Usage: python build_editions.py --source-dir PATH [--output-dir PATH]
Requires PyMuPDF for surgical PDF text edits. Original files are never modified.
This is an editorial build script, not an experimental controller or study result.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f"Expected one source match: {old[:100]!r}; found {text.count(old)}")
    return text.replace(old, new, 1)


def correct_explorer(text):
    edits = [
        ('<p class="sub">The paper puts', '<p class="small muted">Corrected edition · 12 November 2026. These are study-specific mechanisms and examples, not platform-wide restrictions or validated experimental results.</p>\n  <p class="sub">The paper puts'),
        ('with zero safety-boundary violations and instant fallback on model fault.', 'with a test target of zero observed safety-boundary violations and measured fallback latency on model fault. Bounds alone do not prove stability.'),
        ('Local self-preservation sits in the system prompt as primary attention priorities; global goals arrive as Policy Envelopes. Nodes degrade to local survival heuristics when comms drop.', 'Local self-preservation is requested in the system prompt; global goals arrive as Policy Envelopes. Whether the model honors that priority and survives dropouts is measured; independent controller protections enforce the configured physical limits.'),
        ('Correlating Layer 1 micro-amps, impedance and switching noise with Layer 7 forecasts lets a solar SLM swarm recover energy that classical controllers leave behind.', 'Test whether correlating physical telemetry with weather forecasts recovers net energy beyond classical baselines. Score each proposed mechanism separately, including negative, inconclusive and untested outcomes.'),
        ('Two objective functions, arbitrated by token priority', 'Two objective functions, with arbitration to test'),
        ('Hardcoded as primary attention priorities in the Tier 2 system prompt: stability, minimal mechanical and thermal wear, immediate goals such as pump throughput or avoiding thermal throttling.', 'Expressed as priorities in the Tier 2 system prompt: stability, minimal wear and immediate operational goals. A prompt does not guarantee local safety; record the proposed decision and the independently protected committed action separately.'),
        ('The hardcoded local safety weight overrides the global request.', 'The intended response is to prioritize local safety over the global request. Test whether the model actually does so.'),
        ('The paper describes two ways to handle a breach; switch between them.', 'The corrected paper clamps valid out-of-range numbers and drops malformed output. The alternative reject policy below is a researcher-selected comparison, not a second paper requirement. This one-field illustration does not prove closed-loop stability.'),
        ('<button type="button" id="modeReject" aria-pressed="true">§3.3 pipeline: reject</button>', '<button type="button" id="modeReject" aria-pressed="false">Comparison policy: reject</button>'),
        ('<button type="button" id="modeClamp" aria-pressed="false">§5.1 listing: clamp</button>', '<button type="button" id="modeClamp" aria-pressed="true">Paper policy: clamp</button>'),
        ('<b>3 · Execution guardrails</b>Check every value against physical reality limits.', '<b>3 · Execution guardrails</b>Check the configured bounds. In-range gains may still destabilize a plant; assess state-dependent safety separately.'),
        ('"Maintain cell safety above all else."', '"Maintain cell safety above all else."\n            # Prompt priority is a testable instruction.'),
        ('chargeCurrentLimitA = 120.0\n<span class="c1">6 writeRegisters()', 'chargeCurrentLimitA = 120.0\n            # One-field clamp; not a stability proof.\n<span class="c1">6 writeRegisters()'),
        ('<h3>Nano-scale cell degradation</h3><p>Micro-amperage and impedance reveal micro-cracks and soiling; smart junction boxes reconfigure series-parallel sub-strings to isolate bottlenecked cells.</p>', '<h3>Cell degradation and bypass hypothesis</h3><p>Test degradation detection using identifiable voltage, current, impedance and bypass-state observations. Series-path current alone does not locate a weak panel; masked-panel tests are proxies for defects.</p>'),
        ('<h3>Predictive pre-cooling</h3><p>Ahead of a forecast clear-sky window, the inverter cycles MPPT out of phase so cells cool and meet peak sun at higher efficiency.</p>', '<h3>Thermal detuning hypothesis</h3><p>Test whether detuning before a clear-sky window changes panel temperature and net energy. Reduced electrical extraction can leave more absorbed energy as heat. Cooling is not assumed; measure panel and converter temperatures separately.</p>'),
        ('Detunes MPPT before the ceiling; packs non-critical frames into multi-second arrays; pre-overclocks cooling.', 'Tests an MPPT/cooling intervention; measures panel and converter temperatures and net energy; batches non-critical frames. No panel cooling benefit is assumed.'),
        ('Schemaless register payloads (0x03, 0x04, 0x1A, 0x2C) are mapped onto SunSpec Alliance solar data models.', 'A deterministic parser validates transport framing and discovers the SunSpec model chain before interpreting fields. RTU uses a trailing CRC; Modbus TCP uses an MBAP header and no RTU CRC.'),
        ('SunSpec register 40021, Active Power Limit, on the inverter', 'Discover SunSpec Model 123; resolve WMaxLimPct, its scale factor and WMaxLim_Ena from the model definition and device map. No universal absolute control address is assumed.'),
        ('A walkthrough of the corrected paper. Every figure, token and value on this page comes from the paper; the simulations run its stated rules on example inputs.', 'Corrected walkthrough of the paper, 12 November 2026. The examples illustrate configurable study mechanisms; they do not establish safety, stability or energy gains. The reject policy is an optional comparison. The companion lab guide distinguishes the 24 V reference bench from separately configured PV rigs.'),
        ("let mode = 'reject';", "let mode = 'clamp';"),
        ("{ h: 'C5', g: 4 }, { h: 'C4', g: 4 }", "{ h: 'C5', g: 4 }, { h: 'C1', g: 4 }"),
        ('<span class="tok t2">[CRC-VALID]</span></div>', '<span class="tok t2" id="frameCrc">[CRC-VALID]</span></div>'),
        ("{ tok: '[CRC-VALID]', d: 'Bytes 7 to 8, the frame check, which the paper tokenizes as valid.' }", "{ tok: '[CRC-VALID]', d: 'Bytes 7 to 8: CRC-16/Modbus, low byte first. The calculated CRC for 01 03 00 20 00 02 is C5 C1.' }"),
    ]
    for old, new in edits:
        text = replace_once(text, old, new)
    start = text.index('    if (mode === \'reject\') {')
    end = text.index("\n  $('#modeReject').addEventListener", start)
    text = text[:start] + '''    if (bad) {
      v.style.setProperty('--c', 'var(--red)');
      v.innerHTML = '<b>4 · Dropped; fallback requested</b>Malformed syntax is dropped under either selected policy. Actual fallback latency and plant safety require measurement.';
    } else if (a > 120 && mode === 'reject') {
      v.style.setProperty('--c', 'var(--red)');
      v.innerHTML = '<b>4 · Rejected under comparison policy</b>The optional reject policy drops this out-of-range proposal and requests fallback. This is an alternative experiment configuration.';
    } else if (a > 120) {
      v.style.setProperty('--c', 'var(--t1)');
      v.innerHTML = '<b>4 · Clamped to 120.0 A; then committed</b>The paper policy bounds the valid numeric proposal before Tier 1 receives it. This checks one field, not closed-loop stability.';
    } else {
      v.style.setProperty('--c', 'var(--green)');
      v.innerHTML = '<b>4 · Within the illustrated bound; committed</b>This proposal passes the one-field check. In-range parameter combinations can still be harmful to a plant.';
    }
  }''' + text[end:]
    marker = "  const by = $('#bytes');"
    text = replace_once(text, marker, '''  function modbusCrc16(bytes) {
    let crc = 0xFFFF;
    bytes.forEach(byte => {
      crc ^= byte;
      for (let bit = 0; bit < 8; bit++) crc = (crc >>> 1) ^ ((crc & 1) ? 0xA001 : 0);
    });
    return crc;
  }
  const frameBytes = BYTES.map(byte => parseInt(byte.h, 16));
  const crcValid = modbusCrc16(frameBytes.slice(0, -2)) === (frameBytes[6] | (frameBytes[7] << 8));
  GROUPS[4].tok = crcValid ? '[CRC-VALID]' : '[CRC-INVALID]';
  $('#frameCrc').textContent = GROUPS[4].tok;
  $('#frameCrc').className = 'tok ' + (crcValid ? 't2' : 'red');
  if (!crcValid) GROUPS[4].d = 'The trailing CRC does not match the calculated CRC-16/Modbus.';
''' + marker)
    return text


def correct_guide(text):
    text = replace_once(text, 'Nov 11, 2026 · @Emmanuel Akinwale', 'Nov 11, 2026 · @Emmanuel Akinwale\n\nCorrected edition: Nov 12, 2026. Original authorship and original sources are retained; the corrections below have not been hardware-validated.')
    text = replace_once(text, 'Chapter 0 is the Round 2 bench: two nodes, three tiers, 24 V or less.', 'Chapter 0 is a selected Round 2 reference bench: two nodes, three tiers, 24 V or less. Chapter 6 distinguishes single-panel tests from a separately specified series-string rig.')
    anchor = '### How each chapter is laid out'
    text = replace_once(text, anchor, '''### Scope of the examples

These chapters are editable study protocols, not Tiny Studio platform restrictions. The researcher chooses mechanisms, models, limits, baselines, sample sizes and analysis. The 24 V ceiling below belongs to the Chapter 0 reference bench; it is not a software-wide voltage cap. A differently rated rig is a separate documented apparatus, not an exception silently applied to this bench. Tracking, raw-data custody and statistical calculations should remain general tooling. Counts in this guide are proposed protocol values, not calculated sample-size requirements: select or revise them using the intended effect, variability, precision/power, design and independent experimental unit before collecting final data.

''' + anchor)
    text = replace_once(text, '- Keep every bus at 24 V or less, fused, and powered from a current-limited bench supply.', '- For the Chapter 0 reference bench and tests using its wiring, keep every bus at 24 V or less, fused, and powered from a current-limited bench supply. PV panels are independent sources, so the supply limit does not bound their open-circuit voltage. Chapter 6 specifies how the series-string experiment differs; do not connect its three 20.3 V panels in series to the 24 V bench.')
    text = replace_once(text, 'This class is not in the paper\'s design; a clamp alone does not stop it, so record what happens and treat it as a finding rather than a pass or fail.', 'A numeric clamp alone does not establish closed-loop stability. Record both proposed and committed parameters, the plant response and any independent protection. Keep this class separate from classes 1–5, with its own authored endpoint; report failures and any strengthened-interlock comparison rather than excluding unfavorable outcomes.')
    text = replace_once(text, '5. **Baseline:** tune a fixed PID on the same board by hand (or Ziegler–Nichols, then refine) and freeze those gains. This is also the 1× value in the limits table.', '5. **Baseline:** tune a fixed PID on the same board by hand (or Ziegler–Nichols, then refine) and freeze those gains. This is also the 1× value in the limits table.\n6. **Interpretation:** bounds validation and prompt priority do not guarantee physical safety or closed-loop stability. Keep independent physical protections and record proposal errors separately from committed-output errors. If using state-dependent interlocks, validated gain regions or slew constraints, declare them as experiment mechanisms and compare them explicitly; they are not assumed properties of a clamp.')
    text = replace_once(text, '3. **System prompt:** the paper\'s node template from §5.1, with L(x) first: "Maintain cell safety above all else." The model returns `{"heater-duty-pct": n, "mode": "NORMAL" | "SAFE-MODE-CURTAIL"}`.', '3. **System prompt:** the paper\'s node template from §5.1, with L(x) first: "Maintain cell safety above all else." The model returns `{"heater-duty-pct": n, "mode": "NORMAL" | "SAFE-MODE-CURTAIL"}`. This is an instruction whose compliance is measured, not a guaranteed attention priority. Record the original proposed duty/mode and any independent interlock override as separate outcomes.')
    text = replace_once(text, '2. **Correction to carry into your notes:** the paper\'s §6.3 names "SunSpec Register 40021 Active Power Limit". In a standard map starting at 40000, register 40021 falls inside Common Model 1\'s model-name string, and Model 123\'s address depends on which models come before it. The gateway must walk the model chain (read ID and length, skip, repeat) and find Model 123 at run time. This guide does not edit the paper; you may want to fix that sentence.', '2. **Discover the control address at run time:** record the chosen SunSpec base/address convention, validate `SunS`, then walk each model ID and length (next model = current ID address + 2 + declared length) until Model 123 or the end marker is found. Reject invalid lengths or missing/unsupported controls; do not guess an absolute address. Resolve WMaxLimPct, WMaxLimPct_SF and WMaxLim_Ena using the model definition, apply the declared scaling/enable semantics, and retain discovered addresses and actual write acknowledgements. The old absolute address 40021 can lie inside Common Model 1\'s model-name field. The corrected paper and Explorer now use discovery too. RTU frames carry the CRC low byte first; Modbus TCP uses its MBAP header, without an appended RTU CRC.')
    text = replace_once(text, '| 1. Sub-string bypass of weak cells | Yes, with masks standing in for defects | Fixed series string under MPPT |', '| 1. Sub-string bypass of weak cells | Simulation, or a separately specified series rig; masks proxy defects | Fixed series string under MPPT |')
    text = replace_once(text, '| 3. Thermal pre-cooling by detuning | Yes, and physics predicts the opposite | Panel held at MPP |', '| 3. Thermal effect of detuning | Yes; reduced electrical extraction may warm the panel | Panel held at MPP |')
    text = replace_once(text, '| Per-panel current monitor |', '| Current/power monitor (separate panels or string path) |')
    text = replace_once(text, 'The DPOW1 cannot be the panel converter here: its VIN limit is 16 V and one panel\'s Voc is 20.3 V. Use the electronic load instead.', '''The DPOW1 cannot be the panel converter here: its VIN limit is 16 V and one panel's nominal Voc is 20.3 V. Use the electronic load for the specified operating-point tests.

**Apparatus boundary for mechanism 1:** the three specified panels in series have nominal summed Voc = 3 × 20.3 = 60.9 V, before cold-temperature correction. That configuration is not the 24 V reference bench. Choose and record one of these study variants: (A) software-in-the-loop with the three-panel model; (B) a physical string built from appropriately lower-voltage modules whose worst-case summed Voc stays within the reference-bench limit; or (C) a separately rated physical string apparatus for these panels. For C, calculate cold-corrected maximum voltage and verify the load, meters, isolation/common-mode ranges, switching, wiring and protection as an entire assembly. The load's advertised 150 V and monitor's 85 V ratings alone do not qualify that assembly. Specify isolated/differential per-panel voltage sensing and a rated bypass mechanism; those parts and their costs are not included in the existing list. This is apparatus configuration owned by the experimenter, not a platform-wide prohibition on higher-voltage experiments.''')
    text = replace_once(text, '3. **Operating-point tests** (mechanisms 1 and 3): the electronic load in CV mode, set by Pi A over LAN.', '3. **Operating-point tests:** mechanism 3 uses a single panel and electronic load in CV mode, set by Pi A over LAN. Mechanism 1 uses the explicitly selected simulation or separately documented string apparatus above. A second simultaneous thermal operating point needs its own verified load/channel; do not assume the single listed load independently controls both panels.')
    text = replace_once(text, '5. All panel and sensor wiring to Pi A; Pi A logs every 1 s.', '5. Pi A logs every 1 s through the specified measurement interfaces. Separate panels may use their own current monitors. For a series rig, use the documented isolated/differential interfaces; do not join floating panel measurement references through a shared nonisolated I2C bus. Log per-panel voltages, common string current and bypass state, with timestamps and units.')
    text = replace_once(text, '2. The model receives HTM tokens built from the three Isc values, the temperature sensors and a weather token from the forecast for your location, and returns a tilt angle per row, a load setpoint, or a bypass decision.', '2. For tracking, the model receives HTM tokens built from the three separately measured Isc values, temperatures and a forecast available at decision time. For bypass decisions, provide per-panel voltage, common string current, bypass-state observations and available characterization; three series-path current readings are not independent fault-localization signals. Return the action appropriate to the authored experiment: row tilt, load setpoint or bypass decision.')
    start = text.index('**Mechanism 1, sub-string bypass.**')
    end = text.index('\n\n**Mechanism 2,', start)
    text = text[:start] + '''**Mechanism 1, bypass decision.** Use the selected simulation or separately documented physical string variant. Apply masks with recorded identities/transmittance to create 40 proposed mask/irradiance cases. Give the controller only observable per-panel voltages, common string current, measured bypass state and characterization available before its decision; keep the mask identity as evaluator-only truth. A series path carries a common current, so the previous "lowest current by more than 10%" localization rule is invalid. Freeze a voltage/IV-based rule baseline using development data before the held-out cases. Ground truth for the bypass decision is maximum string power from matched I–V sweeps with and without the specified panel bypassed, at comparable irradiance and temperature. Use a rated bypass switch in physical trials, not a live manual jumper operation. **Proposed endpoints:** identify the masked panel, decide whether to bypass it, and measure the resulting power difference separately. The guide's candidate criterion is ≥90% agreement with the sweep's better option, plus a paired comparison against the frozen observable-input baseline; select the sample size and statistical rule before final trials. Whole-panel masks/bypass are proxies, not evidence of sub-string micro-crack diagnosis.''' + text[end:]
    start = text.index('**Mechanism 3, thermal pre-cooling.**')
    end = text.index('\n\n**Mechanism 4,', start)
    text = text[:start] + '''**Mechanism 3, thermal effect of detuning.** Keep the claim falsifiable: detuning before a sunny interval may change panel temperature and energy yield, but cooling is not assumed. In an energy balance with comparable absorbed irradiance and heat transfer, reduced exported electrical power leaves more energy to dissipate as heat. Distinguish the panel from converter/inverter heating; a fixed-efficiency thermal function alone is not a measured detuned operating-point model. Compare MPP against a declared detuned operating point and, as a separate diagnostic arm, open circuit. Open circuit does not by itself reproduce a specific timing/frequency intervention. Use a verified independently controllable load/channel per simultaneous operating point, or preregister alternating blocks with a reference panel and matched weather. Log panel and ambient temperatures, irradiance/reference output, voltage, current, actuator/inference overhead and integrated energy over the whole intervention/recovery window. Swap panel/arm assignments. The original 10-day count is a planning proposal, not a powered sample-size calculation. Freeze a meaningful temperature margin, uncertainty method and energy endpoint before the final observations. Record cooler, warmer, equivalent or inconclusive outcomes; warming contradicts the cooling prediction under the tested conditions and does not settle every possible thermal intervention.''' + text[end:]
    text = replace_once(text, 'Two findings in the paper are worth fixing while you are in it: the SunSpec "Register 40021 Active Power Limit" address (Chapter 5, step 2 of the software setup), and the pre-cooling mechanism, which the standard PV thermal model predicts works the other way (Chapter 6, mechanism 3).', 'The corrected editions address runtime SunSpec discovery, the distinction between numeric bounds/prompt instructions and safety or stability guarantees, and thermal detuning as an experiment with no assumed cooling benefit. Chapter 6 now separates the 24 V bench from the 60.9 V nominal series option and uses voltage/bypass-state observations for localization. No new hardware validation or successful experiment is implied.')
    return text


def edit_pdf(source, target):
    import fitz
    doc = fitz.open(source)
    edits = []

    def replace_region(page_number, rect, text, font='helv', size=10.5, minimum=9.5):
        page = doc[page_number - 1]
        rect = fitz.Rect(rect)
        # Remove old text only; retain diagram lines, fills and raster graphics.
        page.add_redact_annot(rect, fill=False)
        page.apply_redactions(images=0, graphics=0, text=0)
        actual = size
        while actual >= minimum:
            shape = page.new_shape()
            spare = shape.insert_textbox(rect, text, fontname=font, fontsize=actual,
                                         lineheight=1.25, color=(0.08, 0.12, 0.16))
            if spare >= 0:
                shape.commit()
                edits.append({'page': page_number, 'rect': list(rect), 'font_size': actual,
                              'replacement': text})
                return
            actual = round(actual - .25, 2)
        raise ValueError(f'PDF text does not fit on page {page_number}: {text[:60]}')

    replace_region(2, (55, 516, 545, 631),
        '1. Hypothesis 1 (H1 - Supervisory Cognitive-Deterministic Decoupling): '
        'A quantized board-level SLM (500M-1.5B parameters), separated from direct '
        '10-100 kHz actuation by a compiled guardrail, can synthesize controller '
        'parameters at 100 ms-1 s intervals. Test zero observed boundary violations '
        'and bounded fallback latency under declared faults and operating conditions. '
        'Bounds checks alone are not a stability proof; finite tests do not establish '
        'a universal safety guarantee (Sha, 2001; Wang et al., 2026).')
    replace_region(5, (173, 378, 354, 405),
        'Offline operation; measured local latency\nPrivacy and power depend on deployment',
        size=8.5, minimum=8)
    replace_region(8, (65, 53, 545, 117),
        'Local Objective Function L(x): The Tier 2 system prompt requests local '
        'stability, low wear and immediate operational goals. Prompt priority is '
        'a testable instruction, not a guarantee. Independent deterministic '
        'protections enforce the configured physical constraints.')
    replace_region(8, (65, 210, 545, 291),
        'Arbitration via Token Priorities: When local preservation conflicts with '
        'global demand, the intended SLM response to [CELL-TEMP-CRIT] is to '
        'curtail an [AGGRESSIVE-DISCHARGE] request. Measure whether the model '
        'actually proposes [SAFE-MODE-CURTAIL]. Log proposal errors separately '
        'from interlock overrides and committed actions; a protective interlock '
        'must not hide a failed arbitration decision.')
    replace_region(9, (54, 470, 545, 550),
        '3. Execution Guardrails (The Deterministic Step): A compiled C/C++ '
        'matrix or bounded verifier checks configured limits before actuation '
        '(Sha, 2001; Nesti et al., 2026). Valid numeric values outside bounds '
        'are clamped; malformed output is dropped. In-range parameter combinations '
        'can still destabilize a plant. State-dependent interlocks or validated '
        'gain regions, when used, are separately declared experimental mechanisms.')
    # The original example comment overstates what its single upper clamp proves.
    replace_region(16, (62, 521, 545, 546),
        '# Current upper bound only; not a stability or safety proof.',
        font='cour', size=9.5, minimum=9)
    replace_region(16, (50, 633, 545, 698),
        'The following five mechanisms are research proposals to evaluate '
        'separately. Their usefulness and observability are not assumed. '
        'Compare against suitable classical baselines, retain negative results, '
        'and measure net energy over the complete intervention window.')
    replace_region(17, (50, 537, 545, 667),
        '3. Predictive Thermal-Electrical Co-Optimization (Hypothesis): '
        'Correlate weather forecasts with measured thermal/electrical state '
        'to test an authored MPPT or switching intervention before a sunny interval. '
        'Reduced electrical extraction can leave more absorbed solar energy '
        'to dissipate as heat, so panel cooling is not assumed. Measure panel '
        'and converter temperatures separately, and compare against MPP '
        'over the complete intervention and recovery window. A warmer detuned '
        'panel contradicts the cooling prediction under those conditions. '
        'Retain cooler, warmer and inconclusive outcomes, with measurement '
        'uncertainty and net energy including intervention costs.')
    replace_region(18, (208, 159, 390, 180),
        'Test MPPT detuning before the forecast window\nMeasure temperature and net energy; cooling is unproven',
        size=6.6, minimum=6.1)
    replace_region(21, (50, 231, 545, 328),
        'Three design principles are proposed for resilient execution: '
        '(1) state isolation through deterministic interlocks; (2) shared '
        'global/local context with explicit local survival behavior during '
        'communication loss; and (3) graceful degradation under CPU or memory '
        'strain, with independently running classical controllers and protocol '
        'maps. These mechanisms require validation under the declared operating '
        'conditions. They do not guarantee defect-free execution or stable '
        'closed-loop behavior merely by being present.')
    replace_region(22, (65, 213, 545, 293),
        'Byte-to-Token Compilation & SunSpec Mapping: Parse the transport '
        'deterministically. The RTU request 01 03 00 20 00 02 C5 C1 has '
        'CRC-16/Modbus bytes low-first and maps to read/slave/register/count '
        'tokens after CRC validation. Modbus TCP uses MBAP framing, without '
        'the RTU CRC. Discover SunSpec model IDs and lengths before interpreting '
        'registers; raw byte values alone do not identify their device semantics.')
    replace_region(22, (65, 370, 545, 467),
        'Multi-Protocol Southbound Synthesis: An MQTT demand-response intent '
        '(farm/grid/demand-response, strategy curtail-battery, reduction 50 kW) '
        'can propose a Modbus control and CANopen SDO plan. Discover SunSpec '
        'Model 123 by walking model IDs and lengths from the declared base; '
        'resolve WMaxLimPct, its scale factor and WMaxLim_Ena from the model '
        'definition and device capabilities. Do not assume absolute register '
        '40021. Validate each action and retain application acknowledgements '
        'and partial-application outcomes for the inverter and BMS.')
    for page in doc:
        page.wrap_contents()
        page.insert_text((51, 31), 'CORRECTED EDITION | 12 NOVEMBER 2026 | Research proposals; not experimental results',
                         fontname='helv', fontsize=6.7, color=(0.22, .33, .43))
    doc.set_metadata({**doc.metadata, 'subject': 'Corrected edition, 12 November 2026: bounds/prompt limitations, SunSpec discovery and falsifiable thermal detuning.',
                      'keywords': 'supervisory SLM, corrected edition 2026-11-12, research hypotheses'})
    doc.save(target, garbage=4, deflate=True)
    doc.close()
    return edits


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, default=Path(__file__).parent)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    names = ['1-Supervisory-Small-Language-Models-for-Hardware.pdf',
             '2-Lab-Guide-Supervisory-SLM-Testbed.md', '3-Supervisory-SLM-Explorer.html']
    inputs = [args.source_dir / name for name in names]
    for source in inputs:
        if not source.is_file():
            raise FileNotFoundError(source)
    outputs = [args.output_dir / name for name in [
        'Supervisory-Small-Language-Models-for-Hardware-Corrected.pdf',
        'Lab-Guide-Supervisory-SLM-Testbed-Corrected.md',
        'Supervisory-SLM-Explorer-Corrected.html']]
    if any(source.resolve() == target.resolve() for source, target in zip(inputs, outputs)):
        raise ValueError('Output paths must differ from originals')
    outputs[1].write_text(correct_guide(inputs[1].read_text()), encoding='utf-8')
    outputs[2].write_text(correct_explorer(inputs[2].read_text()), encoding='utf-8')
    edits = edit_pdf(inputs[0], outputs[0])
    digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = {'edition': '2026-11-12', 'scope': 'Editorial corrections; no hardware validation or hypothesis results.',
                'sources': {path.name: digest(path) for path in inputs},
                'outputs': {path.name: digest(path) for path in outputs}, 'pdf_edits': edits}
    (args.output_dir / 'correction-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps({'outputs': [str(path) for path in outputs], 'pdf_regions_changed': len(edits)}))


if __name__ == '__main__':
    main()
