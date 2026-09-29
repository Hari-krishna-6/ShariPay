from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DrunixInvocationResult:
    function: str
    arguments: tuple[str, ...]
    response: Any | None = None
