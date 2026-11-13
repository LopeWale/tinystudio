# Statistical planning

Statistical planning uses the Laboratory's existing definitions, revisions, preflight, scoped target execution and retained artifacts. It does not start a new study database or infer a result with a language model. Install the optional calculation dependency on the execution target with `pip install 'tinystudio[statistics]'`; a server can expose method descriptions without SciPy. Runner runtime receipts already record the installed SciPy version.

## Current methods

| Method | Goals | Required study assumptions |
| --- | --- | --- |
| `means.independent` | Sample size, fixed-sample power, detectable mean difference | Alpha, alternative, common SD and measurement unit; target power or group-1 analysis count; the chosen difference except when solving for effect; group-2/group-1 allocation ratio |
| `means.paired` | Sample size, fixed-sample power, detectable mean difference | Alpha, alternative, **SD of within-pair differences**, unit; target power or complete-pair count; chosen difference except when solving for effect |
| `proportions.independent` | Sample size, fixed-sample power | Alpha, alternative, anticipated proportions in both groups, allocation ratio; target power or group-1 analysis count |
| `proportion.precision` | Sample size, fixed-sample interval half-width | Alpha, anticipated proportion; target half-width or analysis observation count |

`solve_for` is `sample_size` by default; other implemented goals are `power`, `effect_size` (means) and `precision` (single proportion). `n` means analysis observations in group 1, complete pairs, or single-group observations according to the method. `delta` means group 1 minus group 2 (or the declared paired difference). A sample-size request with an effect pointing away from a one-sided alternative is rejected as unattainable. Fixed-sample power can intentionally describe a zero or opposite-direction effect. Alpha and target power are explicit researcher inputs; target power must exceed alpha when solving for a nonzero detectable effect or sample count.

The method registry exposes field applicability, assumptions, equations, references and implementation version at `GET /api/p/<project>/lab/statistics/methods`, and under `statistics_methods` in the existing lab catalog. This initial collection is not a platform-wide restriction on study designs. Adding numerical methods requires a declared method/selection contract and tested numerical kernel; arbitrary uploaded code is not implicitly executed by this calculator. Clustered, repeated-measures, sequential, finite-population and simulation-based designs need their own methods rather than being silently treated as independent observations.

## Numerical meaning and limits

Means use central-t rejection boundaries and noncentral-t power. Independent means assume independent normally distributed observations and common population variance; paired means assume independent, normally distributed differences. Proportions use a **normal approximation on Cohen's arcsine h scale**, not an exact binomial test. Precision uses a two-sided normal/Wald planning formula at the supplied anticipated proportion, not Wilson/exact coverage or a guarantee of realized interval width. The report warns about small anticipated success/failure counts. Anticipated proportions and interval error rate must lie strictly between zero and one for these methods.

Sample-size search finds the smallest integer group-1 count meeting target power with actual `ceil(n1 × ratio)` group-2 allocation. `attrition` defaults to an explicitly recorded zero and inflates recruitment separately as `ceil(analysis_n / (1-attrition))`. Allocation defaults to a recorded one if absent. Decimal allocation and attrition rounding use exact rational representations of their normalized decimal values to avoid an extra observation caused by floating-point rounding. Pair counts mean complete independent pairs, not individual participants; how pairs map to people or devices belongs to the study.

Counts must fit the exact JSON/IEEE-754 integer range. Invalid/nonfinite inputs, unresolvable numerical requests, missing dependencies and unsupported settings produce actionable errors, not fabricated results. Existing definition-size and runner time/output budgets are operational constraints. The calculator adds no paper-derived sample-count, voltage, model-size or experimental pass rule.

## Configure, run and reopen

In Laboratory, choose **Statistical planning** in the experiment dialog. Select a method and calculation goal, supply the displayed inputs, and record source notes. Newly required inputs remain empty when changing design; they are not inferred. The initial template contains clearly labeled illustrative assumptions. Preflight remains authoritative for method compatibility and target dependencies. Save and submit through the ordinary lab workflow, select the resulting run and open **Planning**. The view checks the retained artifact's SHA-256 before displaying scenarios, group/pair counts, power/effect/precision, warnings, assumptions and the original calculation recipe.

`input_sources` maps field names to researcher-authored source notes. It is not independent verification of a pilot estimate. `sensitivity` is an ordered array of input-override objects for the same method and goal. Each scenario is validated, frozen and calculated, and retains its effective inputs. Changing a saved definition does not change an already submitted run. A cancelled or failed run cannot publish a successful calculation result.

The [example definition](../examples/statistical-planning.json) uses illustrative paired differences of 0.5 with SD 1, two-sided alpha 0.05 and target power 0.8. It requires **34 complete analysis pairs**, or **38 recruited pairs** with 10% attrition under this formula. These are demonstration assumptions, not a sample-size recommendation for the supervisory paper.

```bash
tinystudio lab statistics-methods --project research
tinystudio lab define examples/statistical-planning.json --project research
tinystudio lab check examples/statistical-planning.json --project research --target TARGET_ID
tinystudio lab submit DEFINITION_ID 1 --project research --target TARGET_ID
tinystudio lab inspect RUN_ID --project research
tinystudio lab export RUN_ID planning.zip --project research
```

The existing runner must be enrolled and running on the selected calculation target. A queued run is not a completed calculation. The retained `statistics-plan.json` artifact (`statistical_plan` role, `tinystudio.statistics-plan.v1` format) includes the original request/hash, normalized scenario inputs, method version/equations/assumptions, engine source hash, Python/SciPy versions, rounding and result counts. The regular run receipt additionally binds the frozen implementation and runtime. Exports use the existing checksum-verified portable evidence bundle.

## Assistant and MCP

Both interfaces use the same tools: `lab_statistics_methods` discovers methods, while `lab_statistics_define(name, parameters, execution)` saves a definition without executing. Then `lab_preflight`, `lab_submit`, `lab_run`, `lab_events` and `lab_export` use existing services. The assistant should ask for missing effects, variability or design choices, or present explicitly sourced sensitivity scenarios. It must not invent a variance, claim a planning estimate as an observed outcome, or imply that a sample-size calculation guarantees success.

Planning receipts can be linked to [versioned studies and collection records](study-tracking.md). Pilot estimation, broader post-collection statistical analysis, campaign automation and additional design methods remain subsequent work. This increment supplies the statistical-planning workflow and its extension boundary.

## Verification

Install calculation and test dependencies with `pip install -e '.[dev,statistics]'`. Run `python -m pytest tests/test_lab_statistics.py tests/test_lab_statistics_integration.py` for numerical references, invalid and extreme inputs, cancellation, real registered HTTP/native-child execution, immutable submitted assumptions and checksum-verified export. Installing `statsmodels` additionally exercises independent power comparisons. These HTTP tests own disposable SQLite metadata and local artifacts; they do not qualify PostgreSQL locking, Redis/RQ delivery or a physical target.

The frontend tests in `statistics-definition.test.ts` and `statistics-planning.test.tsx` cover catalog-driven editing, method/goal changes, source notes and sensitivity, draft validity, ordinary Lab admission, retained checksums and stale-response handling. The built application was also exercised in Chromium with real sign-in, save/submit/native-child execution, two sensitivity records and reopening persisted evidence after reload. The illustrative independent-means result was 64 observations per group at achieved power approximately 0.80146.
