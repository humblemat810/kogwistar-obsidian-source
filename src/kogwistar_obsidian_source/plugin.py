"""Explicit LLM-Wiki Workbench extension for managed Obsidian vaults."""

from __future__ import annotations

from collections.abc import Mapping
from importlib import import_module
from typing import TYPE_CHECKING, Callable, cast

from .models import JsonObject

if TYPE_CHECKING:
    from .typing_contracts import (
        ExtensionHandler,
        ExtensionMethod,
        ExtensionScope,
        WorkbenchExtension,
        WorkbenchRequest,
        WorkbenchResponse,
        WorkbenchRoute,
    )


def _workspace_id(payload: Mapping[str, object]) -> str | None:
    value = payload.get("workspace_id")
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 256:
        return None
    return value.strip()


def _request_ids(payload: Mapping[str, object]) -> tuple[str, str]:
    workspace = _workspace_id(payload)
    vault_value = payload.get("vault_id")
    vault = vault_value.strip() if isinstance(vault_value, str) else ""
    if not workspace or not vault or len(vault) > 256:
        raise ValueError("workspace_id and vault_id must be non-empty bounded strings")
    return workspace, vault


def _host_method(host: object, name: str):
    method = getattr(host, name, None)
    if not callable(method):
        raise TypeError(f"enabled Obsidian source host lacks {name}()")
    return method


def build_workbench_extension(host: object) -> "WorkbenchExtension":
    """Build the allowlisted extension without importing LLM-Wiki at module load."""
    for method_name in ("export_snapshot", "scan_import", "plan_import", "apply_import", "list_conflicts"):
        _host_method(host, method_name)
    try:
        contracts = import_module("kogwistar_llm_wiki.app_contracts")
        RuntimeWorkbenchExtension = getattr(contracts, "WorkbenchExtension")
        RuntimeWorkbenchExtensionResponse = getattr(contracts, "WorkbenchExtensionResponse")
        RuntimeWorkbenchExtensionRoute = getattr(contracts, "WorkbenchExtensionRoute")
    except (ImportError, AttributeError) as exc:
        raise RuntimeError(
            "install the optional llm-wiki extra before enabling obsidian-source"
        ) from exc

    response = cast("Callable[[JsonObject], WorkbenchResponse]", RuntimeWorkbenchExtensionResponse)
    extension = cast("Callable[..., WorkbenchExtension]", RuntimeWorkbenchExtension)
    route_constructor = cast("Callable[..., WorkbenchRoute]", RuntimeWorkbenchExtensionRoute)

    def workspace_id(query: Mapping[str, tuple[str, ...]], payload: JsonObject) -> str | None:
        del query
        return _workspace_id(payload)

    def export(request: "WorkbenchRequest") -> "WorkbenchResponse":
        workspace, vault_id = _request_ids(request.payload)
        result = _host_method(host, "export_snapshot")(workspace, vault_id)
        return response(cast(JsonObject, result))

    def scan(request: "WorkbenchRequest") -> "WorkbenchResponse":
        workspace, vault_id = _request_ids(request.payload)
        result = _host_method(host, "scan_import")(workspace, vault_id, request.payload)
        return response(cast(JsonObject, result))

    def plan(request: "WorkbenchRequest") -> "WorkbenchResponse":
        workspace, vault_id = _request_ids(request.payload)
        result = _host_method(host, "plan_import")(workspace, vault_id, request.payload)
        return response(cast(JsonObject, result))

    def apply(request: "WorkbenchRequest") -> "WorkbenchResponse":
        workspace, vault_id = _request_ids(request.payload)
        plan_value = request.payload.get("plan_id")
        plan_id = plan_value.strip() if isinstance(plan_value, str) else ""
        deletion_mode = request.payload.get("deletion_mode", "ignore")
        if deletion_mode not in {"ignore", "tombstone"}:
            raise ValueError("deletion_mode must be ignore or tombstone")
        if not plan_id or len(plan_id) > 256:
            raise ValueError("plan_id must be a non-empty bounded string")
        result = _host_method(host, "apply_import")(
            workspace, vault_id, plan_id, deletion_mode
        )
        return response(cast(JsonObject, result))

    def conflicts(request: "WorkbenchRequest") -> "WorkbenchResponse":
        workspace, vault_id = _request_ids(request.payload)
        result = _host_method(host, "list_conflicts")(workspace, vault_id)
        return response(cast(JsonObject, result))

    def route(
        method: str,
        path: str,
        scope: str,
        handler: object,
    ) -> "WorkbenchRoute":
        return route_constructor(
            method=cast("ExtensionMethod", method),
            path=path,
            required_scope=cast("ExtensionScope", scope),
            workspace_id=workspace_id,
            handler=cast("ExtensionHandler", handler),
        )

    return extension(
        extension_id="obsidian-source",
        routes=(
            route("POST", "/plugins/obsidian-source/export", "read", export),
            route("POST", "/plugins/obsidian-source/scan", "read", scan),
            route("POST", "/plugins/obsidian-source/plan-import", "read", plan),
            route("POST", "/plugins/obsidian-source/apply-import", "write", apply),
            route("POST", "/plugins/obsidian-source/conflicts", "read", conflicts),
        ),
    )

