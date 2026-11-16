"""The model a local specialist thinks with: yours.

Two ways, both over plain HTTPS with the standard library:

* **Anthropic**, with your own ``ANTHROPIC_API_KEY``;
* **any OpenAI-compatible server**: a model you run yourself (Ollama at
  ``http://localhost:11434/v1``, LM Studio, vLLM) or a hosted one.

The conversation is kept in one neutral shape and translated for each, so
the rest of somame-local never depends on a vendor's format.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol

Turn = dict[str, Any]
"""``{"role": "user", "text"}``, ``{"role": "assistant", "text", "calls"}``
or ``{"role": "tool", "results": [{"id", "name", "content", "error"}]}``."""

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_ANTHROPIC_MODEL = "claude-opus-5-5"


class ModelError(RuntimeError):
    """The model could not be reached, or refused."""


@dataclass(frozen=True, slots=True)
class Call:
    id: str
    name: str
    args: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Reply:
    text: str
    calls: list[Call] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    schema: dict[str, Any]


class Model(Protocol):
    def complete(self, system: str, turns: list[Turn], tools: list[ToolSpec]) -> Reply: ...


def _post(url: str, body: dict[str, Any], headers: dict[str, str], timeout: float = 300) -> Any:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"content-type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        raise ModelError(f"The model refused ({exc.code}): {detail}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ModelError(f"The model could not be reached: {exc}") from exc


class Anthropic:
    def __init__(self, api_key: str, model: str = DEFAULT_ANTHROPIC_MODEL, max_tokens: int = 8000):
        if not api_key:
            raise ModelError("Set ANTHROPIC_API_KEY, or use --base-url for a model you run.")
        self.api_key, self.model, self.max_tokens = api_key, model, max_tokens

    def _messages(self, turns: list[Turn]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for turn in turns:
            if turn["role"] == "user":
                out.append({"role": "user", "content": turn["text"]})
            elif turn["role"] == "assistant":
                blocks: list[dict[str, Any]] = []
                if turn.get("text"):
                    blocks.append({"type": "text", "text": turn["text"]})
                for call in turn.get("calls", []):
                    blocks.append(
                        {"type": "tool_use", "id": call.id, "name": call.name, "input": call.args}
                    )
                out.append({"role": "assistant", "content": blocks})
            else:
                out.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": r["id"],
                                "content": r["content"],
                                "is_error": bool(r.get("error")),
                            }
                            for r in turn["results"]
                        ],
                    }
                )
        return out

    def complete(self, system: str, turns: list[Turn], tools: list[ToolSpec]) -> Reply:
        body = _post(
            ANTHROPIC_URL,
            {
                "model": self.model,
                "max_tokens": self.max_tokens,
                "system": system,
                "messages": self._messages(turns),
                "tools": [
                    {"name": t.name, "description": t.description, "input_schema": t.schema}
                    for t in tools
                ],
            },
            {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"},
        )
        text = "".join(
            b.get("text", "") for b in body.get("content", []) if b.get("type") == "text"
        )
        calls = [
            Call(str(b["id"]), str(b["name"]), dict(b.get("input") or {}))
            for b in body.get("content", [])
            if b.get("type") == "tool_use"
        ]
        return Reply(text.strip(), calls)


class OpenAICompatible:
    def __init__(self, base_url: str, model: str, api_key: str = ""):
        if not model:
            raise ModelError("Say which model to use with --model, such as llama3.1.")
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model, self.api_key = model, api_key

    def _messages(self, system: str, turns: list[Turn]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for turn in turns:
            if turn["role"] == "user":
                out.append({"role": "user", "content": turn["text"]})
            elif turn["role"] == "assistant":
                message: dict[str, Any] = {"role": "assistant", "content": turn.get("text") or ""}
                if turn.get("calls"):
                    message["tool_calls"] = [
                        {
                            "id": c.id,
                            "type": "function",
                            "function": {"name": c.name, "arguments": json.dumps(c.args)},
                        }
                        for c in turn["calls"]
                    ]
                out.append(message)
            else:
                for r in turn["results"]:
                    out.append({"role": "tool", "tool_call_id": r["id"], "content": r["content"]})
        return out

    def complete(self, system: str, turns: list[Turn], tools: list[ToolSpec]) -> Reply:
        headers = {"authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        body = _post(
            self.url,
            {
                "model": self.model,
                "messages": self._messages(system, turns),
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": t.name,
                            "description": t.description,
                            "parameters": t.schema,
                        },
                    }
                    for t in tools
                ],
            },
            headers,
        )
        try:
            message = body["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelError("The model answered with an unexpected shape.") from exc
        calls = []
        for index, call in enumerate(message.get("tool_calls") or []):
            function = call.get("function") or {}
            raw = function.get("arguments") or "{}"
            try:
                args = json.loads(raw) if isinstance(raw, str) else dict(raw)
            except json.JSONDecodeError:
                args = {}
            calls.append(
                Call(str(call.get("id") or f"call_{index}"), str(function.get("name")), args)
            )
        return Reply(str(message.get("content") or "").strip(), calls)
