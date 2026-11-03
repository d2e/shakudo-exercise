"""Shared model configuration."""
from pydantic import ConfigDict

STRICT = ConfigDict(strict=True, extra="forbid")  # strict types, unknown fields rejected (V4, V5)
