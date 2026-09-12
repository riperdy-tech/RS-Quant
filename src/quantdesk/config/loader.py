from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError
from yaml import YAMLError

from quantdesk.config.schema import AppConfig


class ConfigError(ValueError):
    """A safe, user-actionable configuration failure."""


_EMBEDDED_SECRET_KEYS = {
    "api_key",
    "api_secret",
    "credential",
    "credentials",
    "password",
    "passphrase",
    "private_key",
    "secret",
    "token",
}


def _find_embedded_secret_key(value: object, path: tuple[str, ...] = ()) -> str | None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_text = str(key)
            child_path = (*path, key_text)
            if key_text.casefold() in _EMBEDDED_SECRET_KEYS:
                return ".".join(child_path)
            found = _find_embedded_secret_key(child, child_path)
            if found is not None:
                return found
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value):
            found = _find_embedded_secret_key(child, (*path, str(index)))
            if found is not None:
                return found
    return None


def _safe_validation_message(error: ValidationError) -> str:
    messages: list[str] = []
    for detail in error.errors(include_input=False, include_url=False):
        location = ".".join(str(part) for part in detail["loc"]) or "config"
        message = str(detail["msg"])
        if detail["type"] == "extra_forbidden":
            message = "unknown key is not permitted"
        messages.append(f"{location}: {message}")
    return "; ".join(messages)


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path)
    try:
        source = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read configuration file: {config_path}") from exc
    try:
        raw: Any = yaml.safe_load(source)
    except YAMLError as exc:
        raise ConfigError("invalid YAML syntax in configuration file") from exc
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise ConfigError("configuration root must be a mapping")
    secret_path = _find_embedded_secret_key(raw)
    if secret_path is not None:
        raise ConfigError(
            f"{secret_path}: embedded secrets are forbidden; use a secret reference"
        )
    try:
        return AppConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(_safe_validation_message(exc)) from exc
