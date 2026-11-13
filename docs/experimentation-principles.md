# Experimentation principles

User direction, 12 November 2026.

Tiny Studio is a general experimentation workbench. The supervisory SLM paper is one use case, not a specification of what the platform permits researchers to study. Supply mechanisms for the researcher to define, connect, observe, calculate, compare and reproduce experiments across domains.

## Researcher-owned choices

Hypotheses, mechanisms, models, plant dynamics, representations, clocks, bounds, controller policies, baselines, endpoints, sample counts, stopping rules and statistical methods belong to versioned user-authored experiment configuration. Paper values and lab-guide choices may be editable templates; they must not become universal platform limits or mandatory pass criteria. A mechanism can be tested even if the paper or a default physical model predicts that it will fail.

Make capability and authority requirements explicit: distinguish an unsupported adapter, an unavailable dependency, an operator-provisioned device limit, and a user-selected experimental constraint. Do not silently convert any of these into a restriction inferred from a paper. Existing runtime limitations are implementation gaps to report and address, not scientific rules to endorse. Resource budgets should describe their operational purpose and be configurable within the execution target's provisioned capacity. This direction does not authorize removing authentication, provenance checks, or the control boundaries required by an actually connected device.

## Observation and data custody

Tracking and data collection are first-class capabilities: preserve raw inputs, transformations, mechanism/model identities, proposed actions, applied actions, outcomes, timestamps, units, uncertainty, errors, missingness and source lineage. Keep authored assumptions, measured results and derived statistics distinguishable. Support repeatable runs and independent recalculation. Record negative and inconclusive findings as fully as positive findings.

## Statistical tooling and assistant support

Statistical computation is a high-priority platform requirement, including before data collection. Provide extensible methods for sample-size and power calculations, minimum detectable effect, precision/confidence-interval planning, allocation, paired/repeated designs, clustering, attrition and simulation-based planning. No single test, sample count, significance level or power target should be compulsory across experiments.

The assistant should help select a design, identify the unit of independence, elicit or find inputs, explain assumptions and run the chosen calculation through an auditable computation tool. Retain the method/version, equations or executable calculation, input values and sources, effect-size definition, sidedness, alpha/power or precision target, result/rounding and sensitivity analysis. If a variance, rate or effect is unknown, offer a pilot or a range of explicit scenarios rather than inventing an input. A power calculation is a planning result, not a guarantee of experimental success.

Subsequent analysis should support researcher-selected estimates, intervals, diagnostics, tests and multiplicity/stopping procedures, with immutable links to the data and analysis specification. The assistant's prose should link to the actual calculation and data, not stand in for either.

## Scope of this change

These principles guide the product beyond the [document corrections](research/supervisory-slm/README.md). [Statistical planning](statistical-planning.md) implements sample-size, power, detectable-effect and proportion-precision calculations through the existing Laboratory workflow. [Study tracking](study-tracking.md) adds versioned protocols, append-only collection records and frozen evidence associations. Broader post-collection analysis, new adapters and unrestricted cross-engine composition remain development work.
