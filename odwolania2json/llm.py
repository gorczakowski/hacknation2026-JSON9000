"""Minimal OpenAI-compatible chat client for the model fallback (default: ARK Labs, Qwen3.5-4B, EU-hosted).

Configured in config/pipeline.yaml under `llm:`; the API key is read from the environment / .env
(variable named by `api_key_env`). Calls are limited to `max_parallel` at a time and retried on 429/5xx.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time

import httpx

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


class LLM:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.base_url = cfg["base_url"].rstrip("/")
        self.model = cfg["model"]
        self.key = os.environ.get(cfg.get("api_key_env", "LLM_API_KEY"), "")
        self.timeout = float(cfg.get("timeout_s", 120))
        self.max_tokens = int(cfg.get("max_tokens", 3000))
        self.extra = cfg.get("extra_body") or {}
        self.sem = threading.Semaphore(int(cfg.get("max_parallel", 3)))
        self.http = httpx.Client(timeout=self.timeout)
        self.calls = 0
        self.failures = 0
        self.lock = threading.Lock()

    @property
    def available(self) -> bool:
        return bool(self.key) and bool(self.cfg.get("enabled", True))

    def chat_json(self, system: str, user: str) -> dict:
        """Ask for a JSON object; returns the parsed dict or raises LLMError."""
        body = {"model": self.model, "temperature": 0, "max_tokens": self.max_tokens,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "response_format": {"type": "json_object"}, **self.extra}
        headers = {"Authorization": f"Bearer {self.key}"}
        last = None
        # 404 is retried too: ARK briefly reported "model does not exist" during a backend restart, and treating
        # it as final made ARK lock the key for a minute ("Too many invalid request attempts").
        retryable = (404, 408, 409, 425, 429, 500, 502, 503, 504)
        for attempt in range(int(self.cfg.get("retries", 4))):
            wait = min(20, 3 * (attempt + 1))
            try:
                with self.sem:
                    r = self.http.post(f"{self.base_url}/chat/completions", json=body, headers=headers)
                if r.status_code in retryable:
                    if "minute" in r.text.lower():
                        wait = 30
                    raise LLMError(f"{r.status_code}: {r.text[:200]}")
                if r.status_code != 200:
                    with self.lock:
                        self.failures += 1
                    raise LLMError(f"{r.status_code}: {r.text[:300]}")  # e.g. 400/401: not retried
                content = r.json()["choices"][0]["message"].get("content") or ""
                with self.lock:
                    self.calls += 1
                return parse_json(content)
            except LLMError as e:
                last = e
                if not str(e)[:3].isdigit() or int(str(e)[:3]) not in retryable:
                    raise
            except (httpx.HTTPError, ValueError, KeyError) as e:
                last = e
            time.sleep(wait)
        with self.lock:
            self.failures += 1
        raise LLMError(f"LLM call failed after retries: {last}")


def parse_json(text: str) -> dict:
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    if not t.startswith("{"):
        a, b = t.find("{"), t.rfind("}")
        t = t[a:b + 1] if a >= 0 and b > a else t
    obj = json.loads(t)
    if not isinstance(obj, dict):
        raise ValueError("model did not return a JSON object")
    return obj
