# Interpretability tab: plan (2026-10-22)

Purpose: let the owner test claims from Nick Cammarata's essay "Neural Networks Are Already Clean" (Goodfire, 2026-10-15, https://already-clean.goodfire.com/) on their own fine-tuned models (SmolLM2-135M/360M, Qwen2.5-0.5B; LoRA and full) on Apple Silicon, using the datasets and snapshots already in a tinystudio project.

Status (2026-10-22): implemented. `tinystudio/interp/neurons.py` (capture, neuron table and view, explain, blind-maximum and near-miss tests, before/after diff, checkpoint captures, drift), `tinystudio interp ...` commands, `/api/interp/...` endpoints, nine MCP tools (51 in the catalog), the Interpretability page in the web UI, `--checkpoint-every` / `--keep-checkpoints` on training with `job/n@step` model ids, and `tests/test_interp.py` (five offline tests on a tiny random Llama). Two deviations from section 2: the shared chat-template prefix is excluded from all top-k and per-datapoint statistics (causal attention makes those activations identical across datapoints, which filled every list with the same system-prompt token), and test examples are user/assistant exchanges rather than user sentences, because the corpus maxima of a chat fine-tune usually sit in the assistant turn. After three code reviews (correctness, UI, methodology) the following changed from section 2: density is a token density (share of content tokens above 10% of the neuron's maximum, measured in a second pass); label shares are per distinct datapoint over the full top-k; the first-token flag marks the first token after the prefix and a layer-level count of such peaks is reported; test sentences are scored against the gate-open corpus maximum with per-text prefix matching on token ids, and a verdict is "incomplete" when the helper returned unequal lists; correlation is undefined (not 1.0) when one side is constant and a combined change score (1 - r plus relative change, capped) ranks the comparison table; drift uses the full top-k datapoint sets, a cumulative label set, and reports new labels per step; explanation and test records store their prompts and raw replies; only one layer's activations are alive at a time and rows are batched to about 8k tokens per pass. The design below is kept as written; the README section "Interpretability: look inside your model" describes the shipped behaviour.

## 0. What the essay claims, what it shows, what it defers

### Claims as the author makes them
1. The canonical evidence for polysemanticity and superposition was misread. InceptionV1's messy last layer was an architectural learning failure (no residual stream, poor regularisation), not a second learning principle; a parameter-matched ConvNeXt on the same data has 99-100% interpretable last-layer neurons, and the family is legible across a 300x size range. Less room makes neurons coarser, not fractured.
2. The cat/car neuron 4e:55 is a V-shape neuron (negative pole: round). The 2019 "car into dog neurons" circuit was shared pose plus feature-visualization reward hacking; nothing reads the combination.
3. Modern language models are far cleaner than 2023 estimates: in preliminary audits of Qwen3-32B about 90% of neuron poles have one main job (often with small related offshoots), about 7% two related jobs, 2-4% not yet understood; similar in dense, MoE, and trillion-parameter models (Kimi K3). The number is threshold-dependent and "a broad pointer".
4. Most remaining apparent polysemanticity is "recursive generalization": a neuron sprouts related offshoots over training (an "iy" neuron grows to "diy" and then "do it yourself"). One learning principle, generalization, which is fractal.
5. The 2023 gap came from weak explainer models, weak tools and metrics (simulation scoring rewards reproducing activations, not being right), unknown motifs (summaries on punctuation, negative poles, "say or just think"), and small simplified models.
6. Understanding compounds: as the map fills in, every explanation is constrained by its neighbours, and the unexplained remainder exposes tool bugs, which is how the SwiGLU double-negative regime was found.
7. Neurons are better read as control flow (gates, verbs) than noun detectors; the negative pole matters as much as the positive.
8. Weak activations are generalization, not hidden features: removing the weakest 75% barely changes accuracy, removing the strongest 7% destroys it.
9. Rare facts and memorisation ride existing routes (umbrella taught to be "bee" switches routes in nine steps) rather than forming hidden features.
10. Attribution graphs built from raw neurons reproduce Anthropic's transcoder-based preeclampsia graph in Qwen3-32B and work in Kimi K3; negative poles carry Santa; hedge and "say not" neurons explain behaviours.
11. SwiGLU double negative: negative gate times negative value sends activations into the positive pole; for about 4% of neurons it is the whole top of the list; ablating the regime raises most benchmarks and lowers some reasoning ones.
12. Explanations can lose: blind-maximum sentences written from an explanation alone often beat the corpus maximum found in 10 billion tokens; near-miss tests on 183 neurons: new examples fire strongly 76%, near misses 11%, win/tie/loss 164/15/4; distant-concept texts fire about 1%.
13. Residual stream and attention are not bottlenecks in practice; attention heads are predicted (low confidence) to be broad and recursively generalized.
14. Zooming out: "clean" means parts built to be read by downstream readers; prefer exploratory over enumerative interpretability; motifs over monosemanticity; dictionaries and probes belong at the end; superposition should not appear at larger scale (circuit ecology, "semantic gravity"); the author is "maybe 85% sure".

### What is presented as evidence (measurements and experiments shown in the essay)
- Vision: ConvNeXt last-layer interpretability (99-100%), InceptionV1 revisited (about 50% with better tools, about 70% retrained), the 4e:55 input/reader circuit and pole images, path-guided feature visualization of the car circuit, pose neurons in DINOv2/ConvNeXt, layer 3b squeezed 480 to 10 neurons (58.35% top-1) with an SAE that does not recover the ten curve detectors (shape match 0.38-0.79, photo match at most 0.26, spliced-in top-1 58.8 to 43.0), the last layer cut to 13 neurons, SAE on the last layer (88% near-copies of neurons; removing non-neuron directions leaves accuracy unchanged), umbrella to bee in nine steps, weak-activation ablation tables per layer and model (p25-p99), residual-stream coordinate 112 carrying 94.5% of energy, crushed residual stream (72% to 60%/39%), one-photo generalization (20% vs 10% chance), circuit racing (48-50% alone vs about 2% beside a pretrained twin), edge motifs (median margin drop 0.86 vs 0.03), adversarial examples recruiting jaguar neurons.
- Language: ablation probabilities in Qwen3-32B graphs (P(Scar) 0.24 vs 0.91 random; P(Santa) 0.059 vs 0.997; hedge 0.23 vs 0.76; P(Anna) 0.92 vs 0.66) and Kimi K3 (return 0.83 to 0.98); the Russia neuron L1/N20736 whose positive top list is entirely gate-closed echo; the enemy neuron L36/E206/N103 (131/133 responses in p75-p99 use minus times minus, 93/93 at p99.99+ use plus times plus); 100M tokens through one neuron (about 160 reach the strong half); 1,000 random neuron papers on Qwen3-32B and Qwen3.6-35B-A3B; 500 GPT-2 XL neuron papers (about 70% roughly one thing, about 5% pristine); don't-say-dot ablations (20/20, 30/30); blind-maximum examples (15.0 to 18.4; 82.0 to 99.5); the 200-neuron near-miss study; Psalm 23 recitation breaking when extreme responses are zeroed; the Social Security direction (AUC 1.00 but -0.004 nats when removed); the 10,240-expert map; agents reviewing 100,000 attribution graphs (qualitative).

### What is the author's interpretation
- "One learning principle, not two"; superposition should be the last hypothesis; the 90/7/2-4 split and any single cleanliness score; recursive generalization as "most of what's left"; circuit ecology, racing, gradient starvation and "semantic gravity" as causes; MoE as an anti-superposition architecture; attention heads as broad; "clean" as readability; exploratory beats enumerative; features as a tainted word; the echo as an architectural flaw designers should fix; "85% sure"; interpretability possibly tractable soon; deception "in plain sight"; the neuroscience transfer. Several figures are explicitly "conceptual illustration" (the iy growth figure, the pr/Base64/Bengali figure), and the InceptionV1 revisits lack the original checkpoints.

### Deferred to the forthcoming companion paper
- The detailed language-neuron experiments: regimes of modern non-linearities, circuit motifs by depth, the Kimi K3 audits, "several ways" to test over-broad explanations, more prediction metrics and positive controls, the GPT-2 XL revisit details, expert specialisation measures, the echo ablation's effect on reasoning ("haven't looked into it deeply"), and the scale/ecology argument ("at least another paper"). As of 2026-10-22 Goodfire's research index lists no companion paper or code release.

### The two definitions of superposition
The essay defines superposition as a learning principle: "neurons learning unrelated things together because they rarely interfere" (arbitrary packing). It sets aside Toy Models of Superposition's definition, "more features than dimensions", as a count claim that composition also satisfies and that the essay calls good news; it says Olah's 2023 composition-versus-superposition note is closest to its own framing, and notes the word is "in superposition between several common uses". Consequence for this tab: the tests below can only bear on the essay's sense (do a neuron's strong activations group unrelated content, and did unrelated content arrive by packing or grow from a related core?). They say nothing about whether the residual stream holds more features than dimensions, and we should not read a clean neuron browser as evidence either way on that count claim.

## 1. Feasible on this laptop, and what is not

Measured on 2026-10-22 (M-series Mac, MPS, fp32, dataset home/commands = 241 datapoints, 16,100 chat-templated tokens):

| Operation | Measured | Note |
| --- | --- | --- |
| SmolLM2-135M, hooks on all 30 layers, one pass over the dataset | 3.2 s, 5,000 tok/s, peak 1.26 GB MPS | per-neuron max/min over tokens for 1,536 neurons x 30 layers |
| SmolLM2-360M, hooks on all 32 layers, running top-20 per neuron | 6.1 s, 2,650 tok/s, peak 1.9 GB | 2,560 neurons x 32 layers |
| Qwen2.5-0.5B | not cached locally (needs a 1 GB download); estimate 10-20 s per pass | 4,864 neurons x 24 layers |
| LoRA checkpoint with `save_only_model=True` | 23.1 MB (adapter 19.6 MB + tokenizer 3.5 MB) | r=16 on seven projections, 135M |
| LoRA checkpoint without `save_only_model` | 62.6 MB (adds optimizer.pt 39.4 MB) | avoid; we do not resume |
| Full fine-tune checkpoint, weights only | about 540 MB (135M), 1.45 GB (360M), 2 GB (Qwen 0.5B) | 4 bytes/parameter fp32; 3x with AdamW state |
| Helper LLM explanation call (Qwen3-4B 4-bit, mlx_lm.server) | 0.9-12 s per call | first call slow; budget 5 s |
| Base vs LoRA A/B through PEFT `disable_adapter()` | one extra forward pass | works in PEFT 0.21.2; no second model load |

Feasible now: all four requested features, plus two cheap extras the essay uses (steering a neuron up or down with a hook and reading the generation; zeroing activations below a percentile and re-running an evaluation).

Not feasible or out of scope: the essay's attribution graphs (per-token attribution across all neurons and heads with pruning and agent grouping; Anthropic's pipeline wants transcoders, and circuit-tracer ships pretrained transcoders for Gemma-2, Llama-3.2, Qwen-3, Gemma-3, GPT-OSS and Llama-3.1 8B, none for SmolLM2 or Qwen2.5), expert maps (mixture-of-experts only; none of our models), feature visualization (vision only), 10-billion-token corpus maxima, and agent audits of 100,000 graphs. A later "direct effect of each neuron on the answer logit for one prompt" view (activation x gradient) is tractable on these models but is not in this phase.

## 2. Design

### 2.1 Compute module `tinystudio/interp/neurons.py`
- `capture(project, model_id, rows, layers, topk=40, batch_size=16, device)`:
  - Load tokenizer and model in fp32 (`AutoModelForCausalLM.from_pretrained(src, dtype=torch.float32)`; transformers 5 defaults to the config dtype, bf16 for these models, so fp32 must be explicit). For LoRA experiments load the base and `PeftModel.from_pretrained(base, adapter_dir)` without merging; `disable_adapter()` gives the base in the same process. For full experiments load `experiments/<n>/model`.
  - Texts: `tokenizer.apply_chat_template(messages, tokenize=False)` for each datapoint (full conversation, assistant turn included), then batch-tokenize with right padding and an attention mask; run under `torch.inference_mode()`.
  - Hooks (PyTorch 2.14.1): `register_forward_hook` on `layers[i].mlp.gate_proj` (g) and `mlp.up_proj` (v), `register_forward_pre_hook` on `mlp.down_proj` (its input is `act_fn(g) * v`, the neuron activation; verified bit-exact). Reductions happen on device; only top-k entries and per-datapoint maxima move to CPU.
  - Per layer and neuron: top-k positive and top-k negative entries as (datapoint, token position, activation, g, v) with the sign regime derived from (sign g, sign v); separate top-k with the gate open (g > 0) so the double-negative echo can be hidden or shown; per-datapoint max and min per neuron (float16 matrix, datapoints x neurons); summary stats (max, min, p99, density = share of tokens above 10% of max). Padding is excluded by mask, not by position.
  - Store: `interp/<capture>/meta.json` (model, dataset or snapshot, layers, counts, timings), `tokens.json` (per datapoint: token strings), `layer-<L>.npz` (numpy 2.5.4 arrays). Size: about 1 MB per layer for 241 datapoints on 135M, 2.3 MB per layer on Qwen 0.5B.
- `neuron_view(capture, layer, neuron)`: top-k per pole and per regime with a +/-12-token context window around the token, per-label share of the top-k datapoints (the essay's "share of the neuron's strongest examples" band), stats, and any explanation or test records.
- `explain(capture, layer, neuron, client)`: helper LLM prompt with the gate-open positive top-k (token bracketed, context window), the negative top-k, and the instruction to describe a shared pattern as a verb-like rule rather than a list of nouns; output `{explanation, confidence}` appended to `explanations.jsonl`.
- `test(capture, layer, neuron, client, n_max=3, n_pos=5, n_near=5, n_far=5)`: blind-maximum (a second prompt that sees only the explanation writes n_max sentences), near-miss (n_pos should fire, n_near should not), and distant-concept controls; each sentence is wrapped as a user message in the same chat template, run through the model with the same hooks, and scored as max activation over its tokens divided by the corpus maximum of that neuron; "strong" = 30% of the corpus max (the essay's threshold); per-neuron outcome win/tie/loss by comparing positives against near misses; results in `tests.jsonl`.
- `diff(project, base_id, experiment_id, rows, layers)`: same inputs through base and experiment (adapter toggled for LoRA; two loads for full), per neuron Pearson r over tokens, relative L2 change, max shift; per neuron the datapoints and tokens with the largest absolute delta; per layer the distribution of r. Measured on smol-lora/1 at layer 12: median r 0.929, 364 of 1,536 neurons below 0.9.
- `drift(project, job, number, layer, neuron)`: reads captures made for each checkpoint of an experiment and returns per checkpoint the top-k lists, the label shares, Jaccard overlap of top-k datapoint sets between consecutive checkpoints and against the final one, and a flag for entries whose label was absent from earlier top-k ("offshoot candidate"); optional helper-LLM "related to the core / unrelated" judgement for new entries.

### 2.2 Checkpoints during training (`tinystudio/train.py`)
- `TrainConfig` gains `checkpoint_every_steps: int = 0` and `keep_checkpoints: int = 0` (0 = keep all).
- `_train` and `_train_dpo`: `save_strategy="steps" if cfg.checkpoint_every_steps else "no"`, `save_steps=cfg.checkpoint_every_steps`, `save_total_limit=cfg.keep_checkpoints or None`, `save_only_model=True`. Checkpoints land in `experiments/<n>/checkpoints/checkpoint-<step>/` (adapter-only for LoRA and DPO, full weights for full fine-tuning). A `TrainerCallback.on_save` records `{step, epoch, loss, path}` to `checkpoints.jsonl` (verified: fires at each save with state.global_step and state.epoch).
- UI shows the projected disk cost before starting (checkpoints x per-checkpoint size) and the measured size afterwards. `save_total_limit` deletes older checkpoints silently, so drift studies should keep all.
- `resolve_model` accepts `job/n@step` to load a checkpoint for capture, chat, or evaluation.

### 2.3 API (`tinystudio/studio_api.py`, ungated `/api/interp/...`)
- `POST /api/interp/captures` (task: `interp capture ...`), `GET /api/interp/captures`, `GET /api/interp/captures/{id}`, `GET .../layers/{L}/neurons?sort=max|density|echo_share|change`, `GET .../layers/{L}/neurons/{n}`, `POST .../layers/{L}/neurons/{n}/explain` (task), `POST .../test` (task), `POST /api/interp/diff` (task), `GET /api/interp/diff/{id}`, `GET /api/interp/drift?job=&number=&layer=&neuron=`.
- Tasks run as CLI subprocesses through the existing `TaskRunner` (they load their own weights; 2-3 s). Progress comes from the log tail as today.

### 2.4 CLI (`tinystudio interp ...`)
`capture MODEL --dataset D | --snapshot S [--layers 8,12,16|all] [--topk 40] [--name]`, `neurons CAPTURE --layer L [--sort ...]`, `show CAPTURE L N`, `explain CAPTURE L N [--all-top K]`, `test CAPTURE L N`, `diff BASE EXPERIMENT --dataset D --layers ...`, `drift JOB/N --layer L --neuron N`.

### 2.5 MCP tools (`tinystudio/tools.py`)
`capture_activations` (long-running), `list_captures`, `get_neuron`, `explain_neuron` (long-running), `test_neuron` (long-running), `compare_models` (long-running), `get_neuron_drift`. Catalog goes from 42 to 49 tools; `tests/test_integrations.py` counts change.

### 2.6 Web UI (`tinystudio/ui/studio.js`)
- Sidebar item "Interpretability" (route `#/interp`), captures list with a "New capture" dialog (model, dataset or snapshot, layers, top-k, estimated time).
- Capture page: layer selector; neuron table sortable by max, density, echo share, change score; neuron panel with the positive and negative poles (token highlighted inside its context), regime chips (+/+, +/-, -/+, -/-) with counts and a "hide gate-closed" toggle, label-share bars, explanation with an Explain button, test results (blind-max ratio, near-miss win/tie/loss) with a Test button, and a drift strip when the experiment has checkpoints.
- Diff page: base vs experiment, per-layer histogram of r, most-changed neurons, and the datapoints that drove each change.

## 3. Order of work
1. `interp.py` capture + storage + CLI `capture`/`neurons`/`show` + unit tests on a tiny random model (no download).
2. Checkpoints in `train.py` + `checkpoints.jsonl` + `@step` model ids + a smoke run.
3. Diff and drift.
4. Explain and test with the helper LLM.
5. API, tools, UI.

## 4. Assumptions to disprove (status after research and probes, 2026-10-22)
- A1 LlamaMLP/Qwen2MLP compute `down_proj(act_fn(gate_proj(x)) * up_proj(x))`, so the `down_proj` input is the neuron activation: Supported (modeling_llama.py:152-165, modeling_qwen2.py:34-47 in transformers 5.20.2; bit-exact in the probe).
- A2 Forward hooks capture inputs/outputs on MPS: Supported (PyTorch 2.14 Module docs; probe).
- A3 `from_pretrained` loads fp32 by default: Contradicted; v5 defaults to `auto` (config bf16). Pass `dtype=torch.float32` explicitly (already the codebase's habit).
- A4 BOS/first-token "massive activations" dominate per-neuron maxima: Contradicted on this model and dataset (0-0.3% of neurons have their max at position 0), but documented for LLaMA-2 7B/13B, so keep the exclusion flag and report it.
- A5 The double-negative echo is rare (about 4% of neurons in Qwen3-32B have the whole top of their positive list in it): Partly contradicted on SmolLM2-135M over the full home/commands dataset. Share of neurons with at least half of their top-50 positive activations in the minus-times-minus regime: layer 4 48%, layer 12 47%, layer 25 14%. Share whose gate-open maximum is below half of the overall maximum (the echo dominates the top): layer 4 14%, layer 12 19%, layer 25 3%. The regime split is therefore essential, not cosmetic, and the early and middle layers of a 135M model show far more echo than the essay reports for a 32B model.
- A6 TRL 1.16 SFTConfig/DPOConfig honour `save_strategy`, `save_steps`, `save_total_limit`, `save_only_model`, and PEFT checkpoints are adapter-only: Supported (docs and probe; 23.1 MB vs 62.6 MB).
- A7 `PeftModel.from_pretrained` plus `disable_adapter()` gives a base/adapter A/B in one process: Supported (probe, PEFT 0.21.2); note it may modify the passed base model in place, so never reuse that object as a standalone base.
- A8 The helper LLM (Qwen3-4B) produces usable one-line explanations and test sentences in JSON: Supported for one sample (0.9-12 s per call); reliability over hundreds of neurons unverified.
- A9 `output_hidden_states` gives per-layer residual states with the last entry post-norm: Supported (output_capturing.py:294-305); not used for neuron activations.
- A10 The neuron index is comparable across checkpoints and between base and fine-tuned models: Supported structurally (LoRA and full fine-tuning keep the MLP basis); meaning drift is what we measure.
- A11 Corpus maxima from a few hundred datapoints are representative: Contradicted in principle (the essay used 10 billion tokens); the UI must show the ratio and allow a larger reference dataset.

## 5. Evidence

### Codebase
- `tinystudio/train.py:L396` and `L307`: `save_strategy="no"` in SFT and DPO configs (the lines to change); `L378`/`L289`: `output_dir=exp_dir/"checkpoints"` already set.
- `tinystudio/runner.py:L87-L92`: fp32 load and `merge_and_unload()` for adapters (the tab needs an unmerged PEFT load instead).
- `tinystudio/studio_api.py:L179-L189`: router signature and `wrap()`; `L357`, `L417`: `start_task` examples.
- `tinystudio/ui.py:L33-L59`, `L149-L175`: Task and TaskRunner (no progress field).
- `tinystudio/ui/studio.js:L60-L75`: hash router; `L100-L122`: sidebar; `L326-L331`: `histogram`; `L537-L560`: loss chart; `L906-L920`: task drawer.
- `tinystudio/tools.py:L279-L285`: tool lists; `tests/test_integrations.py:L14-L21`: catalog asserts.
- `tinystudio/llm.py:L59-L82`: `chat` and `chat_json`.
- Model source: `.venv/.../transformers/models/llama/modeling_llama.py:L152-L165`, `models/qwen2/modeling_qwen2.py:L34-L47`, `utils/output_capturing.py:L294-L305`.
- Probes (scratchpad, 2026-10-22): `interp/probe.py`, `interp/probe2.py`, `interp/ckpt_probe.py` with the numbers in section 1.

### Official and peer-reviewed sources
- Cammarata, N., Neural Networks Are Already Clean, Goodfire, listed 2026-10-15, accessed 2026-10-22, https://already-clean.goodfire.com/
- PyTorch torch.nn.Module (hooks), v2.14, accessed 2026-10-21, https://docs.pytorch.org/docs/2.14/generated/torch.nn.Module.html
- PyTorch inference_mode, v2.14, accessed 2026-10-21, https://docs.pytorch.org/docs/2.14/generated/torch.autograd.grad_mode.inference_mode.html
- PyTorch torch.mps, v2.14, updated 2026-07-07, accessed 2026-10-21, https://docs.pytorch.org/docs/2.14/mps.html
- PyTorch MPS environment variables, created 2025-06-11, accessed 2026-10-21, https://docs.pytorch.org/docs/2.14/mps_environment_variables.html
- PyTorch 2.14.0 release notes, 2026-09-02, and 2.14.1, 2026-09-30, accessed 2026-10-21, https://github.com/pytorch/pytorch/releases
- transformers v5.0.0 release notes (default dtype auto), accessed 2026-10-21, https://github.com/huggingface/transformers/releases/tag/v5.0.0
- transformers migration guide v5 (apply_chat_template returns BatchEncoding), accessed 2026-10-21, https://github.com/huggingface/transformers/blob/main/MIGRATION_GUIDE_V5.md
- transformers model outputs (hidden_states semantics), main docs, accessed 2026-10-21, https://huggingface.co/docs/transformers/main/en/main_classes/output
- transformers TrainingArguments and Trainer (save_strategy, save_steps, save_total_limit, save_only_model), v5.20.2 docs, accessed 2026-10-21, https://huggingface.co/docs/transformers/main_classes/trainer
- transformers callbacks (on_save), v5.20.2, accessed 2026-10-21, https://huggingface.co/docs/transformers/main_classes/callback
- transformers trainer.py at v5.20.2 (checkpoint folder naming, save_only_model gating), accessed 2026-10-21, https://raw.githubusercontent.com/huggingface/transformers/refs/tags/v5.20.2/src/transformers/trainer.py
- PEFT PeftModel (from_pretrained, load_adapter, set_adapter, disable_adapter), v0.21.0 docs, accessed 2026-10-21, https://huggingface.co/docs/peft/v0.21.0/en/package_reference/peft_model
- PEFT LoRA guide (merge_and_unload), v0.21.0, accessed 2026-10-21, https://huggingface.co/docs/peft/v0.21.0/en/developer_guides/lora
- PEFT constants (adapter file names), v0.21.0, accessed 2026-10-21, https://raw.githubusercontent.com/huggingface/peft/refs/tags/v0.21.0/src/peft/utils/constants.py
- TRL v1.16.0 release notes, 2026-10-15, accessed 2026-10-21, https://github.com/huggingface/trl/releases/tag/v1.16.0
- TRL SFTTrainer and DPOTrainer docs (no save overrides), main, accessed 2026-10-21, https://huggingface.co/docs/trl/main/en/sft_trainer and https://huggingface.co/docs/trl/main/en/dpo_trainer
- Bills, S. et al., Language models can explain neurons in language models, OpenAI, 2023-05-09, accessed 2026-10-22, https://openaipublic.blob.core.windows.net/neuron-explainer/paper/index.html
- openai/automated-interpretability (archived 2026-05-29), accessed 2026-10-22, https://github.com/openai/automated-interpretability
- Paulo, G. et al., Automatically Interpreting Millions of Features in Large Language Models, arXiv:2410.13928 v3, 2025-08-06, accessed 2026-10-22
- Bolukbasi, T. et al., An Interpretability Illusion for BERT, arXiv:2104.07143, 2021-04-14, accessed 2026-10-22
- Xiao, G. et al., Efficient Streaming Language Models with Attention Sinks, arXiv:2309.17453, 2023, accessed 2026-10-22
- Sun, M. et al., Massive Activations in Large Language Models, arXiv:2402.17762, 2024, accessed 2026-10-22
- Ameisen, E. et al., Circuit Tracing: Revealing Computational Graphs in Language Models, Anthropic, 2025-03-27, accessed 2026-10-22, https://transformer-circuits.pub/2025/attribution-graphs/methods.html
- safety-research/circuit-tracer README and releases (v0.5.3), accessed 2026-10-22, https://github.com/safety-research/circuit-tracer
- Goodfire research index (no companion paper listed), accessed 2026-10-22, https://www.goodfire.com/research
- Prakash, N. et al., Fine-Tuning Enhances Existing Mechanisms, arXiv:2402.14811, ICLR 2024, accessed 2026-10-22

## 6. Gaps, risks, and counter-evidence
- Qwen2.5-0.5B is not in the local Hub cache; its numbers are estimates until downloaded.
- The essay's companion paper, thresholds beyond those quoted, and code are unpublished; the blind-maximum and near-miss procedures are reconstructed from the essay text.
- Our corpus maximum comes from hundreds of datapoints, so "beats the corpus maximum" is far weaker than in the essay; report ratios and allow a larger reference dataset.
- The echo share measured here (61% of neurons at layer 12, first batch) contradicts the essay's 4% for Qwen3-32B; different model, dataset and k, so treat as an observation to re-measure over the full dataset, not a refutation.
- A 4B helper model may invent examples or drift from JSON; every explanation and test sentence is stored with its prompt so failures are inspectable, and the tests are what catch bad explanations.
- `disable_adapter()` is an in-place toggle on a model that `PeftModel.from_pretrained` may have modified; never treat that object as a pristine base elsewhere.
- Full fine-tuning checkpoints cost 0.5-2 GB each; the UI must show the projected total and default to a small number.
- MPS vs CPU numerical equivalence of hooked activations is untested; add a one-batch equivalence test.
- The task runner has no progress field; capture progress is parsed from the log tail as elsewhere.
- Adding seven MCP tools changes the catalog asserts in tests.

## 7. Unverified
- Massive activations or sink tokens in SmolLM2/Qwen2.5 on other datasets (only home/commands was probed).
- Helper-LLM reliability across hundreds of neurons, and whether 5 positives and 5 near misses per neuron give a stable win/tie/loss (a sign test needs about 37 non-tied neurons to detect a 0.7 win rate).
- Whether DPO adapter checkpoints behave identically to SFT ones (probed SFT only).
- Timing and memory for Qwen2.5-0.5B and SmolLM2-1.7B.

Research report: reports/Neuron browser pitfalls for tiny LMs.md (REA checkout; written by the deep-research run of 2026-10-22).
