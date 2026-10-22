"""MCP server: lets Claude Code, Codex, Claude Desktop, or any MCP client drive tinystudio.

The tool catalog lives in ``tools.py`` and is shared with the in-app
assistant. Long actions return a task id; ``get_task`` polls it. If no server
is listening, the first call starts ``tinystudio serve`` for the project.

Register once (user scope):

    claude mcp add --scope user tinystudio -- /path/to/.venv/bin/tinystudio mcp --project /path/to/project
    Codex: ~/.codex/config.toml  [mcp_servers.tinystudio] command = ".../tinystudio" args = ["mcp", "--project", "..."]
    Claude Desktop: claude_desktop_config.json  "mcpServers": {"tinystudio": {"command": ".../tinystudio", "args": ["mcp", "--project", "..."]}}
"""

from __future__ import annotations

import functools
import logging
import os
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from .tools import Studio, StudioError, ToolSpec, tool_specs

INSTRUCTIONS = (
    "tinystudio trains, evaluates, and serves tiny and domain-specific language models on this machine. "
    "Pipeline: dataset (create_dataset_from_jsonl or create_synthetic_dataset) -> profile_dataset/clean_dataset -> split_dataset -> create_snapshot -> "
    "recommend -> create_job + add_experiment + start_job -> get_job until succeeded -> run_eval -> get_eval -> chat. "
    "Long actions return a task id; poll get_task every few seconds. Model ids are 'job/number' or the alias 'ft-job-number'. "
    "search_hub_models and download_hub_model bring any Hugging Face causal LM in as a base model."
)
OPEN_WORLD = {"search_hub_models", "download_hub_model", "create_synthetic_dataset"}


def _annotations(spec: ToolSpec) -> ToolAnnotations:
    return ToolAnnotations(
        title="Starts a background task" if spec.long_running else None,
        read_only_hint=spec.read_only,
        destructive_hint=spec.destructive,
        idempotent_hint=spec.read_only or spec.destructive,
        open_world_hint=spec.name in OPEN_WORLD,
    )


def _wrap(spec: ToolSpec) -> Any:
    """Translate expected failures into MCP tool errors (clean message, no traceback)."""

    @functools.wraps(spec.func)
    def call(*args: Any, **kwargs: Any) -> Any:
        try:
            return spec.func(*args, **kwargs)
        except StudioError as cause:
            raise ToolError(str(cause)) from cause

    return call


def build_server(studio: Studio) -> MCPServer:
    server = MCPServer("tinystudio", instructions=INSTRUCTIONS)
    for spec in tool_specs(studio):
        server.add_tool(_wrap(spec), name=spec.name, description=spec.description, annotations=_annotations(spec))
    return server


def main(base_url: str | None = None, project: str | None = None) -> None:
    logging.getLogger("httpx").setLevel(logging.WARNING)  # keep stdio clean; MCP clients treat stderr as noise
    url = base_url or os.environ.get("TINYSTUDIO_URL", "http://127.0.0.1:8000")
    root = Path(project or os.environ.get("TINYSTUDIO_PROJECT", ".")).expanduser().resolve()
    studio = Studio(url, root if (root / "tinystudio.json").exists() else None)
    build_server(studio).run(transport="stdio")


if __name__ == "__main__":
    main()
