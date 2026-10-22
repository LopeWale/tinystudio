# tinystudio

A local, account-free pipeline for training tiny and domain-specific language
models on a Mac: **Datasets → Fine-tuning → Evaluations → Deployment**, the
same four stages as Prem Studio, but every stage is a command over files in a
directory you own. No sign-up, no billing, no network except the model weights
you download once from Hugging Face and any helper model you run yourself.

It targets Apple Silicon (PyTorch MPS) and models from 135M to about 1.7B
parameters. On a 24 GB M4 Pro, a LoRA run over 200 examples takes about a
minute.

## Install

```bash
cd ~/Developer/tinystudio
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[mlx,dev]"
export PATH="$PWD/.venv/bin:$PATH"       # or call .venv/bin/tinystudio
```

Built and verified against: torch 2.14.1, transformers 5.20.2, trl 1.16.0,
peft 0.21.2, datasets 5.1.0, fastapi 0.156.4, mlx-lm 0.32.2, Python 3.12.13.

## Ten-minute tour

```bash
tinystudio init home projects/home --goal "Convert smart-home requests into one strict JSON command"
cd projects/home

# Datasets: import, split into training/validation, freeze a snapshot
tinystudio dataset create commands --from-jsonl ../../examples/home_commands.jsonl
tinystudio dataset split commands --train 85
tinystudio snapshot create commands v1

# Fine-tuning: see what fits this machine, then train
tinystudio recommend v1
tinystudio finetune v1 --job smol-lora --model HuggingFaceTB/SmolLM2-135M-Instruct --type lora --epochs 3
tinystudio jobs
tinystudio losses smol-lora/1

# Evaluations: deterministic metrics, base model vs. your experiment
tinystudio metric create json --kind code --check json_valid
tinystudio metric create exact --kind code --check exact_match
tinystudio eval run v1-smol --snapshot v1 --model smol-lora/1 --model HuggingFaceTB/SmolLM2-135M-Instruct --metric json --metric exact
tinystudio eval show v1-smol --failures

# Deployment: chat directly, or serve an OpenAI-compatible API
tinystudio chat smol-lora/1 --system "You convert smart-home requests into one JSON command with keys action, device, room and optional value. Reply with JSON only."
tinystudio serve --port 8000 --up smol-lora/1
curl -s localhost:8000/v1/chat/completions -H 'content-type: application/json' \
  -d '{"model":"smol-lora/1","messages":[{"role":"user","content":"kitchen lights off"}]}'
```

## The web interface (Studio replica)

`tinystudio serve` serves a local replica of Prem Studio's interface at
http://127.0.0.1:8000/ with the same navigation: Datasets, Finetuning,
Evaluations (Eval, Metrics), Playground, Models, Traces, API Keys, Stats.

```bash
./dev-up.sh projects/home smol-full/1     # starts the helper LLM (mlx_lm.server) and tinystudio serve, detached
./dev-down.sh                              # stops both
```

- **Datasets**: cards with counts and creation date; the three-step *Add dataset*
  wizard (upload JSONL, or synthetic generation from files and web pages with
  pairs per source, rules & constraints, question/answer guidance, creativity
  level, and an optional curation threshold); dataset page with bucket
  counters, the datapoint table, Enrich, Snapshots (create, download, delete),
  Autosplit with the "allow synthetic data in validation" toggle, Labels with
  auto-labeling, add datapoint, download, delete.
- **Finetuning**: *New finetuning job* dialog (name, snapshot, reasoning / non
  reasoning); experiment configuration cards (base model, batch size, learning
  rate, epochs, LoRA or full) next to the dataset-analysis accordion (task
  summary, characterization, knowledge requirements, success criteria, edge
  cases, input/output analysis, recommended experiment); *Start experiments*
  runs them in order; completed jobs show status, the `ft-<job>-<n>` alias,
  Test model, download, delete, and the training-loss comparison chart with
  per-experiment toggles and eval loss.
- **Evaluations**: *New evaluation* with Agentic and Bring-your-own-evals tabs;
  results page with one card per model, metric tabs, the datapoint × model grid
  with an Average row and pagination, and the result-details modal (Datapoint
  tab; Model results tab with per-rule pass/fail verdicts and the response).
- **Metrics**: cards with should / should-not counters; create a metric by hand
  or let the helper LLM generate rules from a description and a snapshot; edit
  rules inline. Conciseness, Correctness, and Hallucination are created by
  default.
- **Playground**: model dropdown, system prompt, creativity slider, streaming
  chat; every exchange becomes a trace.
- **Models**: fine-tuned and base models with parameter counts, load/unload,
  test, download. **Traces**: score, correct, delete, promote into a dataset.
  **API Keys**: bearer keys that gate `/v1` once one exists. **Stats**: counts,
  training time, requests and tokens served.

Long actions run as CLI subprocesses; the task panel shows the exact command
and its log so every run is reproducible from the terminal. The page binds to
127.0.0.1 only.

## Data quality: before, during, after

- **Before training** — *Quality* on a dataset page (or `tinystudio dataset quality NAME`):
  length histograms for user and assistant turns, approximate token count,
  bucket / label / source distributions, exact and near-duplicate detection
  (word n-gram Jaccard), length outliers (IQR fences), empty-like rows,
  JSON-answer share, curation ratings, PII-like patterns (emails, phones,
  card numbers, national ids). *Clean* applies dedupe, outlier, rating, label,
  JSON, and whitespace rules in place, and *Redact PII* replaces those spans
  with `[EMAIL]`, `[PHONE]`, `[CARD]`, `[ID]` while keeping the rows; every
  run is logged to `datasets/<name>/cleaning.jsonl` (`tinystudio dataset
  clean --redact-pii ...`). *Enrich* paraphrases user turns with a creativity
  level plus positive ("generate more of") and negative ("avoid")
  instructions, the same three controls as Studio's augmentation form.
- **During training** — the job page polls every few seconds and draws the
  training-loss comparison with eval loss per experiment.
- **After training** — evaluation pages show per-model score cards, the score
  distribution per metric, the datapoint × model grid, and per-rule verdicts.

## Hugging Face Hub

The Models page (and `tinystudio hub ...`) searches text-generation models on
the Hub with parameter counts, license, and gated flags; downloads them into
the local Hub cache as a background task; lists what is cached and whether
transformers can load it; saves a token for gated models; and uploads
experiments (adapter or merged), datasets, and snapshots to your account.
Any downloaded transformers model can be chosen as a base model when you
configure an experiment.

```bash
tinystudio hub search "qwen2.5" --max-params-b 1
tinystudio hub download Qwen/Qwen2.5-0.5B-Instruct
tinystudio hub login hf_...                 # gated models and uploads
tinystudio hub upload experiment smol-full/1 you/smol-home-commands --merge
```

## Serving: JSON mode, tool calls, streaming usage

`/v1/chat/completions` accepts the OpenAI fields that matter for small models:

- `response_format: {"type": "json_object"}` or `{"type": "json_schema", "json_schema": {...}}`
  — the model is instructed to answer with JSON, the reply is parsed and
  checked against the schema (type, enum, required, items,
  additionalProperties), and it retries up to three times. The response
  carries `tinystudio: {"json_valid": true|false, "attempts": n}` so you see
  when a 135M model could not satisfy a schema instead of getting a silently
  broken object.
- `tools: [...]` — tool definitions are passed to the base model's chat
  template; Hermes/Qwen `<tool_call>` blocks and Llama-style bare JSON calls are
  parsed into `message.tool_calls` with `finish_reason: "tool_calls"`. Whether a
  model emits a call depends on its template and training; SmolLM2 base models
  mostly do not, Qwen2.5 does.
- `stream: true` — SSE chunks, and the last chunk carries `usage` with real
  prompt and completion token counts.

## Refine, export, run elsewhere

- **Refine an experiment**: `--refinetune-from smol-lora/1` (CLI `finetune` and
  `job add`, or the "Refine from" select in the web experiment form) starts a
  new experiment from a finished one's weights: a merged model directory, or an
  adapter merged on the fly. Studio calls this continuous fine-tuning; here the
  parent is recorded as `weights_source` in the experiment's `status.json`.
- **Export**: `tinystudio export smol-lora/1 ./out` writes a standalone
  Hugging Face model directory; add `--ollama --system "..."` to also write an
  Ollama `Modelfile` next to it (`ollama create NAME -f out/Modelfile`).
- **Documents for synthetic data**: `.txt .md .html .pdf .docx .pptx` files,
  web pages (`--url`), and YouTube videos with captions (`--url https://youtu.be/...`,
  needs the `youtube` extra). DOCX and PPTX are read with the standard library.

## Alignment with DPO, and the autopilot loop

- **Preference pairs**: a datapoint may carry a `rejected` answer next to the
  assistant's (chosen) answer. The quickest source is your own traffic:
  correct a bad response on the Traces page, then
  `tinystudio trace promote DATASET --preference` stores each corrected trace
  as a pair (correction = chosen, original response = rejected).
- **DPO**: `--type dpo` (CLI, API, or the experiment form) trains LoRA
  adapters with TRL's `DPOTrainer`; the adapter-free base is the reference
  model, so no second model is loaded. Default learning rate 5e-6, `beta`
  0.1. Rows without `rejected` are ignored, and the experiment fails with a
  clear message when fewer than two pairs exist. Prem lists GRPO and DPO as
  optional alignment; GRPO (reward-model RL) is not implemented here.
- **Autopilot**: `tinystudio autopilot run NAME --dataset D [--model M]
  [--min-trace-score 0.9] [--preference] [--type lora|full|dpo] [--metric json ...]`
  runs one continuous fine-tuning cycle: promote scored traces → clean → split →
  snapshot `NAME-cN` → job `NAME-cN` with one experiment refined from the
  previous cycle's winner (the base model on the first cycle) → evaluate
  candidate vs. previous winner on the new snapshot (the first metric decides)
  → record the winner in `autopilot/NAME.jsonl`. The Finetuning page has an
  *Autopilot cycle* button, a *Next cycle* button per autopilot, and a history
  dialog; the MCP tool is `run_autopilot_cycle`. Nothing runs on a schedule:
  each cycle is one explicit call, so an assistant or a cron job can drive it.

## Interpretability: look inside your model

The *Interpretability* page (and `tinystudio interp ...`) lets you test, on your
own fine-tunes, the claims in Nick Cammarata's essay *Neural Networks Are
Already Clean* (Goodfire, 2026): that MLP neurons are mostly legible, that
their negative pole matters, that SwiGLU's double-negative "echo" pollutes top
lists, that explanations can be tested, and that neurons grow from a related
core. The checks here are label-based and corpus-bound; they can support or
weaken those claims on your model, not settle them, and they say nothing about
the "more features than dimensions" sense of superposition.

- **Capture** (`interp capture MODEL --dataset D --layers 8,12`): one pass of the
  dataset through the model records, per MLP neuron, its top-k positive and
  negative activations (the `down_proj` input, `silu(gate) * value`) with the
  gate and value signs, a gate-open top list, and per-datapoint maxima. The
  chat template's shared prefix (system prompt and header) is excluded because
  causal attention makes those activations identical across datapoints. Two
  passes (statistics, then token density) over 240 datapoints and all 30
  layers of SmolLM2-135M take about 11 s on an M-series Mac; rows are
  batched to fit about 8k tokens per forward pass and only one layer's
  activations are alive at a time.
- **Neuron browser**: per layer, a table sortable by max, gate-open max, min,
  echo share, and density; per neuron, the positive and negative poles with the
  token in context, the four sign regimes (+/+, +/−, −/+, −/−; the last is the
  echo: a closed gate times a negative value gives a positive activation that
  belongs to the negative pole, and on SmolLM2-135M it tops the positive list
  of 3 to 19 percent of neurons per layer), a toggle to hide gate-closed
  activations, a flag for neurons that peak on the first token after the
  prefix, and the share of the top-k datapoints that carry each label.
- **Explain and test** (`interp explain CAPTURE L --neuron N` / `--top 20`,
  `interp test`): the helper LLM writes a one-line, verb-like explanation from
  the strongest contexts; the blind-maximum test has a second prompt that sees
  only the explanation (plus the model's system prompt, so it can write
  plausible exchanges) write user/assistant exchanges meant to fire the neuron,
  scored as a multiple of the neuron's gate-open corpus maximum; the near-miss
  test writes 5 exchanges that should fire, 5 near misses, and 5 distant ones
  (strong = 30% of that maximum) and records win/tie/loss, or "incomplete" when
  the helper returned unequal lists. Prompts and raw replies are stored with
  every record. Your corpus maximum comes from a few hundred datapoints, far
  fewer than the essay's 10 billion tokens, so read the ratios, not the
  pass/fail alone.
- **Before/after** (`interp diff JOB/N --dataset D`): the same tokens through the
  base weights and the experiment (LoRA adapters are toggled in place; full
  fine-tunes load both models); per neuron the activation correlation r
  (undefined when one side is constant), the relative change ‖after − before‖ /
  ‖before‖, a combined change score, and the datapoints and tokens that drove
  it; per layer the distribution of r.
- **Drift across checkpoints**: train with `--checkpoint-every N` (weights-only
  checkpoints under `experiments/<n>/checkpoints/`, about 23 MB each for a LoRA
  adapter, 0.5 GB for a full 135M fine-tune), then `interp capture-checkpoints
  JOB/N --dataset D --layers L` and `interp drift JOB/N --dataset D --layer L
  --neuron N`: label shares per checkpoint, overlap of the top-k datapoint sets
  between steps, and label-based offshoot candidates (datapoints carrying a
  label not seen in any earlier checkpoint's top-k). The baseline is the first
  saved checkpoint, not the base model. Checkpoints load anywhere a model id is
  accepted as `job/n@step`.

Not reproduced from the essay: attribution graphs, expert maps (mixture of
experts only), and feature visualization. See `docs/interpretability-plan.md`
for the design and the measured numbers behind it.

## Repositories: chat with your documents (RAG)

`tinystudio repo create manuals`, then `tinystudio repo add manuals --from
handbook.pdf --url https://…` extracts the text (txt, md, html, pdf, docx,
pptx, web pages, YouTube captions), splits it into ~1,200-character chunks,
embeds them with any Hugging Face feature-extraction model (default
`sentence-transformers/all-MiniLM-L6-v2`; search the Models page with task
"embeddings" to pick another), and stores the vectors next to the chunks under
`repositories/<name>/`. No external vector database: retrieval is a cosine
similarity over the stored matrix, which is instant for a personal corpus.
`tinystudio repo query manuals "reset the thermostat"` shows what retrieval
returns. In the Playground, the API, and the `chat` tool, pass
`repositories: {"ids": ["manuals"], "limit": 3, "similarity_threshold": 0.3}`
(Prem's shape) and the retrieved passages are prepended to the system prompt
and returned as `tinystudio.document_chunks`.

## The assistant on every page

The ✦ button (or ⌘K) opens a chat drawer on every page. Pick Claude or OpenAI
and any model your account lists (the pickers read each provider's live model
list). With *page* ticked, every message carries what you are looking at: the
route, the visible text, the data the page fetched, and your text selection.
The assistant can also read the live page as an element tree with ref ids
(`ui_read_page`), open pages (`ui_navigate`), click, fill, and select
(`ui_click`, `ui_fill`, `ui_select`), and run every tinystudio tool, so "explain
the three most changed neurons on this page" or "start a LoRA job on the latest
snapshot and open it" both work. This is the same shape as Claude in Chrome's
page tools (an accessibility tree with ref ids plus actions), scoped to the
tinystudio interface. Credentials: `ANTHROPIC_API_KEY` (or `ant auth login`)
and `OPENAI_API_KEY` in the environment or `.env`.

## Running on bigger machines and other models

The catalog (SmolLM2, Qwen2.5) only drives the recommendation; any causal LM on
the Hub works as a base model, and the neuron browser handles the gated
(Llama, Mistral, Qwen, Gemma, SmolLM), fused (Phi-3), and plain (GPT-2, Phi-2,
OPT, Bloom) MLP layouts. Compute follows the machine: CUDA first, then Apple
MPS, then CPU (`TINYSTUDIO_DEVICE`), bf16 on CUDA GPUs and fp32 elsewhere
(`TINYSTUDIO_DTYPE`), the fused AdamW on CUDA, and 4-bit QLoRA (`--type qlora`)
when a CUDA GPU and `bitsandbytes` are present. The memory budget for
recommendations reads total GPU memory on CUDA and host RAM on Linux and
macOS. To use a bigger box, run `tinystudio serve --host 0.0.0.0` there and
open its address; the CLI, the MCP server, and the assistant all talk to the
same HTTP API. The server is single-process; multi-GPU data parallelism is not
implemented.

## Coding assistants and the in-app assistant

tinystudio is an MCP server: every capability (55 tools) is available to
Claude Code, Codex, Claude Desktop, or any MCP client. Long actions return a
task id the assistant polls.

```bash
claude mcp add --scope user tinystudio -- $PWD/.venv/bin/tinystudio mcp --project $PWD/projects/home
# Codex: ~/.codex/config.toml  ->  [mcp_servers.tinystudio] command = ".../tinystudio" args = ["mcp", "--project", "..."]
# Claude Desktop: claude_desktop_config.json -> "mcpServers": {"tinystudio": {"command": ".../tinystudio", "args": ["mcp", "--project", "..."]}}
```

The *Assistant* page runs the same tools from inside the app with a frontier
model: Claude (Anthropic SDK tool runner, `claude-opus-5-5`, adaptive
thinking, refusal fallbacks) when `ANTHROPIC_API_KEY` or an `ant auth login`
profile is present, or OpenAI (Responses API function calling) when
`OPENAI_API_KEY` and `TINYSTUDIO_OPENAI_MODEL` are set. Tool calls and
results stream into the chat; the delete tool is offered only when you tick
"Allow deletions".

## How Studio concepts map

| Studio | tinystudio | Where it lives |
| --- | --- | --- |
| Project | `tinystudio init` | `tinystudio.json` (one project per server; run `serve` in another directory for another project) |
| Dataset, datapoints with `messages` | `dataset create / add / show` | `datasets/<name>/datapoints.jsonl` |
| Synthetic data from documents and URLs | `dataset synth --from doc.pdf --url https://... --spec spec.json` | needs a helper LLM (below); txt/md/html/pdf/docx/pptx and YouTube captions |
| Augmentation | `dataset augment` | needs a helper LLM |
| Autosplit into training / validation buckets | `dataset split` | `bucket` field on each datapoint |
| Snapshot (immutable split) | `snapshot create` | `snapshots/<name>/{train,val}.jsonl` |
| Recommended models and hyperparameters | `recommend` | heuristic over dataset size, sequence length, and unified memory |
| Fine-tuning job → experiments, model aliases `ft-…` | `job create / add / run / analyze`, `finetune --job NAME` | `jobs/<job>/experiments/<n>/{config,status,loss}`, `analysis.json` |
| Labels and auto-labeling | `dataset labels`, `dataset auto-label` | `labels` in `meta.json`, one label per datapoint |
| Model up / down, API keys, Playground, Stats | web interface, `/v1/models/{id}/up`, API Keys page | `.api-keys.json` (hashed) |
| `training_type` full / lora / qlora | `--type full` or `--type lora` | QLoRA is unavailable on Apple GPUs (no bitsandbytes Metal backend) |
| Experiment states pending → running → succeeded / failed | `status.json` | poll with `tinystudio jobs` |
| Rule-based metrics (should / should-not), built-ins Conciseness / Correctness / Hallucination | `metric create --kind rubric`, `metric generate --description`, web editor | `metrics/<name>.json`, judged by a helper LLM |
| Bring-your-own evaluator webhook | `metric create --kind webhook --url ...` | POSTs `{datapoint, prediction, model_name}`, expects `{score, reason}` |
| Code-based checks | `metric create --kind code --check ...` | exact_match, token_f1, similarity, json_valid, contains_reference |
| Evaluation runs and leaderboard | `eval run`, `eval show` | `evals/<name>/{eval.json,results.jsonl}` |
| Model up / down and chat completions | `serve`, `POST /v1/models/{id}/up` | OpenAI-compatible, localhost only |
| Traces → add to dataset | `trace list / score / promote` | `traces.jsonl` |
| Download weights | `export JOB/N DIR [--ollama]`, `hub upload experiment` | merged Hugging Face model directory, Ollama Modelfile, or a Hub repo |
| Continuous fine-tuning from an earlier experiment | `--refinetune-from JOB/N` | `weights_source` in the experiment's `status.json` |
| Structured output and tool calling in the API | `response_format`, `tools` on `/v1/chat/completions` | validated with retries; `tinystudio.json_valid` reports the outcome |
| Alignment (Prem: GRPO, DPO) | `--type dpo` on datapoints with `rejected`; `trace promote --preference` | TRL `DPOTrainer` with LoRA; GRPO not implemented |
| PII redaction, augmentation creativity + positive/negative instructions | `dataset clean --redact-pii`, `dataset augment --more/--avoid/--temperature` | Quality dialog, Enrich dialog |
| Autonomous / continuous fine-tuning loop | `autopilot run NAME`, Finetuning page *Autopilot cycle* | `autopilot/<name>.jsonl`, jobs and snapshots `<name>-cN` |
| (not in Studio) Neuron browser, explanations and tests, before/after, drift | `interp capture / explain / test / diff / drift`, Interpretability page | `interp/captures/<name>/`, `interp/diffs/<name>/`, `experiments/<n>/checkpoints/` |
| Repositories (Prem platform 2024: documents, chunks, Limit, Similarity) | `repo create / add / query`, Repositories page, `repositories` on chat | `repositories/<name>/{meta.json,chunks.jsonl,embeddings.npy}` |
| Data cleaning and statistics | Quality dialog, `dataset quality`, `dataset clean` | `cleaning.jsonl` per dataset |
| Assistant / SDK automation | MCP server (`tinystudio mcp`), Assistant page | 40 tools over the HTTP API |

## The helper LLM

Synthetic generation, augmentation, rule generation, and rubric judging need a
model that is better than the one you are training. Run one locally and point
tinystudio at it with an OpenAI-compatible URL:

```bash
# Apple Silicon, using MLX weights (this one is already in your Hugging Face cache)
.venv/bin/mlx_lm.server --model mlx-community/Qwen3-4B-Instruct-2507-4bit --port 8080
export TINYSTUDIO_LLM_BASE_URL=http://127.0.0.1:8080/v1
export TINYSTUDIO_LLM_MODEL=mlx-community/Qwen3-4B-Instruct-2507-4bit
```

Anything that speaks `/v1/chat/completions` works: llama-server, Ollama,
LM Studio, vLLM, or a second `tinystudio serve`. Set `TINYSTUDIO_LLM_API_KEY`
if your server wants a bearer token. Nothing talks to a hosted API unless you
point it there.

## Training details

- **Format**: JSONL, one `{"messages": [...]}` per line with `system`
  (optional, first), `user`, and `assistant` turns ending in the assistant's
  answer. The base model's chat template is applied by TRL's `SFTTrainer`.
- **LoRA**: PEFT `LoraConfig(r=16, lora_alpha=32, dropout=0.05)` over the
  seven linear projections of Llama/Qwen2 decoders (`q/k/v/o_proj`,
  `gate/up/down_proj`). Adapters are saved under `experiments/<n>/adapter/`;
  `export` merges them into a standalone model.
- **Full fine-tuning**: every weight, AdamW, fp32. Fits for ≤ 360M parameters
  on a 16–24 GB Mac.
- **MPS**: training runs in fp32 with `bf16=False` set explicitly (TRL would
  otherwise default it on), plain `adamw_torch` instead of the fused CUDA
  optimizer, and `PYTORCH_ENABLE_MPS_FALLBACK=1` so a rare unsupported op
  falls back to CPU instead of crashing.
- **Assistant-only loss**: `--assistant-only-loss` masks the prompt tokens.
  TRL patches the chat template for known families (Qwen2.5 is one); SmolLM2's
  template has no `{% generation %}` markers, so leave it off for SmolLM2.
- **Loss curve**: every logging step appends to `loss.jsonl`;
  `tinystudio losses JOB/N` prints it.

## Base models in the catalog

| Model | Params | License | Notes |
| --- | --- | --- | --- |
| HuggingFaceTB/SmolLM2-135M-Instruct | 0.135B | Apache-2.0 | fastest loop; learns formats and narrow domains |
| HuggingFaceTB/SmolLM2-360M-Instruct | 0.36B | Apache-2.0 | balance for domain Q&A |
| Qwen/Qwen2.5-0.5B-Instruct | 0.49B | Apache-2.0 | strongest under 1B; multilingual |
| HuggingFaceTB/SmolLM2-1.7B-Instruct | 1.7B | Apache-2.0 | LoRA only on 24 GB |
| Qwen/Qwen2.5-1.5B-Instruct | 1.54B | Apache-2.0 | LoRA only on 24 GB |

Any other causal LM on the Hub works with `--model`; the catalog only drives
the recommendation and the default LoRA targets.

## Configuration

Open **Settings** in the web interface to enter the helper LLM endpoint, the
embedding model, Claude and OpenAI credentials and default models, the Hub
token, and the device and dtype. Values are saved to `settings.json` in the
project (owner-only file), applied to every task, the API, the MCP server,
and the assistant, and win over the shell and `.env`. Each card has a
**Test connection** button. An organization-level Anthropic key (one not
created inside a workspace) also needs the workspace id, shown in the ID column of the Console's
Workspaces page (platform.claude.com/settings/workspaces).

For unattended deployments, copy `.env.example` to `.env` (next to it, or in
the directory you run from) or export the variables; the bind address and
port are process settings and only come from there. `tinystudio config`
prints the effective values with secrets shown as set or unset.

## Code layout

```
tinystudio/
  settings.py        environment and .env
  core/              project layout, datasets, documents, quality, synthetic data, traces, helper-LLM client
  training/          SFT, LoRA, QLoRA, DPO (train.py), recommendations, autopilot
  evaluation/        metrics and evaluation runs, dataset analysis
  serving/           model runner, OpenAI-compatible API server, API keys
  interp/            neuron browser, explanations and tests, comparisons, drift
  rag/               repositories (chunks + embeddings + retrieval)
  integrations/      Hugging Face Hub, assistant, tool catalog, MCP server
  api/               FastAPI routers for the web interface, task runner
  cli/               the `tinystudio` command
  ui/                the web interface (vanilla JS single-page app, no build step)
```

## Project layout

```
tinystudio.json
datasets/<name>/datapoints.jsonl, meta.json, cleaning.jsonl
snapshots/<name>/train.jsonl, val.jsonl, meta.json
jobs/<name>/job.json, analysis.json
jobs/<name>/experiments/<n>/config.json, status.json, loss.jsonl, adapter/ | model/, merged/
metrics/<name>.json
evals/<name>/eval.json, results.jsonl
traces.jsonl
autopilot/<name>.jsonl
repositories/<name>/meta.json, chunks.jsonl, embeddings.npy
interp/captures/<name>/meta.json, tokens.json, layer-<L>.npz, explanations.jsonl, tests.jsonl
interp/diffs/<name>/meta.json, tokens.json, layer-<L>.npz
jobs/<name>/experiments/<n>/checkpoints/checkpoint-<step>/, checkpoints.jsonl
.api-keys.json (hashed), .tasks/, .uploads/, .downloads/
```

Everything is plain JSON. Delete a directory to delete the thing.

## Tests

```bash
.venv/bin/python -m pytest -q tests
```

The 54 unit tests cover datapoint validation, splitting, snapshots, metric
scoring, JSON extraction, quality profiling, cleaning rules and PII redaction,
augmentation prompts, preference pairs and DPO configuration, autopilot
bookkeeping, the 42-tool catalog and MCP registration, Hub metadata handling,
DOCX/PPTX extraction, tool-call parsing, JSON-schema validation, and
refine-from resolution, and the interpretability capture, scoring, comparison,
checkpoint references, and drift on a tiny random Llama model (the SmolLM2
tokenizer is read from the local Hub cache), all without downloading anything. The end-to-end paths
(train → eval → serve, JSON mode, refine-from, streamed usage, DPO, autopilot
cycles) are exercised against a real SmolLM2-135M run.

## Sources

- TRL SFTTrainer and SFTConfig (`max_length`, `assistant_only_loss`,
  `processing_class`, `bf16` default), v1.16.0: https://huggingface.co/docs/trl/sft_trainer
- TRL chat templates and the auto-patched families: https://huggingface.co/docs/trl/chat_templates
- TRL DPOTrainer / DPOConfig (conversational preference format, `beta`,
  implicit reference model with PEFT), v1.16: https://huggingface.co/docs/trl/dpo_trainer
- PEFT LoraConfig and `merge_and_unload`, v0.21: https://huggingface.co/docs/peft/en/package_reference/lora
- PyTorch MPS backend notes (macOS 14+) and `PYTORCH_ENABLE_MPS_FALLBACK`:
  https://docs.pytorch.org/docs/2.14/notes/mps.html and
  https://docs.pytorch.org/docs/2.12/mps_environment_variables.html
- bitsandbytes installation matrix (Apple entries are CPU-only):
  https://huggingface.co/docs/bitsandbytes/main/en/installation
- transformers chat templating (`add_generation_prompt`, `apply_chat_template`):
  https://huggingface.co/docs/transformers/main/en/chat_templating
- Model cards: https://huggingface.co/HuggingFaceTB/SmolLM2-135M-Instruct,
  https://huggingface.co/HuggingFaceTB/SmolLM2-360M-Instruct,
  https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct
- huggingface_hub `HfApi.list_models`, `snapshot_download`, `scan_cache_dir`,
  v2.2: https://huggingface.co/docs/huggingface_hub/en/package_reference/hf_api
- MCP Python SDK 2.x server (`MCPServer`, tool annotations):
  https://github.com/modelcontextprotocol/python-sdk
- Anthropic Python SDK tool runner (`client.beta.messages.tool_runner`,
  `beta_tool`): https://github.com/anthropics/anthropic-sdk-python
- OpenAI Responses API function calling:
  https://platform.openai.com/docs/guides/function-calling
- Ollama Modelfile reference: https://github.com/ollama/ollama/blob/main/docs/modelfile.md
- Cammarata, N., Neural Networks Are Already Clean, Goodfire, 2026: https://already-clean.goodfire.com/
  (recursive generalization, negative poles, SwiGLU sign regimes, blind-maximum and near-miss tests)
- PyTorch module hooks, v2.14: https://docs.pytorch.org/docs/2.14/generated/torch.nn.Module.html
- transformers TrainingArguments (`save_strategy`, `save_steps`, `save_total_limit`,
  `save_only_model`), v5.20: https://huggingface.co/docs/transformers/main_classes/trainer
- PEFT `PeftModel.disable_adapter`, v0.21: https://huggingface.co/docs/peft/v0.21.0/en/package_reference/peft_model
- The Studio concepts reproduced here come from Prem's public OpenAPI document,
  documentation, archived 2024–2025 platform docs and blog posts; see
  `reports/prem-studio-reverse-engineering.md` and
  `reports/Prem Studio gaps and integrations.md` in the REA checkout.
