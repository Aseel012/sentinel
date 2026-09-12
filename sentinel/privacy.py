"""Small, dependency-free safeguards for bounded locally retained text."""

from __future__ import annotations

import re

_SECRET_NAME = r"token|password|passwd|secret|api[_-]?key|authorization"
_SECRET_ASSIGNMENT = re.compile(rf"(?i)({_SECRET_NAME})(=|:)([^\s]+)")
_SECRET_ARGUMENT = re.compile(rf"(?i)(--?(?:{_SECRET_NAME})\b)(\s+)([^\s]+)")
_SHORT_SECRET_ARGUMENT = re.compile(r"(?i)(^|\s)(-[pk])(\s+)([^\s]+)")
_SECRET_QUERY = re.compile(r"(?i)([?&](?:" + _SECRET_NAME + r")=)([^&\s]+)")


def redact_sensitive_text(value: str, *, max_length: int) -> str:
    """Redact common inline credential forms before applying a character bound."""
    if max_length < 1:
        raise ValueError("max_length must be positive")
    value = _SECRET_ASSIGNMENT.sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", value)
    value = _SECRET_ARGUMENT.sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", value)
    value = _SHORT_SECRET_ARGUMENT.sub(
        lambda match: f"{match.group(1)}{match.group(2)}{match.group(3)}[REDACTED]", value
    )
    return _SECRET_QUERY.sub(lambda match: f"{match.group(1)}[REDACTED]", value)[:max_length]
