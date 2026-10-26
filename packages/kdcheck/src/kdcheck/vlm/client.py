"""A vision-language model on an OpenAI-compatible server: LM Studio, llama.cpp, Ollama or vLLM (ADR-0014).

The core sends an image and a prompt from ``prompts/`` to ``/chat/completions`` and asks for the answer as the JSON
``{"text": …}``. A reasoning model then answers at once instead of spending its tokens on reasoning, and a reply in
any other form is no reading at all. The server must run on this machine unless ``allow_remote`` says otherwise.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from kdcheck.config import ConfigError, VlmEngine, prompts_dir

ANSWER = {
    "type": "json_schema",
    "json_schema": {
        "name": "cell_text",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
    },
}


class VlmError(Exception):
    """The model gave no reading. ``fatal``: the server does not answer, so the next request would fail too."""

    def __init__(self, message: str, *, fatal: bool = False) -> None:
        super().__init__(message)
        self.fatal = fatal


@dataclass(frozen=True)
class Prompt:
    name: str
    text: str

    @property
    def version(self) -> str:
        """The name and the start of the SHA-256: an edited file without a new name still shows in the report."""
        return f"{self.name} sha256:{hashlib.sha256(self.text.encode('utf-8')).hexdigest()[:12]}"


def load_prompt(name: str, directory: Path | None = None) -> Prompt:
    path = prompts_dir(directory) / f"{name}.txt"
    try:
        return Prompt(name=name, text=path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"Нет промпта {path}: проверьте vlm.prompt в models.yaml и ARM_PROMPTS_DIR ({exc})") from None


class VlmClient:
    def __init__(self, engine: VlmEngine, prompt: Prompt) -> None:
        self.engine = engine
        self.prompt = prompt
        self.url = engine.base_url.rstrip("/")
        self.calls = 0  # readings asked for, answered or not

    @property
    def version(self) -> str:
        return self.prompt.version

    def unavailable(self) -> str | None:
        """Why the model cannot read now, or None: the server answers and serves the model."""
        try:
            listed = self._request("/models", None)
        except VlmError as exc:
            return str(exc)
        models = listed.get("data") if isinstance(listed, dict) else None
        ids = {m.get("id") for m in models or [] if isinstance(m, dict)}
        if self.engine.model not in ids:
            return f"на сервере {self.url} нет модели {self.engine.model}"
        return None

    def read(self, image: Image.Image, label: str) -> str:
        """The text of the cell ``label`` in ``image``; an empty string when the model sees none."""
        png = io.BytesIO()
        image.save(png, "PNG")
        picture = "data:image/png;base64," + base64.b64encode(png.getvalue()).decode("ascii")
        body = {
            "model": self.engine.model,
            "temperature": 0,
            "seed": self.engine.seed,
            "max_tokens": self.engine.max_tokens,
            "response_format": ANSWER,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        # Replace, not format: braces of a JSON example in the prompt stay as they are.
                        {"type": "text", "text": self.prompt.text.replace("{label}", label)},
                        {"type": "image_url", "image_url": {"url": picture}},
                    ],
                }
            ],
        }
        self.calls += 1
        reply = self._request("/chat/completions", body)
        try:
            text = json.loads(reply["choices"][0]["message"]["content"])["text"]
        except (KeyError, IndexError, TypeError, ValueError):
            raise VlmError('ответ модели не в виде JSON {"text": …}') from None
        if not isinstance(text, str):
            raise VlmError("в ответе модели поле text — не строка")
        return text.strip()

    def _request(self, path: str, body: dict[str, Any] | None) -> Any:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(
            self.url + path, data=data, headers={"Content-Type": "application/json"} if data else {}
        )
        try:
            with urllib.request.urlopen(request, timeout=self.engine.timeout_sec) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:  # the server answered: this request failed, the next may not
            raise VlmError(f"сервер {self.url} ответил ошибкой {exc.code}: {exc.reason}") from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            raise VlmError(f"сервер {self.url} не отвечает: {exc}", fatal=True) from None
        except ValueError:
            raise VlmError(f"сервер {self.url} ответил не JSON") from None
