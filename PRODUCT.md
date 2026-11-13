# TinyStudio

Self-hosted tooling for preparing domain datasets, fine-tuning small language models, evaluating results, inspecting model evidence and serving models. Laboratory adds experiment definitions, operations, replay and scientific views. Projects scope work; users and roles govern administration. The assistant operates alongside actual page evidence.

Tiny Studio provides general mechanisms for researcher-authored experiments; individual papers and example benches do not define platform-wide scientific limits. Tracking, data collection and auditable statistical computation, including sample-size and power planning with assistant support, are priority capabilities. See [experimentation principles](docs/experimentation-principles.md) for the user direction recorded on 2026-11-12.

Existing API/controllers are product truth. Dataset imports, repository indexing, training jobs, model serving and assistant conversations have distinct lifecycle and persistence contracts. Never simulate production success or replace real operations with illustrative data.

The current redesign covers all interfaces and begins with reusable components, blocks and templates. The user explicitly approved implementation on 2026-10-27. Existing work in the shared checkout must remain intact.
