"""Tests for the live config-reload coordinator (no tray relaunch).

``exlab_wizard.tray.dependencies.apply_live_config`` pushes a freshly
saved ``config.yaml`` into the already-running components instead of
forcing a tray restart. These tests cover both regimes -- reconfigure
in place (steady state) and build-on-first-save (fresh install) -- plus
the rebuild-gating for the LIMS client and plugin host and the
per-component failure isolation.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

# Prime the api package first (import-order note mirrors test_mount.py).
import exlab_wizard.api.app  # noqa: F401  -- import order matters
from exlab_wizard.api.app import AppDependencies
from exlab_wizard.config.models import (
    Config,
    EquipmentConfig,
    LIMSConfig,
    LoggingConfig,
    PathsConfig,
)
from exlab_wizard.tray import dependencies as deps_mod
from exlab_wizard.tray.dependencies import apply_live_config

# ---------------------------------------------------------------------------
# Fixtures / stubs
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _stub_configure_logging(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Stub the real logging reconfigure so tests don't churn global handlers.

    Returns the list of configs it was called with, so a test can assert
    the live reload reconfigures logging.
    """
    calls: list[Any] = []
    monkeypatch.setattr(
        "exlab_wizard.logging.manager.configure_logging",
        lambda cfg=None: calls.append(cfg),
    )
    return calls


def _equipment(eq_id: str = "EQ1") -> EquipmentConfig:
    return EquipmentConfig(
        id=eq_id,
        label=f"Equipment {eq_id}",
        nas_root="/nas",
    )


def _config(
    *,
    app_root: str = "/srv/exlab",
    endpoint: str = "https://lims.example",
    email: str = "op@example",
    log_level: str = "INFO",
    equipment: tuple[EquipmentConfig, ...] | None = None,
) -> Config:
    # plugin_dir / templates_dir / data_root are all derived from app_root, so
    # a plugin-dir change is driven by pointing app_root at a different root.
    return Config(
        paths=PathsConfig(app_root=app_root),
        lims=LIMSConfig(endpoint=endpoint, email=email),
        logging=LoggingConfig(level=log_level),
        equipment=list(equipment) if equipment is not None else [_equipment()],
    )


class _RecordingComponent:
    """Stub controller / nas_sync / poller recording ``apply_config`` calls."""

    def __init__(self) -> None:
        self.applied: list[Any] = []
        self.plugin_hosts: list[Any] = []

    def apply_config(self, config: Any, *, plugin_host: Any = None) -> None:
        self.applied.append(config)
        self.plugin_hosts.append(plugin_host)


class _RecordingValidator:
    def __init__(self) -> None:
        self.reconfigured: list[tuple[Any, Any, Any]] = []

    def reconfigure(self, vc: Any, *, equipment_roots: Any, staging_root: Any) -> None:
        self.reconfigured.append((vc, equipment_roots, staging_root))


class _AsyncStub:
    """Fresh-built nas_sync / poller stub with async init / start."""

    def __init__(self) -> None:
        self.inited = False
        self.started = False

    async def init(self) -> None:
        self.inited = True

    async def start(self) -> None:
        self.started = True


def _running_deps() -> AppDependencies:
    """Build a deps bundle whose config-dependent components already exist."""
    deps = AppDependencies()
    deps.config = _config()
    deps.validator = _RecordingValidator()
    deps.controller = _RecordingComponent()
    deps.nas_sync = _RecordingComponent()
    deps.quiescence_poller = _RecordingComponent()
    return deps


# ---------------------------------------------------------------------------
# Reconfigure regime (steady state)
# ---------------------------------------------------------------------------


def test_reconfigures_existing_components_in_place(_stub_configure_logging: list[Any]) -> None:
    deps = _running_deps()  # old config logs at INFO
    new = _config(log_level="DEBUG", equipment=(_equipment("EQ1"), _equipment("EQ2")))

    apply_live_config(deps, new)

    assert deps.config is new  # canonical config swapped
    assert _stub_configure_logging == [new.logging]  # logging reconfigured (level changed)
    assert deps.validator.reconfigured  # validator reconfigured in place
    assert deps.controller.applied == [new]
    assert deps.nas_sync.applied == [new]
    assert deps.quiescence_poller.applied == [new]


def test_skips_logging_reconfigure_when_unchanged(_stub_configure_logging: list[Any]) -> None:
    deps = _running_deps()  # old config logs at INFO
    apply_live_config(deps, _config())  # same INFO level
    assert _stub_configure_logging == []


def test_component_failure_is_isolated(_stub_configure_logging: list[Any]) -> None:
    """One component raising must not abort the rest, and config still lands."""

    class _Boom(_RecordingComponent):
        def apply_config(self, config: Any, *, plugin_host: Any = None) -> None:
            msg = "boom"
            raise RuntimeError(msg)

    deps = _running_deps()
    deps.controller = _Boom()  # raises mid-reload
    new = _config(equipment=(_equipment("EQ9"),))

    apply_live_config(deps, new)  # must not raise

    # Components after the failing one are still reconfigured, and the
    # canonical config is assigned.
    assert deps.nas_sync.applied == [new]
    assert deps.quiescence_poller.applied == [new]
    assert deps.config is new


# ---------------------------------------------------------------------------
# Rebuild gating: LIMS client + plugin host
# ---------------------------------------------------------------------------


def test_rebuilds_lims_only_on_endpoint_change(monkeypatch: pytest.MonkeyPatch) -> None:
    built: list[Any] = []
    monkeypatch.setattr(deps_mod, "_build_lims_client", lambda cfg, ks: built.append(cfg) or "LIMS")
    deps = _running_deps()
    deps.config = _config(endpoint="https://old.example")
    deps.lims_client = None

    # Endpoint changes -> rebuild.
    new = _config(endpoint="https://new.example")
    apply_live_config(deps, new)
    assert built == [new]
    assert deps.lims_client == "LIMS"

    # Same endpoint -> no rebuild.
    built.clear()
    apply_live_config(deps, _config(endpoint="https://new.example"))
    assert built == []


def test_rebuilds_plugin_host_only_on_dir_change(monkeypatch: pytest.MonkeyPatch) -> None:
    built: list[Any] = []
    monkeypatch.setattr(deps_mod, "_build_plugin_host", lambda cfg: built.append(cfg) or "HOST")
    deps = _running_deps()
    deps.config = _config(app_root="/srv/old")

    # plugin_dir (derived from app_root) changes -> rebuild + re-inject into
    # the controller.
    new = _config(app_root="/srv/new")
    apply_live_config(deps, new)
    assert built == [new]
    assert deps.plugin_host == "HOST"
    assert deps.controller.plugin_hosts == ["HOST"]

    # Unchanged app_root -> derived plugin_dir unchanged -> no rebuild,
    # controller keeps its host.
    built.clear()
    apply_live_config(deps, _config(app_root="/srv/new"))
    assert built == []
    assert deps.controller.plugin_hosts[-1] is None


# ---------------------------------------------------------------------------
# Fresh-build regime (first-install save)
# ---------------------------------------------------------------------------


async def test_builds_and_starts_components_on_fresh_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On a first save the absent components are built and their async
    bring-up is scheduled on the running event loop -- no relaunch."""
    nas = _AsyncStub()
    poller = _AsyncStub()
    monkeypatch.setattr(deps_mod, "_build_nas_sync", lambda *a, **k: nas)
    monkeypatch.setattr(deps_mod, "_build_quiescence_poller", lambda **k: poller)
    monkeypatch.setattr(deps_mod, "_build_controller", lambda **k: "CTRL")
    monkeypatch.setattr(deps_mod, "_build_validator", lambda cfg: "VALIDATOR")
    monkeypatch.setattr(deps_mod, "_build_plugin_host", lambda cfg: "HOST")
    monkeypatch.setattr(deps_mod, "_build_lims_client", lambda cfg, ks: "LIMS")

    deps = AppDependencies()  # fresh install: config is None, components absent
    deps.state_dir = Path("/tmp/state")
    new = _config()

    apply_live_config(deps, new)

    # The components were constructed synchronously...
    assert deps.validator == "VALIDATOR"
    assert deps.controller == "CTRL"
    assert deps.nas_sync is nas
    assert deps.quiescence_poller is poller
    assert deps.lims_client == "LIMS"
    assert deps.config is new

    # ...and their async bring-up was scheduled on the loop.
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert nas.inited is True
    assert poller.started is True
