"""Shared helpers for ``tests/unit/sync/`` test modules.

Kept under a leading-underscore name so pytest does not treat it as a
test module. The only public entry point is :func:`local_hashsum_factory`,
used by both :mod:`tests.unit.sync.test_nas_client` and
:mod:`tests.unit.sync.test_nas_client_extra` to inject a no-op-equivalent
remote-hash probe that recomputes from the local subtree.
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from pathlib import Path

from exlab_wizard.config.models import EquipmentConfig

__all__ = ["local_hashsum_factory"]


def local_hashsum_factory() -> Callable[
    [EquipmentConfig], Callable[[Path], Awaitable[dict[str, str]]]
]:
    """Hashsum factory whose closure recomputes from the local run dir.

    The injected stub push callables in these unit tests are no-ops --
    they don't actually transfer files anywhere, so the verifier can't
    probe a real "remote" subtree. We mimic a perfect remote by
    re-walking the local tree and returning the same SHA-256 manifest
    the verifier would expect, so the spec-mandated remote-verify check
    (§7.1.4 step 2) succeeds without needing a real rclone / rsync
    binary on PATH.
    """

    async def _hashsum(target: Path) -> dict[str, str]:
        out: dict[str, str] = {}
        for f in sorted(target.rglob("*")):
            if not f.is_file():
                continue
            rel = f.relative_to(target).as_posix()
            if rel.startswith(".exlab-wizard/"):
                continue
            out[rel] = hashlib.sha256(f.read_bytes()).hexdigest()
        return out

    def factory(_eq: EquipmentConfig) -> Callable[[Path], Awaitable[dict[str, str]]]:
        return _hashsum

    return factory
