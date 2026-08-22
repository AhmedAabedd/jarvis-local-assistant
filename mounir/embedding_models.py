"""Universal embedding connections and the isolated GBrain adapter.

Mounir stores protocols, endpoints, and model identifiers supplied by the user.
Only the small translation into GBrain's provider syntax is provider-specific.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import requests

from . import knowledge_protocol
from .setup_gbrain import _gbrain_executable, ensure_local_gbrain

REQUEST_TIMEOUT_SECONDS = 30
MIGRATION_TIMEOUT_SECONDS = 1800
PROBE_TEXT = "Mounir embedding connection test"


def _resolved_key(value: str) -> str:
    raw = str(value or "")
    expanded = os.path.expandvars(raw)
    return "" if expanded == raw and raw.strip().startswith("$") else expanded


def _headers(api_key: str) -> dict[str, str]:
    key = _resolved_key(api_key)
    return {"Authorization": f"Bearer {key}"} if key else {}


def _error_detail(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error)
        if error:
            return str(error)
        if payload.get("message"):
            return str(payload["message"])
    return (response.text or f"HTTP {response.status_code}").strip()[:1000]


def _request(method: str, url: str, **kwargs) -> requests.Response:
    try:
        response = requests.request(
            method,
            url,
            timeout=REQUEST_TIMEOUT_SECONDS,
            **kwargs,
        )
    except requests.RequestException as exc:
        raise ValueError(f"Connection failed: {exc}") from exc
    if not response.ok:
        raise ValueError(f"Connection failed: {_error_detail(response)}")
    return response


def _ollama_root(base_url: str) -> str:
    root = str(base_url or "").strip().rstrip("/")
    return root.removesuffix("/v1")


def discover_models(config: dict[str, Any]) -> list[str]:
    """Discover model identifiers through the selected public protocol."""
    adapter = str(config.get("adapter") or "openai_compatible")
    base_url = str(config.get("base_url") or "").rstrip("/")
    if adapter == "ollama":
        payload = _request(
            "GET",
            f"{_ollama_root(base_url)}/api/tags",
            headers=_headers(str(config.get("api_key") or "")),
        ).json()
        entries = payload.get("models", []) if isinstance(payload, dict) else []
        names = [
            str(item.get("name") or item.get("model") or "").strip()
            for item in entries
            if isinstance(item, dict)
        ]
    elif adapter == "openai_compatible":
        payload = _request(
            "GET",
            f"{base_url}/models",
            headers=_headers(str(config.get("api_key") or "")),
        ).json()
        entries = payload.get("data", []) if isinstance(payload, dict) else []
        names = [
            str(item.get("id") or "").strip()
            for item in entries
            if isinstance(item, dict)
        ]
    else:
        raise ValueError("Unsupported embedding adapter.")
    return sorted(dict.fromkeys(name for name in names if name), key=str.casefold)


def test_connection(config: dict[str, Any]) -> int:
    """Embed one short string and return the provider's actual vector width."""
    adapter = str(config.get("adapter") or "openai_compatible")
    model = str(config.get("model") or "").strip()
    base_url = str(config.get("base_url") or "").rstrip("/")
    if not model:
        raise ValueError("Model ID is required.")
    if adapter == "ollama":
        payload = _request(
            "POST",
            f"{_ollama_root(base_url)}/api/embed",
            headers=_headers(str(config.get("api_key") or "")),
            json={"model": model, "input": [PROBE_TEXT]},
        ).json()
        vectors = payload.get("embeddings") if isinstance(payload, dict) else None
        vector = vectors[0] if isinstance(vectors, list) and vectors else None
        if vector is None and isinstance(payload, dict):
            vector = payload.get("embedding")
    elif adapter == "openai_compatible":
        payload = _request(
            "POST",
            f"{base_url}/embeddings",
            headers=_headers(str(config.get("api_key") or "")),
            json={"model": model, "input": [PROBE_TEXT]},
        ).json()
        data = payload.get("data") if isinstance(payload, dict) else None
        vector = data[0].get("embedding") if isinstance(data, list) and data else None
    else:
        raise ValueError("Unsupported embedding adapter.")
    if not isinstance(vector, list) or not vector:
        raise ValueError("The provider returned no embedding vector.")
    if not all(isinstance(value, (int, float)) for value in vector):
        raise ValueError("The provider returned an invalid embedding vector.")
    return len(vector)


def gbrain_target(config: dict[str, Any]) -> str:
    # GBrain's LiteLLM recipe is its generic OpenAI-compatible seam: it accepts
    # arbitrary user-supplied model IDs and an optional bearer credential.
    prefix = "ollama" if config.get("adapter") == "ollama" else "litellm"
    return f"{prefix}:{config['model']}"


def gbrain_provider_environment(config: dict[str, Any]) -> dict[str, str]:
    """Translate one standard saved connection into GBrain's isolated env."""
    base_url = str(config.get("base_url") or "").rstrip("/")
    api_key = _resolved_key(str(config.get("api_key") or ""))
    if config.get("adapter") == "ollama":
        if not base_url.endswith("/v1"):
            base_url += "/v1"
        result = {"OLLAMA_BASE_URL": base_url}
        if api_key:
            result["OLLAMA_API_KEY"] = api_key
        return result
    result = {"LITELLM_BASE_URL": base_url}
    if api_key:
        result["LITELLM_API_KEY"] = api_key
    return result


def _config_path() -> Path:
    return knowledge_protocol.local_home_parent() / ".gbrain" / "config.json"


def _write_embedding_state(enabled: bool) -> None:
    path = _config_path()
    if not path.is_file():
        raise RuntimeError("GBrain is not initialized. Run its automatic setup first.")
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Could not read GBrain configuration: {exc}") from exc
    if not isinstance(config, dict):
        raise RuntimeError("GBrain configuration is invalid.")
    config["embedding_disabled"] = not enabled
    temporary = path.with_suffix(".json.tmp")
    try:
        temporary.write_text(
            json.dumps(config, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    except OSError as exc:
        raise RuntimeError(f"Could not update GBrain configuration: {exc}") from exc


def apply_to_gbrain(enabled: bool, config: dict[str, Any] | None = None) -> None:
    """Apply the saved Knowledge choice and re-index safely when required."""
    ensure_local_gbrain()
    if not enabled:
        _write_embedding_state(False)
        return
    if config is None or not config.get("dimensions"):
        raise ValueError("Test and select an embedding model before enabling embeddings.")

    executable = _gbrain_executable()
    if executable is None:
        raise RuntimeError("GBrain is not installed.")
    path = _config_path()
    current = json.loads(path.read_text(encoding="utf-8"))
    target = gbrain_target(config)
    dimensions = int(config["dimensions"])
    already_configured = (
        current.get("embedding_model") == target
        and int(current.get("embedding_dimensions") or 0) == dimensions
    )
    _write_embedding_state(True)
    if already_configured:
        return

    environment = os.environ.copy()
    environment["GBRAIN_HOME"] = str(knowledge_protocol.local_home_parent())
    environment.update(gbrain_provider_environment(config))
    try:
        process = subprocess.run(
            [
                executable,
                "migrate",
                "embeddings",
                "--to",
                target,
                "--dim",
                str(dimensions),
                "--yes",
                "--json",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=MIGRATION_TIMEOUT_SECONDS,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _write_embedding_state(False)
        raise RuntimeError(f"Could not configure GBrain embeddings: {exc}") from exc
    if process.returncode:
        _write_embedding_state(False)
        output = "\n".join(
            part.strip() for part in (process.stdout, process.stderr) if part.strip()
        )
        secret = str(config.get("api_key") or "")
        if secret:
            output = output.replace(secret, "***")
        raise RuntimeError(
            output[-3000:] or f"GBrain migration exited with code {process.returncode}."
        )
