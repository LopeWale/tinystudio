"""tinystudio: local pipeline for tiny and domain-specific language models.

Packages: core (projects, datasets, documents, quality, synthetic data, traces, helper LLM), training (SFT, LoRA,
QLoRA, DPO, recommendations, autopilot), evaluation (metrics, evaluations, analysis), serving (model runner,
OpenAI-compatible API, keys), interp (neuron browser, explanations, tests, comparisons, drift), rag (repositories),
integrations (Hugging Face Hub, assistant, tool catalog, MCP server), api (FastAPI routers, task runner), cli.
"""

from .settings import load_dotenv

__version__ = "0.1.0"
load_dotenv()
