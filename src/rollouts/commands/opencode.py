from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rollouts.errors import RolloutsError

OPENCODE_SCHEMA_URL = "https://opencode.ai/config.json"
PRIME_INTELLECT_PROVIDER = "prime-intellect"


@dataclass(frozen=True)
class OpenCodeConfigUpdateResult:
    config_path: Path
    model_ref: str
    model_id: str
    added_model: bool


def update_global_opencode_model(
    *,
    model_id: str,
    model_name: str,
    provider_name: str = PRIME_INTELLECT_PROVIDER,
) -> OpenCodeConfigUpdateResult:
    config_path = get_global_opencode_config_path()
    config = load_opencode_config(config_path=config_path)
    added_model = add_model_to_opencode_config(
        config=config,
        provider_name=provider_name,
        model_id=model_id,
        model_name=model_name,
    )
    model_ref = f"{provider_name}/{model_id}"
    config["model"] = model_ref
    write_opencode_config(config_path=config_path, config=config)
    return OpenCodeConfigUpdateResult(
        config_path=config_path,
        model_ref=model_ref,
        model_id=model_id,
        added_model=added_model,
    )


def get_global_opencode_config_path() -> Path:
    config_home = os.environ.get("XDG_CONFIG_HOME")
    if config_home:
        return Path(config_home).expanduser().resolve(strict=False) / "opencode" / "opencode.jsonc"
    return Path("~/.config/opencode/opencode.jsonc").expanduser().resolve(strict=False)


def load_opencode_config(*, config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        return {"$schema": OPENCODE_SCHEMA_URL}
    if not config_path.is_file():
        raise RolloutsError(f"OpenCode config path is not a file: {config_path}")

    try:
        raw_config = config_path.read_text(encoding="utf-8")
    except OSError as error:
        raise RolloutsError(f"failed to read OpenCode config: {config_path}") from error

    if not raw_config.strip():
        return {"$schema": OPENCODE_SCHEMA_URL}

    normalized_config = _strip_trailing_commas(_strip_jsonc_comments(raw_config))
    try:
        parsed = json.loads(normalized_config)
    except json.JSONDecodeError as error:
        raise RolloutsError(f"OpenCode config is not valid JSONC: {error}") from error
    if not isinstance(parsed, dict):
        raise RolloutsError("OpenCode config root must be an object")
    if "$schema" not in parsed:
        parsed["$schema"] = OPENCODE_SCHEMA_URL
    return parsed


def add_model_to_opencode_config(
    *,
    config: dict[str, Any],
    provider_name: str,
    model_id: str,
    model_name: str,
) -> bool:
    providers = config.setdefault("provider", {})
    if not isinstance(providers, dict):
        raise RolloutsError("OpenCode config field 'provider' must be an object")

    provider = providers.get(provider_name)
    if provider is None:
        raise RolloutsError(
            f"OpenCode config does not define provider {provider_name!r}; add it first"
        )
    if not isinstance(provider, dict):
        raise RolloutsError(f"OpenCode provider {provider_name!r} must be an object")

    models = provider.setdefault("models", {})
    if not isinstance(models, dict):
        raise RolloutsError(f"OpenCode provider {provider_name!r} field 'models' must be an object")

    existing_model = models.get(model_id)
    if existing_model is None:
        models[model_id] = {"name": model_name}
        return True
    if not isinstance(existing_model, dict):
        raise RolloutsError(
            f"OpenCode provider {provider_name!r} model entry {model_id!r} must be an object"
        )

    existing_model.setdefault("name", model_name)
    return False


def write_opencode_config(*, config_path: Path, config: dict[str, Any]) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        config_path.write_text(
            f"{json.dumps(config, indent=2, ensure_ascii=True)}\n",
            encoding="utf-8",
        )
    except OSError as error:
        raise RolloutsError(f"failed to write OpenCode config: {config_path}") from error


def _strip_jsonc_comments(raw_text: str) -> str:
    output: list[str] = []
    in_string = False
    in_line_comment = False
    in_block_comment = False
    escaped = False
    index = 0
    text_length = len(raw_text)

    while index < text_length:
        current = raw_text[index]
        next_char = raw_text[index + 1] if index + 1 < text_length else ""

        if in_line_comment:
            if current == "\n":
                in_line_comment = False
                output.append(current)
            index += 1
            continue

        if in_block_comment:
            if current == "*" and next_char == "/":
                in_block_comment = False
                index += 2
                continue
            if current == "\n":
                output.append(current)
            index += 1
            continue

        if in_string:
            output.append(current)
            if escaped:
                escaped = False
            elif current == "\\":
                escaped = True
            elif current == '"':
                in_string = False
            index += 1
            continue

        if current == '"':
            in_string = True
            output.append(current)
            index += 1
            continue

        if current == "/" and next_char == "/":
            in_line_comment = True
            index += 2
            continue

        if current == "/" and next_char == "*":
            in_block_comment = True
            index += 2
            continue

        output.append(current)
        index += 1

    return "".join(output)


def _strip_trailing_commas(raw_text: str) -> str:
    output: list[str] = []
    in_string = False
    escaped = False
    index = 0
    text_length = len(raw_text)

    while index < text_length:
        current = raw_text[index]

        if in_string:
            output.append(current)
            if escaped:
                escaped = False
            elif current == "\\":
                escaped = True
            elif current == '"':
                in_string = False
            index += 1
            continue

        if current == '"':
            in_string = True
            output.append(current)
            index += 1
            continue

        if current == ",":
            lookahead = index + 1
            while lookahead < text_length and raw_text[lookahead] in {" ", "\t", "\r", "\n"}:
                lookahead += 1
            if lookahead < text_length and raw_text[lookahead] in {"]", "}"}:
                index += 1
                continue

        output.append(current)
        index += 1

    return "".join(output)
