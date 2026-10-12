"""Type-only structural contracts for the optional LLM-Wiki integration."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from .models import JsonObject, JsonValue

ExtensionMethod = Literal["GET", "POST"]
ExtensionScope = Literal["read", "write"]


@dataclass(frozen=True, slots=True)
class WorkbenchRequest:
    payload: JsonObject


@dataclass(frozen=True, slots=True)
class WorkbenchResponse:
    body: JsonValue


class ExtensionHandler(Protocol):
    def __call__(self, request: WorkbenchRequest, /) -> WorkbenchResponse: ...


@dataclass(frozen=True, slots=True)
class WorkbenchRoute:
    method: ExtensionMethod
    path: str
    required_scope: ExtensionScope
    workspace_id: Callable[[Mapping[str, tuple[str, ...]], JsonObject], str | None]
    handler: ExtensionHandler


class WorkbenchExtension(Protocol):
    extension_id: str
    routes: Sequence[WorkbenchRoute]
