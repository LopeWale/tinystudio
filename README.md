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
  JSON-answer share, curation ratings, PII-like patterns. *Clean* applies
  dedupe, outlier, rating, label, JSON, and whitespace rules in place and logs
  every run to `datasets/<name>/cleaning.jsonl` (`tinystudio dataset clean`).
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

## Coding assistants and the in-app assistant

tinystudio is an MCP server: every capability (40 tools) is available to
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
| Synthetic data from documents | `dataset synth --from doc.pdf ...` | needs a helper LLM (below) |
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
| Download weights | `export JOB/N DIR`, `hub upload experiment` | merged Hugging Face model directory or a Hub repo |
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

## Project layout

```
tinystudio.json
datasets/<name>/datapoints.jsonl, meta.json
snapshots/<name>/train.jsonl, val.jsonl, meta.json
jobs/<name>/job.json
jobs/<name>/experiments/<n>/config.json, status.json, loss.jsonl, adapter/ | model/
metrics/<name>.json
evals/<name>/eval.json, results.jsonl
traces.jsonl
```

Everything is plain JSON. Delete a directory to delete the thing.

## Tests

```bash
.venv/bin/python -m pytest -q tests
```

The unit tests cover datapoint validation, splitting, snapshots, metric
scoring, and JSON extraction without loading a model. The end-to-end path
(train → eval → serve) is the tour above.

## Sources

- TRL SFTTrainer and SFTConfig (`max_length`, `assistant_only_loss`,
  `processing_class`, `bf16` default), v1.16.0: https://huggingface.co/docs/trl/sft_trainer
- TRL chat templates and the auto-patched families: https://huggingface.co/docs/trl/chat_templates
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
- The Studio concepts reproduced here come from Prem's public OpenAPI document
  and documentation; see `reports/prem-studio-reverse-engineering.md` in the
  REA checkout.
