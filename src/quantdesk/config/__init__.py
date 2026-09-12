"""Validated application configuration."""

from quantdesk.config.loader import ConfigError, load_config
from quantdesk.config.schema import AppConfig

__all__ = ["AppConfig", "ConfigError", "load_config"]
