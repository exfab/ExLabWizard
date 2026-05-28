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
composes validator + plugin host + template engine + cache writers, the
quiescence poller depends on the NAS-sync queue. We construct upstream
pieces first and pass them into downstream constructors; any upstream
failure short-circuits the chain so a None upstream produces a None
downstream rather than a partially-constructed object.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
from typing import Any

from exlab_wizard.api.app import AppDependencies
from exlab_wizard.config.loader import load_config, save_config
from exlab_wizard.constants import KEYRING_USERNAME_LIMS, SyncMode
from exlab_wizard.constants.keyring import keyring_nas_username
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

    # ``sync_state_writer`` must precede ``nas_sync`` -- the NASSyncClient
    # takes it as a constructor dependency for per-file verify
    # reconciliation (operator-free per-file NAS sync, 2026-05-21).
    deps.sync_state_writer = _try("sync_state_writer", _build_sync_state_writer)

    deps.nas_sync = _try(
        "nas_sync",
        _build_nas_sync,
        deps.config,
        state_dir,
        validator,
        deps.cache_creation,
        deps.sync_state_writer,
        keyring_store,
    )
    deps.nas_sync_snapshot = _make_nas_sync_snapshot(deps)

    deps.quiescence_poller = _try(
        "quiescence_poller",
        _build_quiescence_poller,
        config=deps.config,
        nas_sync=deps.nas_sync,
        sync_state_writer=deps.sync_state_writer,
    )

    deps.autostart_toggle = _make_autostart_toggle()

    # Rclone-only NAS sync migration (2026-05-26). The per-equipment NAS
    # password-presence set drives the §4.9 setup gate and the Settings
    # credential field's "Set / Not set" badge. Hydrated once at tray
    # boot; the Settings handlers mutate it on Save / Clear so the gate
    # flips without waiting for a relaunch.
    deps.nas_password_present = (
        _try(
            "nas_password_check",
            _check_nas_passwords_present,
            keyring_store,
            deps.config,
        )
        or set()
    )
    deps.equipment_probe = _make_equipment_probe(deps)

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


# Env var supplying the master passphrase for the encrypted-at-rest
# secret store. Read only when the OS keyring is unavailable; never
# committed -- callers export it at launch on keyring-less hosts.
_SECRET_PASSPHRASE_ENV = "EXLAB_WIZARD_SECRET_PASSPHRASE"


def _build_keyring_store(state_dir: Path) -> Any:
    """Build the LIMS/NAS secret store with an optional fallback passphrase.

    When the OS keyring backend is unavailable, :class:`KeyringStore`
    falls back to an encrypted-at-rest file keyed by a master passphrase
    (Backend Spec §7.4.4). That passphrase is read from
    ``EXLAB_WIZARD_SECRET_PASSPHRASE``; when the variable is unset no
    provider is wired and the fallback stays disabled -- the historical
    behaviour, so keyring-equipped hosts are unaffected.
    """
    from exlab_wizard.lims.keyring_store import KeyringStore

    passphrase = os.environ.get(_SECRET_PASSPHRASE_ENV)
    provider = (lambda: passphrase) if passphrase else None
    return KeyringStore(state_dir=state_dir, passphrase_provider=provider)


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


def _nas_keyring_password(keyring_store: Any, equipment_id: str) -> str | None:
    """Return the stored NAS password for ``equipment_id`` or ``None``.

    The credential lives under ``(KEYRING_SERVICE,
    keyring_nas_username(equipment_id))``. ``KeyringStore.get_password``
    is keyword-only; any backend error or absent entry degrades to
    ``None`` so callers treat the password as not-yet-configured rather
    than crashing.
    """
    if keyring_store is None:
        return None
    getter = getattr(keyring_store, "get_password", None)
    if getter is None:
        return None
    with contextlib.suppress(Exception):
        return getter(username=keyring_nas_username(equipment_id))
    return None


def _check_nas_passwords_present(keyring_store: Any, config: Any) -> set[str]:
    """Return the set of nas-mode equipment ids that have a keyring entry.

    Rclone-only NAS sync migration (2026-05-26). Iterates the
    nas-mode equipment whose transport requires a keyring-stored
    password, returning the subset whose entry is populated. The
    output is the hydrated form of ``deps.nas_password_present`` and
    feeds straight into :func:`paths.evaluate_setup_state`.
    """
    if keyring_store is None or config is None:
        return set()
    from exlab_wizard.config.models import transport_requires_keyring_password

    out: set[str] = set()
    for eq in getattr(config, "equipment", ()) or ():
        if eq.sync_mode != SyncMode.NAS:
            continue
        if not transport_requires_keyring_password(eq.transport):
            continue
        if _nas_keyring_password(keyring_store, eq.id):
            out.add(eq.id)
    return out


def _make_equipment_probe(deps: AppDependencies) -> Any:
    """Build the ``deps.equipment_probe`` callable.

    Rclone-only NAS sync migration (2026-05-26). The probe takes the
    already-resolved :class:`EquipmentConfig` (the
    ``POST /setup/test-equipment`` endpoint hands it in), reads the
    keyring password under :func:`keyring_nas_username`, runs
    ``rclone obscure -``, builds the per-backend env, and calls
    :meth:`RcloneDriver.about`. Returns the canonical
    ``{"ok", "reason", "latency_ms"}`` dict the endpoint surfaces.

    A missing keyring entry short-circuits with
    ``ok=False, reason="password not set in keyring"`` -- the probe
    never spawns rclone in that case so the operator sees the gate
    reason rather than an opaque rclone auth error.
    """

    async def _probe(equipment: Any) -> dict[str, Any]:
        if equipment is None:
            return {"ok": False, "reason": "no matching equipment configuration"}
        keyring_store = getattr(deps, "keyring_store", None)
        if keyring_store is None:
            return {"ok": False, "reason": "OS keyring is unavailable"}
        password = _nas_keyring_password(keyring_store, equipment.id)
        if not password:
            return {"ok": False, "reason": "password not set in keyring"}
        # Local imports keep ``tray.dependencies`` cheap to load (the
        # rclone driver pulls in subprocess + asyncio plumbing the tray
        # otherwise wouldn't need until first sync).
        from exlab_wizard.sync.nas_client import _remote_name_for
        from exlab_wizard.sync.transports.rclone import (
            RcloneDriver,
            build_rclone_env,
            obscure,
            pass_env_keys_for,
        )

        try:
            obscured = await obscure(password)
        except Exception as exc:
            return {"ok": False, "reason": f"rclone obscure failed: {exc}"}
        remote_name = _remote_name_for(equipment)
        env = build_rclone_env(
            transport=equipment.transport,
            password_obscured=obscured,
            remote_name=remote_name,
        )
        mask_for_log = pass_env_keys_for(remote_name)
        driver = RcloneDriver()
        import time

        started = time.monotonic()
        try:
            about = await driver.about(remote_name, env=env, mask_for_log=mask_for_log)
        except Exception as exc:
            return {"ok": False, "reason": str(exc)}
        latency_ms = int((time.monotonic() - started) * 1000)
        if not about.ok:
            return {"ok": False, "reason": about.reason, "latency_ms": latency_ms}
        return {"ok": True, "reason": None, "latency_ms": latency_ms}

    return _probe


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


def _build_nas_sync(
    config: Any,
    state_dir: Path,
    validator: Any,
    cache_creation: Any,
    sync_state_writer: Any,
    keyring_store: Any,
) -> Any:
    """Build the :class:`NASSyncClient` -- the public NAS-sync surface.

    The client wires the durable queue, the transport drivers, the
    verifier, the Pre-Sync Gate, the ``sync_state.json`` writer used
    for per-file verify reconciliation, and (rclone-only migration,
    2026-05-26) the keyring store used to resolve per-equipment NAS
    passwords at push time.

    The poller and the force-sync route call ``enqueue`` / ``status`` on
    this object; returning a bare ``SyncQueue`` (which has neither) would
    leave the poller sweep silently dead.
    """
    if config is None:
        msg = "NAS sync requires a loaded config"
        raise RuntimeError(msg)
    if validator is None:
        msg = "NAS sync requires a validator"
        raise RuntimeError(msg)
    if cache_creation is None:
        msg = "NAS sync requires a creation-cache writer"
        raise RuntimeError(msg)
    from exlab_wizard.sync.nas_client import NASSyncClient

    db_path = state_dir / "sync_queue.sqlite"
    return NASSyncClient(
        config=config,
        queue_db=db_path,
        validator=validator,
        cache_creation=cache_creation,
        sync_state_writer=sync_state_writer,
        keyring_store=keyring_store,
    )


def _make_nas_sync_snapshot(deps: AppDependencies) -> Any:
    def _snapshot() -> dict[str, Any]:
        sync = deps.nas_sync
        if sync is None:
            return {"status": "unavailable", "queue_depth": 0, "in_flight": 0}
        depth = getattr(sync, "queue_depth", 0)
        in_flight = getattr(sync, "in_flight", 0)
        return {"status": "ok", "queue_depth": int(depth), "in_flight": int(in_flight)}

    return _snapshot


def _build_sync_state_writer() -> Any:
    """Build the orchestrator-only ``sync_state.json`` writer.

    Operator-free per-file NAS sync design (2026-05-21): the quiescence
    poller reads ``sync_state.json`` to skip already-synced files and the
    NAS-sync client writes per-file verify reconciliation into it.
    """
    from exlab_wizard.cache.sync_state_writer import SyncStateWriter

    return SyncStateWriter()


def _build_quiescence_poller(
    *,
    config: Any,
    nas_sync: Any,
    sync_state_writer: Any,
) -> Any:
    # Operator-free per-file NAS sync design (2026-05-21): the quiescence
    # poller boots whenever a staging_root is configured OR any equipment
    # is in ``nas`` sync mode -- it is the single auto-sync trigger for
    # both orchestrator-staged and nas-mode runs.
    if config is None:
        return None
    from exlab_wizard.constants import SyncMode

    has_staging_root = bool(config.orchestrator.staging_root)
    has_nas_equipment = any(eq.sync_mode == SyncMode.NAS for eq in config.equipment)
    if not (has_staging_root or has_nas_equipment):
        return None
    if nas_sync is None:
        msg = "quiescence poller requires nas_sync"
        raise RuntimeError(msg)
    if sync_state_writer is None:
        msg = "quiescence poller requires sync_state_writer"
        raise RuntimeError(msg)
    from exlab_wizard.orchestrator.quiescence_poller import QuiescenceSyncPoller

    return QuiescenceSyncPoller(
        config=config,
        nas_sync=nas_sync,
        sync_state_writer=sync_state_writer,
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
