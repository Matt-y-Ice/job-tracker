"""Minimal client for the local Ollama server: chat with JSON output, embeddings, and status.

Everything runs on this computer. Nothing here calls a paid or remote API.
"""
import json
import logging

import requests

from .platform_text import ollama_not_running, setup_hint

log = logging.getLogger("jobtracker.ollama")


class OllamaError(Exception):
    """Ollama is not reachable, a model is missing, or a request failed. Message is user-facing."""


class Ollama:
    """Talks to Ollama's HTTP API on localhost."""

    def __init__(self, host: str = "http://localhost:11434", timeout: float = 600):
        self.host, self.timeout = host.rstrip("/"), timeout
        self.session = requests.Session()

    def _post(self, path: str, payload: dict) -> dict:
        try:
            resp = self.session.post(f"{self.host}{path}", json=payload, timeout=self.timeout)
        except requests.ConnectionError as exc:
            raise OllamaError(ollama_not_running()) from exc
        except requests.Timeout as exc:
            raise OllamaError(f"Ollama took longer than {self.timeout:.0f}s to answer. "
                              f"The model may be too large for this computer.") from exc
        if resp.status_code == 404 and "not found" in resp.text:
            model = payload.get("model", "")
            raise OllamaError(f"The model '{model}' is not downloaded. Run {setup_hint()}, "
                              f"or run:  ollama pull {model}")
        if resp.status_code != 200:
            raise OllamaError(f"Ollama returned HTTP {resp.status_code}: {resp.text[:200]}")
        return resp.json()

    def check(self, *models: str) -> None:
        """Raise OllamaError unless the server answers and every model is downloaded."""
        try:
            tags = self.session.get(f"{self.host}/api/tags", timeout=10).json()
        except requests.RequestException as exc:
            raise OllamaError(ollama_not_running()) from exc
        have = {m["name"] for m in tags.get("models", [])}
        for model in models:
            if model not in have and f"{model}:latest" not in have:
                raise OllamaError(f"The model '{model}' is not downloaded. Run {setup_hint()}, "
                                  f"or run:  ollama pull {model}")

    def chat_json(self, model: str, messages: list[dict], schema: dict, options: dict,
                  keep_alive: str = "15m") -> dict:
        """Ask for a reply that follows `schema`. Returns the parsed JSON (may still need validation)."""
        data = self._post("/api/chat", {"model": model, "messages": messages, "stream": False,
                                        "format": schema, "options": options, "keep_alive": keep_alive})
        content = data.get("message", {}).get("content", "")
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError(f"reply was not valid JSON: {content[:120]!r}") from exc

    def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        """One embedding vector per text."""
        data = self._post("/api/embed", {"model": model, "input": texts, "truncate": True,
                                         "keep_alive": "15m"})
        return data["embeddings"]

    def processor(self, model: str) -> str:
        """'GPU', 'CPU', or 'GPU+CPU' for a loaded model, from how much of it sits in GPU memory."""
        try:
            loaded = self.session.get(f"{self.host}/api/ps", timeout=10).json().get("models", [])
        except requests.RequestException:
            return "unknown"
        for entry in loaded:
            if entry["name"] in (model, f"{model}:latest"):
                size, vram = entry.get("size") or 0, entry.get("size_vram") or 0
                if not vram:
                    return "CPU"
                return "GPU" if vram >= size * 0.98 else f"GPU+CPU ({vram / size:.0%} on GPU)"
        return "unknown"
