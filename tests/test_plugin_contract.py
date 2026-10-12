from __future__ import annotations

import sys
from dataclasses import dataclass
from types import ModuleType

import pytest

from kogwistar_obsidian_source.plugin import build_workbench_extension
from kogwistar_obsidian_source.typing_contracts import WorkbenchRequest


@pytest.fixture
def fake_workbench_contracts(monkeypatch: pytest.MonkeyPatch) -> None:
    @dataclass(frozen=True)
    class Response:
        body: object

    @dataclass(frozen=True)
    class Route:
        method: str
        path: str
        required_scope: str
        workspace_id: object
        handler: object

    @dataclass(frozen=True)
    class Extension:
        extension_id: str
        routes: tuple[Route, ...]

    contracts = ModuleType("kogwistar_llm_wiki.app_contracts")
    setattr(contracts, "WorkbenchExtension", Extension)
    setattr(contracts, "WorkbenchExtensionResponse", Response)
    setattr(contracts, "WorkbenchExtensionRoute", Route)
    monkeypatch.setitem(sys.modules, "kogwistar_llm_wiki.app_contracts", contracts)


class _Host:
    def export_snapshot(self, workspace_id: str, vault_id: str) -> dict[str, object]:
        return {"operation": "export", "workspace_id": workspace_id, "vault_id": vault_id}

    def scan_import(self, workspace_id: str, vault_id: str, payload: dict[str, object]) -> dict[str, object]:
        return {"operation": "scan", "workspace_id": workspace_id, "vault_id": vault_id}

    def plan_import(self, workspace_id: str, vault_id: str, payload: dict[str, object]) -> dict[str, object]:
        return {"operation": "plan", "workspace_id": workspace_id, "vault_id": vault_id}

    def apply_import(self, workspace_id: str, vault_id: str, plan_id: str, deletion_mode: str) -> dict[str, object]:
        return {"operation": "apply", "plan_id": plan_id, "deletion_mode": deletion_mode}

    def list_conflicts(self, workspace_id: str, vault_id: str) -> dict[str, object]:
        return {"operation": "conflicts", "workspace_id": workspace_id, "vault_id": vault_id}


def test_extension_is_namespaced_and_apply_is_write_scoped(fake_workbench_contracts: None) -> None:
    extension = build_workbench_extension(_Host())
    routes = {(route.method, route.path): route for route in extension.routes}

    assert all(path.startswith("/plugins/obsidian-source/") for _method, path in routes)
    assert routes[("POST", "/plugins/obsidian-source/apply-import")].required_scope == "write"
    assert routes[("POST", "/plugins/obsidian-source/scan")].required_scope == "read"


def test_apply_requires_plan_identity(fake_workbench_contracts: None) -> None:
    extension = build_workbench_extension(_Host())
    apply_route = next(route for route in extension.routes if route.path.endswith("/apply-import"))
    with pytest.raises(ValueError, match="plan_id"):
        apply_route.handler(
            WorkbenchRequest(
                payload={"workspace_id": "w", "vault_id": "v"},
            )
        )


def test_extension_rejects_unbound_host(fake_workbench_contracts: None) -> None:
    with pytest.raises(TypeError, match="export_snapshot"):
        build_workbench_extension(object())

