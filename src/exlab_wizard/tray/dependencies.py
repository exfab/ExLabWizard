"""Production :class:`AppDependencies` factory. Backend Spec §4.5, §4.6.

The tray entry point constructs every component the FastAPI surface and
the NiceGUI wizard need at runtime, packs them into an
:class:`AppDependencies`, and hands the bundle to ``create_app``. Each
component is built in its own try/except so a single broken collaborator
(absent LIMS endpoint, missing template directory, NAS DB unreachable)
degrades to a structured 503 / "unavailable" banner instead of crashing
the tray. The pattern mirrors the §4.5 lifespan contract ("best-effort;
failure logs WARN").

The order matters: validator depends on the cache writers, controller
composes validator + plugin host + template engine + cache writers,
staging watcher depends on the ingest writer + a NAS-sync stub. We
construct upstream pieces first and pass them into downstream
constructors; any upstream failure short-circuits the chain so a None
upstream produces a None downstream rather than a partially-constructed
object.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

from exlab_wizard.api.app import AppDependencies
from exlab_wizard.config.loader import load_config, save_config
from exlab_wizard.constants import KEYRING_USERNAME_LIMS
from exlab_wizard.logging import get_logger
from exlab_wizard.paths import os_config_path
from exlab_wizard.tray.autostart import AutostartManager

__all__ = ["build_production_dependencies"]

_log = get_logger(__name__)


def build_production_dependencies(state_dir: Path) -> AppDependencies:
    """Return a populated :class:`AppDependencies` for the live tray.

    Every per-component construction is wrapped in best-effort
    error handling: on failure the field is left ``None`` and a WARN
    is logged. The API surface already understands ``None`` dependencies
    (it returns a structured 503 from ``require_*`` helpers), and the
    NiceGUI mount helper wraps each page handler's dep access in the
    same try/except so the GUI degrades to an "unavailable" banner.
    """
    deps = AppDependencies()

    deps.config = _try("config", _load_config_safely)
    # Wire the saver unconditionally: a fresh install has no config.yaml
    # yet, but the settings wizard must be able to *create* one. The
    # saver handles the missing-file case (no original text to preserve).
    deps.save_config = _make_save_config()

    validator = _try("validator", _build_validator, deps.config)
    deps.validator = validator

    deps.session_store = _try("session_store", _build_session_store)
    deps.cache_creation = _try("cache_creation", _build_creation_writer)
    cache_equipment = _try("cache_equipment", _build_equipment_writer)
    template_engine = _try("template_engine", _build_template_engine)
    deps.plugin_host = _try("plugin_host", _build_plugin_host, deps.config)
    deps.ingest_writer = _try("ingest_writer", _build_ingest_writer)

    deps.controller = _try(
        "controller",
        _build_controller,
        config=deps.config,
        validator=validator,
        template_engine=template_engine,
        plugin_host=deps.plugin_host,
        cache_creation=deps.cache_creation,
        cache_equipment=cache_equipment,
        session_store=deps.session_store,
    )

    keyring_store = _try("keyring_store", _build_keyring_store, state_dir)
    # Expose the store so the settings dialog can persist the LIMS
    # password to the OS keyring at click time (Frontend Spec §7.4.1).
    deps.keyring_store = keyring_store
    deps.keyring_password_present = (
        _try(
            "keyring_password_check",
            _check_keyring_present,
            keyring_store,
            deps.config,
        )
        or False
    )

    deps.lims_client = _try("lims_client", _build_lims_client, deps.config, keyring_store)
    deps.lims_reachable = True
    deps.lims_probe = _make_lims_probe(deps)

    deps.nas_sync = _try("nas_sync", _build_nas_sync, deps.config, state_dir)
    deps.nas_sync_snapshot = _make_nas_sync_snapshot(deps)

    deps.staging_watcher = _try(
        "staging_watcher",
        _build_staging_watcher,
        config=deps.config,
        ingest_writer=deps.ingest_writer,
        nas_sync=deps.nas_sync,
        cache_creation=deps.cache_creation,
    )

    deps.autostart_toggle = _make_autostart_toggle()
    deps.equipment_probe = None

    deps.session_store_snapshot = _make_session_store_snapshot(deps)

    if deps.plugin_host is not None:
        registry = getattr(deps.plugin_host, "_registry", None)
        records = getattr(registry, "_records", None)
        deps.registered_plugin_count = len(records) if isinstance(records, dict) else 0
        deps.plugin_host_status = "ok"
    else:
        deps.plugin_host_status = "unavailable"

    return deps


def _try(label: str, fn: Any, /, *args: Any, **kwargs: Any) -> Any:
    """Run ``fn(*args, **kwargs)`` swallowing exceptions with a WARN log."""
    try:
        return fn(*args, **kwargs)
    except Exception as exc:
        _log.warning("dependency unavailable [component=%s] %s: %s", label, type(exc).__name__, exc)
        return None


def _load_config_safely() -> Any:
    path = os_config_path()
    if not path.exists():
        _log.info("config.yaml not found at %s; setup wizard will run", path)
        return None
    return load_config(path)


def _make_save_config() -> Any:
    path = os_config_path()

    def _save(cfg: Any) -> None:
        original = path.read_text(encoding="utf-8") if path.exists() else None
        save_config(path, cfg, original_text=original)

    return _save


def _build_validator(config: Any) -> Any:
    from exlab_wizard.validator.engine import Validator

    validator_config = getattr(config, "validator", None) if config is not None else None
    equipment_roots: dict[str, Path] = {}
    if config is not None:
        local_root = Path(config.paths.local_root) if config.paths.local_root else None
        if local_root is not None:
            for eq in config.equipment:
                equipment_roots[eq.id] = local_root / eq.id
    # Redesign §3.1: the orchestrator pipeline is always active, so the
    # validator's staging-root awareness keys on whether ``staging_root``
    # is set rather than a removed ``enabled`` toggle.
    staging_root = (
        Path(config.orchestrator.staging_root)
        if config is not None and config.orchestrator.staging_root
        else None
    )
    return Validator(
        validator_config,
        equipment_roots=equipment_roots,
        staging_root=staging_root,
    )


def _build_session_store() -> Any:
    from exlab_wizard.controller.session_store import SessionStore

    return SessionStore()


def _build_creation_writer() -> Any:
    from exlab_wizard.cache.creation_writer import CreationWriter

    return CreationWriter()


def _build_equipment_writer() -> Any:
    from exlab_wizard.cache.equipment import EquipmentCacheWriter

    return EquipmentCacheWriter()


def _build_ingest_writer() -> Any:
    from exlab_wizard.cache.ingest_writer import IngestWriter

    return IngestWriter()


def _build_template_engine() -> Any:
    from exlab_wizard.template.copier_driver import TemplateEngine

    return TemplateEngine()


def _build_plugin_host(config: Any) -> Any:
    from exlab_wizard.plugins.host import PluginHost, PluginRecord
    from exlab_wizard.plugins.registry import PluginRegistry

    plugin_dir = (
        Path(config.paths.plugin_dir) if config is not None and config.paths.plugin_dir else None
    )
    registry = PluginRegistry(bundled_dir=None, lab_dir=plugin_dir)
    report = registry.reload()
    if getattr(report, "rejected", None):
        _log.info("plugin registry rejected %d entries", len(report.rejected))

    class _RegistryAdapter:
        def __init__(self, inner: PluginRegistry) -> None:
            self._inner = inner

        def get_record(self, name: str) -> PluginRecord | None:
            return self._inner.get(name)  # type: ignore[return-value]

    return PluginHost(_RegistryAdapter(registry))


def _build_controller(
    *,
    config: Any,
    validator: Any,
    template_engine: Any,
    plugin_host: Any,
    cache_creation: Any,
    cache_equipment: Any,
    session_store: Any,
) -> Any:
    if config is None or validator is None or template_engine is None or cache_creation is None:
        msg = "controller requires config + validator + template_engine + cache_creation"
        raise RuntimeError(msg)
    from exlab_wizard.controller.creation import CreationController

    return CreationController(
        config=config,
        validator=validator,
        template_engine=template_engine,
        plugin_host=plugin_host,
        cache_creation=cache_creation,
        cache_equipment=cache_equipment,
        session_store=session_store,
    )


def _build_keyring_store(state_dir: Path) -> Any:
    from exlab_wizard.lims.keyring_store import KeyringStore

    return KeyringStore(state_dir=state_dir)


def _lims_keyring_password(keyring_store: Any) -> str | None:
    """Return the stored LIMS password, or ``None`` if absent/unavailable.

    The credential lives under ``(KEYRING_SERVICE, KEYRING_USERNAME_LIMS)``
    -- the same ``(service, username)`` pair the settings dialog's
    credential field writes to (Frontend Spec §7.4.1, Backend Spec §7.4).
    ``KeyringStore.get_password`` is keyword-only; any backend error
    degrades to ``None`` so the caller treats the password as
    not-yet-configured rather than crashing.
    """
    if keyring_store is None:
        return None
    getter = getattr(keyring_store, "get_password", None)
    if getter is None:
        return None
    with contextlib.suppress(Exception):
        return getter(username=KEYRING_USERNAME_LIMS)
    return None


def _check_keyring_present(keyring_store: Any, config: Any) -> bool:
    if keyring_store is None or config is None:
        return False
    # An unconfigured LIMS email means the slot is not set up yet, so a
    # stray keyring entry should not count as "password present".
    if not (getattr(config.lims, "email", "") or ""):
        return False
    return bool(_lims_keyring_password(keyring_store))


def _build_lims_client(config: Any, keyring_store: Any) -> Any:
    if config is None or not config.lims.endpoint or not config.lims.email:
        msg = "LIMS endpoint or email not configured"
        raise RuntimeError(msg)
    from exlab_wizard.lims.client import LIMSClient

    email = config.lims.email

    def _provider() -> str | None:
        return _lims_keyring_password(keyring_store)

    return LIMSClient(
        endpoint=config.lims.endpoint,
        email=email,
        keyring_password_provider=_provider,
    )


def _make_lims_probe(deps: AppDependencies) -> Any:
    async def _probe(_body: Any = None) -> dict[str, Any]:
        del _body
        client = deps.lims_client
        if client is None:
            return {"ok": False, "reason": "LIMS not configured"}
        try:
            await client.login()
        except Exception as exc:
            deps.lims_reachable = False
            return {"ok": False, "reason": str(exc)}
        deps.lims_reachable = True
        return {"ok": True}

    return _probe


def _build_nas_sync(config: Any, state_dir: Path) -> Any:
    if config is None:
        msg = "NAS sync requires a loaded config"
        raise RuntimeError(msg)
    from exlab_wizard.sync.queue import SyncQueue

    db_path = state_dir / "sync_queue.sqlite"
    return SyncQueue(db_path)


def _make_nas_sync_snapshot(deps: AppDependencies) -> Any:
    def _snapshot() -> dict[str, Any]:
        sync = deps.nas_sync
        if sync is None:
            return {"status": "unavailable", "queue_depth": 0, "in_flight": 0}
        depth = getattr(sync, "queue_depth", 0)
        in_flight = getattr(sync, "in_flight", 0)
        return {"status": "ok", "queue_depth": int(depth), "in_flight": int(in_flight)}

    return _snapshot


def _build_staging_watcher(
    *,
    config: Any,
    ingest_writer: Any,
    nas_sync: Any,
    cache_creation: Any,
) -> Any:
    # Redesign §3.1: the staging watcher boots whenever staging_root is
    # configured; the legacy enabled toggle is gone.
    if config is None or not config.orchestrator.staging_root:
        return None
    if ingest_writer is None or nas_sync is None or cache_creation is None:
        msg = "staging watcher requires ingest_writer + nas_sync + cache_creation"
        raise RuntimeError(msg)
    from exlab_wizard.orchestrator.staging_watcher import StagingWatcher

    return StagingWatcher(
        config=config,
        ingest_writer=ingest_writer,
        nas_sync=nas_sync,
        cache_creation=cache_creation,
    )


def _make_autostart_toggle() -> Any:
    def _toggle(enabled: bool) -> bool:
        manager = AutostartManager()
        if enabled:
            manager.register()
        else:
            manager.unregister()
        return manager.is_registered()

    return _toggle


def _make_session_store_snapshot(deps: AppDependencies) -> Any:
    def _snapshot() -> dict[str, Any]:
        store = deps.session_store
        if store is None:
            return {"status": "unavailable", "active_sessions": 0, "input_required": 0}
        sessions_attr = getattr(store, "_sessions", {})
        active = sum(
            1 for s in sessions_attr.values() if not getattr(s, "is_terminal", lambda: False)()
        )
        input_required = sum(
            1
            for s in sessions_attr.values()
            if getattr(getattr(s, "state", None), "name", "") == "INPUT_REQUIRED"
        )
        return {
            "status": "ok",
            "active_sessions": active,
            "input_required": input_required,
        }

    return _snapshot
