"""Shared validation primitives."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class ValidationError(Exception):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)
