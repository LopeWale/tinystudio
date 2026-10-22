"""In-app assistant: a frontier model that drives the same tool catalog the MCP server exposes.

Provider ``anthropic`` uses the Anthropic Python SDK's tool runner with
Claude Opus 5.5 (adaptive thinking, explicit effort, server-side refusal
fallbacks). Credentials come from the environment: ``ANTHROPIC_API_KEY``,
``ANTHROPIC_AUTH_TOKEN``, or an ``ant auth login`` profile. Provider
``openai`` uses the OpenAI Responses API with function calling and needs
``OPENAI_API_KEY`` plus ``TINYSTUDIO_OPENAI_MODEL``.

The chat endpoint streams events the page renders live: text deltas, tool
calls with their inputs, tool results, and a final usage line. Destructive
tools (``delete``) are only offered when the caller opts in.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .tools import Studio, StudioError, ToolSpec, tool_specs

ANTHROPIC_MODEL = os.environ.get("TINYSTUDIO_ANTHROPIC_MODEL", "claude-opus-5-5")
ANTHROPIC_EFFORT = os.environ.get("TINYSTUDIO_ASSISTANT_EFFORT", "medium")
OPENAI_MODEL = os.environ.get("TINYSTUDIO_OPENAI_MODEL", "")
MAX_RESULT_CHARS = 12000

SYSTEM = """You are the tinystudio assistant. tinystudio trains, evaluates, and serves tiny and domain-specific language models on the user's own machine (Apple Silicon, PyTorch MPS). You act through tools that call the local server; nothing leaves the machine except your own API calls.

Pipeline: dataset -> profile and clean -> split -> snapshot -> recommend -> job + experiments -> start_job -> get_job -> run_eval -> get_eval -> chat/serve. Long tools return a task id; poll get_task until it succeeds or fails, then read the result with the matching get_* tool. Prefer small, concrete steps and report numbers (counts, losses, scores). Before destructive actions, say what would be deleted and ask. When the helper LLM is not configured, say so instead of retrying. Keep replies short and factual."""


def anthropic_available() -> bool:
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return True
    profile_dir = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "anthropic"
    return profile_dir.exists() and any(profile_dir.iterdir())


def openai_available() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY")) and bool(OPENAI_MODEL)


def status() -> dict[str, Any]:
    return {
        "anthropic": {"available": anthropic_available(), "model": ANTHROPIC_MODEL, "effort": ANTHROPIC_EFFORT},
        "openai": {"available": openai_available(), "model": OPENAI_MODEL or None},
    }


def _selected(studio: Studio, allow_destructive: bool) -> list[ToolSpec]:
    return [spec for spec in tool_specs(studio) if allow_destructive or not spec.destructive]


def _truncate(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= MAX_RESULT_CHARS else text[:MAX_RESULT_CHARS] + f"... [truncated {len(text) - MAX_RESULT_CHARS} chars]"


def _json_tool(spec: ToolSpec):
    """Tool functions return JSON text so every SDK sees the same payload; expected errors become error results."""
    import functools

    @functools.wraps(spec.func)
    def call(*args: Any, **kwargs: Any) -> str:
        try:
            return _truncate(spec.func(*args, **kwargs))
        except StudioError as cause:
            return json.dumps({"error": str(cause)})

    return call


# ---------------------------------------------------------------------- anthropic
def run_anthropic(studio: Studio, messages: list[dict[str, str]], allow_destructive: bool = False) -> Iterator[dict[str, Any]]:
    """Yield stream events for one assistant turn: text, tool_call, tool_result, done, error."""
    import anthropic
    from anthropic import beta_tool

    if not anthropic_available():
        yield {"type": "error", "message": "no Anthropic credentials: set ANTHROPIC_API_KEY (or run `ant auth login`) before starting tinystudio serve"}
        return
    try:
        client = anthropic.Anthropic()
    except Exception as cause:  # noqa: BLE001 - the SDK raises a TypeError when no credential source resolves
        yield {"type": "error", "message": f"Anthropic client could not be created: {cause}"}
        return
    tools = [beta_tool(_json_tool(spec), name=spec.name, description=spec.description) for spec in _selected(studio, allow_destructive)]
    try:
        runner = client.beta.messages.tool_runner(
            model=ANTHROPIC_MODEL,
            max_tokens=16000,
            system=SYSTEM,
            tools=tools,
            messages=[{"role": m["role"], "content": m["content"]} for m in messages],
            stream=True,
            max_iterations=30,
            output_config={"effort": ANTHROPIC_EFFORT},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        usage = {"input_tokens": 0, "output_tokens": 0}
        for stream in runner:
            for event in stream:
                if event.type == "content_block_delta" and getattr(event.delta, "type", "") == "text_delta":
                    yield {"type": "text", "delta": event.delta.text}
            message = stream.get_final_message()
            usage["input_tokens"] += message.usage.input_tokens
            usage["output_tokens"] += message.usage.output_tokens
            tool_uses = [b for b in message.content if b.type == "tool_use"]
            if message.stop_reason == "refusal":
                yield {"type": "error", "message": "the model declined this request"}
                return
            if message.stop_reason == "max_tokens" and tool_uses:
                yield {"type": "error", "message": "the reply hit max_tokens mid tool call; ask again more narrowly"}
                return
            if not tool_uses:
                break
            for block in tool_uses:
                yield {"type": "tool_call", "id": block.id, "name": block.name, "input": block.input}
            response = runner.generate_tool_call_response()
            if response:
                names = {b.id: b.name for b in tool_uses}
                for result in response["content"]:
                    if isinstance(result, dict) and result.get("type") == "tool_result":
                        content = result.get("content")
                        text = content if isinstance(content, str) else json.dumps(content, default=str)
                        yield {"type": "tool_result", "id": result.get("tool_use_id"), "name": names.get(result.get("tool_use_id"), "?"), "output": text[:4000]}
        yield {"type": "done", "usage": usage, "model": ANTHROPIC_MODEL}
    except anthropic.AuthenticationError:
        yield {"type": "error", "message": "Anthropic rejected the credentials: set ANTHROPIC_API_KEY (or run `ant auth login`) and restart tinystudio serve"}
    except anthropic.RateLimitError:
        yield {"type": "error", "message": "Anthropic rate limit; try again in a moment"}
    except anthropic.APIStatusError as cause:
        yield {"type": "error", "message": f"Anthropic API error {cause.status_code}: {cause.message}"}
    except anthropic.APIConnectionError as cause:
        yield {"type": "error", "message": f"could not reach the Anthropic API: {cause}"}


# ---------------------------------------------------------------------- openai
def _openai_tools(specs: list[ToolSpec]) -> list[dict[str, Any]]:
    """Function-calling definitions derived from the same signatures the Anthropic SDK uses."""
    from anthropic import beta_tool

    definitions = []
    for spec in specs:
        schema = beta_tool(spec.func, name=spec.name, description=spec.description).to_dict()
        definitions.append({"type": "function", "name": spec.name, "description": spec.description, "parameters": schema["input_schema"]})
    return definitions


def run_openai(studio: Studio, messages: list[dict[str, str]], allow_destructive: bool = False) -> Iterator[dict[str, Any]]:
    """Same event stream through the OpenAI Responses API (function calling)."""
    import openai

    if not OPENAI_MODEL:
        yield {"type": "error", "message": "set TINYSTUDIO_OPENAI_MODEL to the OpenAI model id you want to use"}
        return
    if not os.environ.get("OPENAI_API_KEY"):
        yield {"type": "error", "message": "no OpenAI credentials: set OPENAI_API_KEY before starting tinystudio serve"}
        return
    try:
        client = openai.OpenAI()
    except Exception as cause:  # noqa: BLE001
        yield {"type": "error", "message": f"OpenAI client could not be created: {cause}"}
        return
    specs = _selected(studio, allow_destructive)
    functions = {spec.name: _json_tool(spec) for spec in specs}
    tools = _openai_tools(specs)
    conversation: list[Any] = [{"role": m["role"], "content": m["content"]} for m in messages]
    usage = {"input_tokens": 0, "output_tokens": 0}
    try:
        for _ in range(30):
            with client.responses.stream(model=OPENAI_MODEL, instructions=SYSTEM, input=conversation, tools=tools) as stream:
                for event in stream:
                    if event.type == "response.output_text.delta":
                        yield {"type": "text", "delta": event.delta}
                response = stream.get_final_response()
            if response.usage:
                usage["input_tokens"] += response.usage.input_tokens
                usage["output_tokens"] += response.usage.output_tokens
            calls = [item for item in response.output if item.type == "function_call"]
            if not calls:
                break
            conversation.extend(item.model_dump(exclude_none=True) for item in response.output)
            for call in calls:
                try:
                    arguments = json.loads(call.arguments or "{}")
                except json.JSONDecodeError:
                    arguments = {}
                yield {"type": "tool_call", "id": call.call_id, "name": call.name, "input": arguments}
                function = functions.get(call.name)
                output = function(**arguments) if function else json.dumps({"error": f"unknown tool {call.name}"})
                yield {"type": "tool_result", "id": call.call_id, "name": call.name, "output": output[:4000]}
                conversation.append({"type": "function_call_output", "call_id": call.call_id, "output": output})
        yield {"type": "done", "usage": usage, "model": OPENAI_MODEL}
    except openai.AuthenticationError:
        yield {"type": "error", "message": "OpenAI rejected the credentials: set OPENAI_API_KEY and restart tinystudio serve"}
    except openai.APIStatusError as cause:
        yield {"type": "error", "message": f"OpenAI API error {cause.status_code}: {cause.message}"}
    except openai.APIConnectionError as cause:
        yield {"type": "error", "message": f"could not reach the OpenAI API: {cause}"}


def run(provider: str, studio: Studio, messages: list[dict[str, str]], allow_destructive: bool = False) -> Iterator[dict[str, Any]]:
    if provider == "anthropic":
        return run_anthropic(studio, messages, allow_destructive)
    if provider == "openai":
        return run_openai(studio, messages, allow_destructive)
    raise StudioError(f"unknown provider {provider!r}")
