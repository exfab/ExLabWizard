# rclone.conf-based NAS Sync — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move NAS sync from inline credential injection to operator-managed `rclone.conf` named remotes, add a global base root + lsjson manifest reconcile, gate the expensive hash-verify at cleanup, and expose `--transfers`/`--checkers`.

**Architecture:** A new top-level `nas:` config block names one rclone remote + a base root + perf knobs. The rclone driver drops all credential/env injection and instead lets rclone read `rclone.conf` (optionally pinned via `--config`). Routine post-push verification becomes a cheap `rclone lsjson` size/mtime reconcile; the full `rclone check --download` hash-verify is retained but runs only as the integrity gate immediately before local deletion (and on manual force-verify). The NAS keyring path and per-equipment transport blocks are removed (clean break).

**Tech Stack:** Python 3.12, Pydantic v2, ruamel.yaml (round-trip config), asyncio subprocess, rclone CLI, pytest, NiceGUI (UI), FastAPI (API).

**Spec:** `docs/superpowers/specs/2026-05-28-rclone-conf-nas-sync-design.md`

**Sequencing note (additive-then-remove):** Old transport/keyring code is kept working until consumers are migrated, then deleted in Phase 7. This keeps the test suite green between phases. Run `uv run pytest -q` at the end of every task; it must pass before commit unless a step says otherwise.

**Verification convention:** All commands use `uv run`. Type-check touched modules with `uv run mypy <path>` and lint with `uv run ruff check <path>` at each phase boundary.

---

## File Structure

**New files**
- `src/exlab_wizard/sync/manifest.py` — `RemoteEntry`, `RemoteManifest`, `parse_lsjson` (lsjson → run-relative size/mtime map). Sole owner of the lsjson wire shape.
- `tests/unit/sync/test_manifest.py` — parser tests.
- `docs/setup/rclone-remote-setup.md` — operator-facing "set up rclone.conf separately" guide.
- `tests/docker/rclone.conf` — fixture config naming the dockerised SFTP/SMB servers.

**Modified (by responsibility)**
- Config: `config/models.py` (add `NasConfig`/`RclonePerf`; remove transport union), `constants/enums.py` (rename setup enums; later remove `OrchestratorTransportType`).
- Transport: `sync/transports/rclone.py` (drop env/obscure; add `lsjson`, `--config/--transfers/--checkers`), `sync/transports/_run.py` (doc only).
- Sync client: `sync/nas_client.py` (named-remote target, lsjson reconcile, cleanup hash-gate, real remote-stat), `sync/verifier.py` (drop `env`/`mask_for_log` params).
- Setup/creds: `paths.py` (remote-availability gate), `tray/dependencies.py` (probe + NAS-sync wiring), `api/_dependencies.py`, `api/app.py`, `api/setup.py`, `api/routers/config.py`, `constants/keyring.py` (remove NAS helper).
- UI: `ui/pages/settings.py`, `ui/mount.py`, `ui/pages/main.py`, `ui/pages/wizard_equipment.py`, `ui/equipment_form.py`, `ui/components/metadata_pane.py`.
- Orchestrator staging (Phase 8, ⚑): `config/models.py`, `ui/equipment_form.py`, `ui/pages/wizard_equipment.py`.
- Tests/fixtures: `tests/fixtures/stub_rclone.py`, `tests/unit/sync/transports/test_rclone_env.py` (→ delete/replace), `tests/unit/sync/test_*`, `tests/unit/config/test_models.py`, `tests/unit/api/*`, `tests/unit/tray/test_dependencies.py`, `tests/unit/ui/*`, `tests/integration/test_nas_sync.py`, `tests/e2e/*`, `tests/docker/docker-compose.yml`.
- Docs: `design_specs/design_spec_sections/{04,07,09}_*.md`, `README.md`.

---

## Phase 1 — Config foundation (`nas:` block, additive)

### Task 1.1: Add `RclonePerf` and `NasConfig` models

**Files:**
- Modify: `src/exlab_wizard/config/models.py` (add models near the other sub-blocks, ~line 486 before `SyncConfig`; register on `Config` ~line 568)
- Test: `tests/unit/config/test_models.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/config/test_models.py — add
from exlab_wizard.config.models import Config, NasConfig, RclonePerf


def test_nas_config_defaults():
    nas = NasConfig(remote="nas01", base_root="/srv/lab")
    assert nas.remote == "nas01"
    assert nas.base_root == "/srv/lab"
    assert nas.rclone_config_path == ""
    assert nas.perf.transfers == 4
    assert nas.perf.checkers == 8
    assert nas.bandwidth.upload_mbps is None
    assert nas.mtime_tolerance_s == 2


def test_nas_config_rejects_nonpositive_perf():
    import pytest
    with pytest.raises(ValueError):
        RclonePerf(transfers=0)
    with pytest.raises(ValueError):
        RclonePerf(checkers=0)


def test_config_has_default_nas_block():
    cfg = Config()
    assert cfg.nas.remote == ""
    assert cfg.nas.base_root == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/config/test_models.py -k nas_config -v`
Expected: FAIL with `ImportError: cannot import name 'NasConfig'`.

- [ ] **Step 3: Implement the models**

In `config/models.py`, add before `SyncConfig` (after `BandwidthConfig` is already defined at line 205):

```python
class RclonePerf(BaseModel):
    """Parallelism knobs forwarded to rclone (``--transfers`` / ``--checkers``).

    These double as the memory dial on space- and RAM-constrained
    acquisition machines: peak memory scales with these counts times
    rclone's per-stream buffer.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    transfers: int = Field(default=4, ge=1, le=64)
    checkers: int = Field(default=8, ge=1, le=64)


class NasConfig(BaseModel):
    """``nas:`` block — the single rclone remote + base root for NAS sync.

    ``remote`` is the name of a remote defined in the operator's
    ``rclone.conf`` (set up separately with ``rclone config``). Equipment
    run folders live under ``<remote>:<base_root>/<equipment_id>/…``.
    ``rclone_config_path`` optionally pins ``rclone --config <path>`` for
    when the app runs as a different OS user than the one who created the
    config; blank means rclone's default discovery.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    remote: str = ""
    base_root: str = ""
    rclone_config_path: str = ""
    # Reconcile tolerance: a file counts as synced only when its remote modtime
    # is within this many seconds of local (absorbs SFTP/SMB modtime rounding).
    mtime_tolerance_s: int = Field(default=2, ge=0)
    perf: RclonePerf = Field(default_factory=RclonePerf)
    bandwidth: BandwidthConfig = Field(default_factory=BandwidthConfig)
```

Register on `Config` (line ~563, alongside `nas_cleanup`):

```python
    nas: NasConfig = Field(default_factory=NasConfig)
```

Add `"NasConfig"` and `"RclonePerf"` to the module `__all__` list (top of file).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/config/test_models.py -k nas_config -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/exlab_wizard/config/models.py tests/unit/config/test_models.py
git commit -m "feat(config): add nas: block (remote, base_root, perf, bandwidth)"
```

---

### Task 1.2: Round-trip the `nas:` block through the loader

**Files:**
- Test: `tests/unit/config/test_loader.py`
- Modify: none expected (ruamel round-trips unknown-but-modelled keys automatically); this task proves it.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/config/test_loader.py — add
def test_loader_round_trips_nas_block(tmp_path):
    from exlab_wizard.config.loader import load_config, save_config

    text = (
        "paths:\n"
        "  templates_dir: /t\n  plugin_dir: /p\n  local_root: /l\n"
        "orchestrator:\n  label: ws-1\n"
        "nas:\n"
        "  remote: nas01\n"
        "  base_root: /srv/lab\n"
        "  perf:\n    transfers: 2\n    checkers: 3\n"
    )
    p = tmp_path / "config.yaml"
    p.write_text(text, encoding="utf-8")
    cfg = load_config(p)
    assert cfg.nas.remote == "nas01"
    assert cfg.nas.perf.transfers == 2
    save_config(p, cfg, original_text=text)
    assert "nas01" in p.read_text(encoding="utf-8")
```

- [ ] **Step 2: Run test to verify it passes or fails**

Run: `uv run pytest tests/unit/config/test_loader.py -k nas_block -v`
Expected: PASS (model already registered in Task 1.1). If FAIL on validation, confirm `paths`/`orchestrator` minimal fields in the fixture text.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/config/test_loader.py
git commit -m "test(config): nas: block round-trips through the loader"
```

---

## Phase 2 — Transport driver (lsjson + config/perf flags, additive)

### Task 2.1: Add `--config`, `--transfers`, `--checkers` plumbing to the driver

**Files:**
- Modify: `src/exlab_wizard/sync/transports/rclone.py` (`RcloneDriver.__init__`, `push`, `check`)
- Test: `tests/unit/sync/test_transports.py`

The driver gains a `config_path: str | None`, `transfers: int | None`, `checkers: int | None` on `__init__`; argv builders append `--config <p>` / `--transfers N` / `--checkers N` when set.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/sync/test_transports.py — add
import asyncio
from pathlib import Path
from exlab_wizard.sync.transports.rclone import RcloneDriver


def test_push_argv_includes_config_and_perf(monkeypatch, tmp_path):
    captured = {}

    async def fake_run(cmd, *, env=None, stdin=None, mask_for_log=()):
        captured["cmd"] = cmd
        return 0, "", ""

    monkeypatch.setattr("exlab_wizard.sync.transports.rclone.run_subprocess", fake_run)
    drv = RcloneDriver(config_path="/etc/rclone.conf", transfers=2, checkers=3)
    asyncio.run(drv.push(tmp_path, "nas01:/srv/lab/EQ/run", bwlimit_kibps=None))
    cmd = captured["cmd"]
    assert "--config" in cmd and "/etc/rclone.conf" in cmd
    assert cmd[cmd.index("--transfers") + 1] == "2"
    assert cmd[cmd.index("--checkers") + 1] == "3"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/sync/test_transports.py -k argv_includes_config -v`
Expected: FAIL (`RcloneDriver.__init__` has no `config_path`).

- [ ] **Step 3: Implement**

In `rclone.py`:

```python
class RcloneDriver:
    def __init__(
        self,
        *,
        binary: str = "rclone",
        config_path: str | None = None,
        transfers: int | None = None,
        checkers: int | None = None,
    ) -> None:
        self._binary = binary
        self._config_path = config_path or None
        self._transfers = transfers
        self._checkers = checkers

    def _global_flags(self) -> list[str]:
        """Flags valid on every rclone subcommand we invoke."""
        flags: list[str] = []
        if self._config_path:
            flags.extend(["--config", self._config_path])
        return flags
```

In `push`, after `cmd = [self._binary, "copy", "--checksum"]`:

```python
        cmd.extend(self._global_flags())
        if self._transfers is not None:
            cmd.extend(["--transfers", str(self._transfers)])
        if self._checkers is not None:
            cmd.extend(["--checkers", str(self._checkers)])
```

In `check`, after `cmd = [self._binary, "check", "--download", ...]` add `cmd[1:1] = self._global_flags()` (insert global flags right after the binary) and append `--checkers` similarly. Apply the same `_global_flags()` insert in `about`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/sync/test_transports.py -k argv_includes_config -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/exlab_wizard/sync/transports/rclone.py tests/unit/sync/test_transports.py
git commit -m "feat(transport): rclone driver accepts --config/--transfers/--checkers"
```

---

### Task 2.2: Add `RcloneDriver.lsjson`

**Files:**
- Modify: `src/exlab_wizard/sync/transports/rclone.py`
- Test: `tests/unit/sync/test_transports.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/sync/test_transports.py — add
def test_lsjson_argv_is_recursive_readonly(monkeypatch):
    captured = {}

    async def fake_run(cmd, *, env=None, stdin=None, mask_for_log=()):
        captured["cmd"] = cmd
        return 0, '[{"Path":"a.txt","Name":"a.txt","Size":3,"ModTime":"2026-05-28T00:00:00Z","IsDir":false}]', ""

    monkeypatch.setattr("exlab_wizard.sync.transports.rclone.run_subprocess", fake_run)
    drv = RcloneDriver()
    out = asyncio.run(drv.lsjson("nas01:/srv/lab/EQ/run"))
    assert captured["cmd"][:2] == ["rclone", "lsjson"]
    assert "-R" in captured["cmd"]
    assert "nas01:/srv/lab/EQ/run" in captured["cmd"]
    assert '"Path":"a.txt"' in out


def test_lsjson_raises_transport_error_on_failure(monkeypatch):
    import pytest
    from exlab_wizard.sync.transports import TransportError

    async def fake_run(cmd, *, env=None, stdin=None, mask_for_log=()):
        return 1, "", "401 Unauthorized"

    monkeypatch.setattr("exlab_wizard.sync.transports.rclone.run_subprocess", fake_run)
    with pytest.raises(TransportError):
        asyncio.run(RcloneDriver().lsjson("nas01:/x"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/sync/test_transports.py -k lsjson -v`
Expected: FAIL (`RcloneDriver` has no `lsjson`).

- [ ] **Step 3: Implement**

Add to `RcloneDriver`:

```python
    async def lsjson(self, remote: str, *, recursive: bool = True) -> str:
        """Run ``rclone lsjson`` (read-only) and return the raw JSON array text.

        Listing only — no transfer, no remote mutation. ``recursive`` adds
        ``-R`` so a whole run subtree returns in one call. Raises
        :class:`TransportError` with a classified ``error_kind`` on a
        non-zero exit so callers route auth/network failures the same way
        as push/check.
        """
        cmd: list[str] = [self._binary, "lsjson", *self._global_flags()]
        if recursive:
            cmd.append("-R")
        if self._checkers is not None:
            cmd.extend(["--checkers", str(self._checkers)])
        cmd.append(remote)
        _log.debug("rclone lsjson cmd: %s", shlex.join(cmd))
        try:
            rc, stdout, stderr = await run_subprocess(cmd)
        except FileNotFoundError as exc:
            msg = f"rclone binary not found: {self._binary!r}"
            raise TransportError(msg) from exc
        if rc != 0:
            kind = _classify_failure(stderr, rc)
            msg = f"rclone lsjson failed rc={rc} kind={kind.value}: {stderr.strip()}"
            raise TransportError(msg, error_kind=kind)
        return stdout
```

Add `"lsjson"` to nothing (method, not export). Ensure `TransportError` is imported (it is).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/sync/test_transports.py -k lsjson -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/exlab_wizard/sync/transports/rclone.py tests/unit/sync/test_transports.py
git commit -m "feat(transport): add read-only RcloneDriver.lsjson"
```

---

### Task 2.3: Add `RcloneDriver.listremotes`

The single source of "which remotes exist in rclone.conf", reused by the setup
gate (Task 5.3) and the Settings "found?" badge (Task 6.1) — DRY: no other module
runs `rclone listremotes`.

**Files:**
- Modify: `src/exlab_wizard/sync/transports/rclone.py`
- Test: `tests/unit/sync/test_transports.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/sync/test_transports.py — add
def test_listremotes_parses_lines(monkeypatch):
    async def fake_run(cmd, *, env=None, stdin=None, mask_for_log=()):
        assert cmd[:2] == ["rclone", "listremotes"]
        return 0, "nas01:\nstagepc:\n", ""

    monkeypatch.setattr("exlab_wizard.sync.transports.rclone.run_subprocess", fake_run)
    remotes = asyncio.run(RcloneDriver().listremotes())
    assert remotes == ("nas01:", "stagepc:")


def test_listremotes_empty_on_failure(monkeypatch):
    async def fake_run(cmd, *, env=None, stdin=None, mask_for_log=()):
        return 1, "", "config not found"

    monkeypatch.setattr("exlab_wizard.sync.transports.rclone.run_subprocess", fake_run)
    assert asyncio.run(RcloneDriver().listremotes()) == ()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/sync/test_transports.py -k listremotes -v`
Expected: FAIL (`RcloneDriver` has no `listremotes`).

- [ ] **Step 3: Implement**

```python
    async def listremotes(self) -> tuple[str, ...]:
        """Return the remote names defined in rclone.conf (each incl. trailing ``:``).

        Offline and cheap — no network. Used by the setup-availability gate and
        the Settings remote badge. A missing/unreadable config yields ``()`` so
        callers treat "no remotes" the same as "remote not found".
        """
        cmd = [self._binary, "listremotes", *self._global_flags()]
        try:
            rc, stdout, _stderr = await run_subprocess(cmd)
        except FileNotFoundError:
            return ()
        if rc != 0:
            return ()
        return tuple(line.strip() for line in stdout.splitlines() if line.strip())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/sync/test_transports.py -k listremotes -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/exlab_wizard/sync/transports/rclone.py tests/unit/sync/test_transports.py
git commit -m "feat(transport): add RcloneDriver.listremotes"
```

---

## Phase 3 — Manifest module + parser

### Task 3.1: `sync/manifest.py` with `parse_lsjson`

**Files:**
- Create: `src/exlab_wizard/sync/manifest.py`
- Test: `tests/unit/sync/test_manifest.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/sync/test_manifest.py
from exlab_wizard.sync.manifest import RemoteEntry, RemoteManifest, parse_lsjson

SAMPLE = """[
  {"Path":"sub/a.txt","Name":"a.txt","Size":3,"ModTime":"2026-05-28T10:00:00.000Z","IsDir":false},
  {"Path":"sub","Name":"sub","Size":-1,"ModTime":"2026-05-28T09:00:00Z","IsDir":true},
  {"Path":"b.bin","Name":"b.bin","Size":1024,"ModTime":"2026-05-28T10:01:00Z","IsDir":false}
]"""


def test_parse_drops_dirs_and_keys_by_path():
    m = parse_lsjson(SAMPLE)
    assert set(m.entries) == {"sub/a.txt", "b.bin"}
    assert m.entries["b.bin"] == RemoteEntry(size=1024, mod_time="2026-05-28T10:01:00Z", is_dir=False)


def test_parse_strips_prefix():
    raw = '[{"Path":"EQ/run/x.txt","Name":"x.txt","Size":1,"ModTime":"t","IsDir":false}]'
    m = parse_lsjson(raw, strip_prefix="EQ/run")
    assert "x.txt" in m.entries


def test_parse_empty_and_malformed():
    assert parse_lsjson("[]").entries == {}
    assert parse_lsjson("").entries == {}
    assert parse_lsjson("not json").entries == {}


def test_manifest_size_match_helper():
    m = parse_lsjson(SAMPLE)
    assert m.size_matches("b.bin", 1024) is True
    assert m.size_matches("b.bin", 999) is False
    assert m.size_matches("missing.txt", 1) is False


def test_manifest_matches_size_and_mtime_within_tolerance():
    from datetime import datetime, timezone
    m = parse_lsjson(SAMPLE)
    epoch = datetime(2026, 5, 28, 10, 1, 0, tzinfo=timezone.utc).timestamp()  # b.bin ModTime
    assert m.matches("b.bin", 1024, epoch + 1, tolerance_s=2) is True   # within tol
    assert m.matches("b.bin", 1024, epoch + 10, tolerance_s=2) is False  # mtime drift
    assert m.matches("b.bin", 999, epoch, tolerance_s=2) is False        # size mismatch
    assert m.matches("missing.txt", 1, epoch, tolerance_s=2) is False    # absent


def test_manifest_to_epoch_handles_fractional_and_z():
    m = parse_lsjson(SAMPLE)
    # nanosecond precision + Z must parse (fromisoformat rejects 9 frac digits raw)
    assert m._to_epoch("2026-05-28T10:00:00.123456789Z") is not None
    assert m._to_epoch("") is None
    assert m._to_epoch("garbage") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/sync/test_manifest.py -v`
Expected: FAIL (`ModuleNotFoundError: exlab_wizard.sync.manifest`).

- [ ] **Step 3: Implement**

```python
# src/exlab_wizard/sync/manifest.py
"""Parser for ``rclone lsjson`` output into a run-relative remote manifest.

Sole owner of the lsjson wire shape. Consumers (the NAS sync client's
routine reconcile and cleanup remote-existence probe) depend only on the
:class:`RemoteManifest` value object, never on the raw JSON.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime

from exlab_wizard.logging import get_logger

__all__ = ["RemoteEntry", "RemoteManifest", "parse_lsjson"]

_log = get_logger(__name__)

# Trim fractional seconds to 6 digits: datetime.fromisoformat rejects the
# 9-digit nanosecond precision rclone can emit (e.g. "...:00.123456789Z").
_FRAC_RE = re.compile(r"\.(\d+)")


@dataclass(frozen=True, slots=True)
class RemoteEntry:
    """One file on the remote. ``mod_time`` is the RFC3339 string as emitted."""

    size: int
    mod_time: str
    is_dir: bool


@dataclass(frozen=True, slots=True)
class RemoteManifest:
    """Run-relative view of a remote subtree. Keyed by POSIX rel-path."""

    entries: dict[str, RemoteEntry]

    def size_matches(self, rel_path: str, local_size: int) -> bool:
        """True iff ``rel_path`` is present and its remote size equals local."""
        entry = self.entries.get(rel_path)
        return entry is not None and entry.size == local_size

    def has(self, rel_path: str) -> bool:
        """True iff ``rel_path`` is present on the remote (existence probe)."""
        return rel_path in self.entries

    def matches(
        self, rel_path: str, local_size: int, local_mtime: float, *, tolerance_s: float
    ) -> bool:
        """The single reconcile predicate: present AND size-equal AND modtime close.

        ``local_mtime`` is the local file's ``st_mtime`` (epoch seconds). A file
        is credited as synced only when the remote entry exists, its size equals
        ``local_size``, and its modtime is within ``tolerance_s`` of local
        (rclone preserves modtime on copy; the window absorbs SFTP/SMB rounding).
        """
        entry = self.entries.get(rel_path)
        if entry is None or entry.size != local_size:
            return False
        remote_epoch = self._to_epoch(entry.mod_time)
        if remote_epoch is None:
            return False
        return abs(remote_epoch - local_mtime) <= tolerance_s

    @staticmethod
    def _to_epoch(mod_time: str) -> float | None:
        """Parse an RFC3339 ``ModTime`` to epoch seconds, or ``None`` if unparseable."""
        s = mod_time.strip()
        if not s:
            return None
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        s = _FRAC_RE.sub(lambda m: "." + m.group(1)[:6], s, count=1)
        try:
            return datetime.fromisoformat(s).timestamp()
        except ValueError:
            return None


def parse_lsjson(raw: str, *, strip_prefix: str = "") -> RemoteManifest:
    """Parse ``rclone lsjson`` array text into a :class:`RemoteManifest`.

    Directory entries are dropped. ``strip_prefix`` (e.g.
    ``<base_root>/<equipment_id>/<run>``) is removed from each ``Path`` so
    keys are run-relative. Malformed / empty input yields an empty manifest
    (logged at debug) rather than raising — a listing we can't parse must
    not crash the sync worker.
    """
    text = raw.strip()
    if not text:
        return RemoteManifest(entries={})
    try:
        rows = json.loads(text)
    except json.JSONDecodeError:
        _log.debug("lsjson output was not valid JSON (%d chars)", len(text))
        return RemoteManifest(entries={})
    if not isinstance(rows, list):
        return RemoteManifest(entries={})

    prefix = strip_prefix.strip("/")
    out: dict[str, RemoteEntry] = {}
    for row in rows:
        if not isinstance(row, dict) or row.get("IsDir"):
            continue
        path = str(row.get("Path", "")).strip("/")
        if not path:
            continue
        if prefix and path.startswith(prefix + "/"):
            path = path[len(prefix) + 1 :]
        try:
            size = int(row.get("Size", -1))
        except (TypeError, ValueError):
            continue
        out[path] = RemoteEntry(
            size=size,
            mod_time=str(row.get("ModTime", "")),
            is_dir=False,
        )
    return RemoteManifest(entries=out)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/sync/test_manifest.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/exlab_wizard/sync/manifest.py tests/unit/sync/test_manifest.py
git commit -m "feat(sync): add lsjson manifest parser"
```

---

## Phase 4 — Sync client: named remote, lsjson reconcile, cleanup hash-gate

This phase flips the runtime behavior. It is the largest single change.

### Task 4.1: Named-remote target builder + driver construction from `nas:` config

**Files:**
- Modify: `src/exlab_wizard/sync/nas_client.py` (`_remote_name_for`, `_build_target_for`, `_build_transport_driver`, `_resolve_env_for_equipment` removal, constructor `keyring_store` removal)
- Test: `tests/unit/sync/test_nas_client.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/sync/test_nas_client.py — add (uses existing helpers/fixtures style)
from pathlib import Path
from exlab_wizard.sync.nas_client import _build_target_for_run


def test_build_target_for_run_uses_nas_block():
    target = _build_target_for_run(
        remote="nas01", base_root="/srv/lab", equipment_id="CONFOCAL_01",
        run=Path("/data/lab/CONFOCAL_01/PROJ-1/Run_2026-05-28"),
    )
    assert target == "nas01:/srv/lab/CONFOCAL_01/Run_2026-05-28"
```

> Note: this preserves today's `_build_target_for` leaf semantics (the run-leaf dir name is appended after the equipment folder; projects are not nested remotely). If project nesting is later desired, change here in one place.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/sync/test_nas_client.py -k build_target_for_run -v`
Expected: FAIL (`_build_target_for_run` undefined).

- [ ] **Step 3: Implement**

In `nas_client.py`, replace `_remote_name_for` / `_build_target_for` / `_resolve_env_for_equipment` / `_build_transport_driver` with `nas:`-config-driven helpers:

```python
def _build_target_for_run(*, remote: str, base_root: str, equipment_id: str, run: Path) -> str:
    """Compose ``<remote>:<base_root>/<equipment_id>/<run-leaf>``.

    Preserves the pre-migration leaf semantics: the run directory name is
    appended after the equipment folder. ``base_root`` is normalised so a
    blank value yields ``<remote>:<equipment_id>/<run-leaf>``.
    """
    parts = [base_root.strip("/"), equipment_id, run.name]
    path = "/".join(p for p in parts if p)
    return f"{remote}:/{path}"


def _build_driver(nas: NasConfig) -> RcloneDriver:
    return RcloneDriver(
        config_path=nas.rclone_config_path or None,
        transfers=nas.perf.transfers,
        checkers=nas.perf.checkers,
    )
```

Update imports: add `from exlab_wizard.config.models import NasConfig`; remove `RcloneSftpTransport`, `RcloneSmbTransport`, `keyring_nas_username`, `build_rclone_env`, `obscure`, `pass_env_keys_for` imports. Delete `_resolve_env_for_equipment` and `_build_transport_driver` entirely.

`NASSyncClient.__init__`: remove the `keyring_store` parameter and `self._keyring_store`. `_build_push` / `_build_check` become:

```python
    def _build_push(self, equipment: EquipmentConfig) -> Callable[..., Any]:
        if self._push_callable_factory is not None:
            return self._push_callable_factory(equipment)
        driver = _build_driver(self._config.nas)

        async def _push(local, *, bwlimit_kibps, files_from=None):
            target = _build_target_for_run(
                remote=self._config.nas.remote,
                base_root=self._config.nas.base_root,
                equipment_id=equipment.id,
                run=local,
            )
            return await driver.push(local, target, bwlimit_kibps=bwlimit_kibps, files_from=files_from)

        return _push
```

`_build_check` mirrors this (calls `driver.check(local, target, files_from=files_from)` — no `env`/`mask`). Add a `_build_lsjson(equipment)` returning a closure that calls `driver.lsjson(target)` for the run and returns `parse_lsjson(raw, strip_prefix=...)` where the strip prefix is `f"{base_root.strip('/')}/{equipment.id}/{run.name}"`.

**DRY — centralise target resolution:** factor the remote/base_root selection into a single `_target_for_equipment(equipment, run) -> str` that returns `_build_target_for_run(remote=nas.remote, base_root=nas.base_root, …)` for nas-mode. Phase 8 extends *only this method* to return the staging remote/base_root when `sync_mode == 'stage'`; push/check/lsjson closures all call it, so neither the driver nor the closures branch on mode.

- [ ] **Step 4: Run test + targeted suite**

Run: `uv run pytest tests/unit/sync/test_nas_client.py -k build_target_for_run -v`
Expected: PASS. (Broader `test_nas_client.py` will fail until Task 4.2–4.4 + Task 6.x update construction; that is expected and addressed there.)

- [ ] **Step 5: Commit**

```bash
git add src/exlab_wizard/sync/nas_client.py tests/unit/sync/test_nas_client.py
git commit -m "refactor(sync): build rclone target from nas: block, drop keyring/env"
```

---

### Task 4.2: `verifier.py` — drop `env`/`mask_for_log`

**Files:**
- Modify: `src/exlab_wizard/sync/verifier.py` (`Verifier.verify` signature; call site)
- Test: `tests/unit/sync/test_nas_client.py` (existing verifier usage) / add focused test

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/sync/test_transports.py — add (verifier is small; test via stub driver)
import asyncio
from exlab_wizard.sync.verifier import Verifier
from exlab_wizard.sync.transports.rclone import CheckResult


class _StubDriver:
    async def check(self, run_path, remote, *, files_from):
        return CheckResult(equal=("a.txt",))


def test_verifier_verify_no_env_param(tmp_path):
    v = Verifier(_StubDriver())
    res = asyncio.run(v.verify(tmp_path, "nas01:/x", files_from=tmp_path / "f"))
    assert res.ok and res.verified == ("a.txt",)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/sync/test_transports.py -k verifier_verify_no_env -v`
Expected: FAIL (`_StubDriver.check` got unexpected `env`).

- [ ] **Step 3: Implement**

In `verifier.py`, remove `env` and `mask_for_log` params from `Verifier.verify` and from the `self._driver.check(...)` call.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/sync/test_transports.py -k verifier_verify_no_env -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/exlab_wizard/sync/verifier.py tests/unit/sync/test_transports.py
git commit -m "refactor(sync): verifier no longer threads credential env"
```

---

### Task 4.3: Routine post-push reconcile via lsjson (replaces per-push `--download`)

**Files:**
- Modify: `src/exlab_wizard/sync/nas_client.py` (`_drive_job`, add `_reconcile_via_manifest`)
- Test: `tests/unit/sync/test_nas_client.py`

`_drive_job` changes: after a successful push, instead of running `rclone check --download`, build the lsjson manifest, mark each file synced when `manifest.matches(rel, size, mtime, tolerance_s=nas.mtime_tolerance_s)` (present AND size-equal AND modtime within tolerance), transition to `VERIFIED`, mark synced, then `_maybe_cleanup`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/sync/test_nas_client.py — add
# Build a client with push_callable_factory + a new lsjson_callable_factory that
# returns a RemoteManifest matching local sizes AND modtimes; assert job reaches
# VERIFIED and sync_state.json credits each file WITHOUT a check --download call.
def test_routine_reconcile_marks_synced_from_lsjson(tmp_path, ...):
    # See _helpers.make_client; inject lsjson_callable_factory returning
    # parse_lsjson of a listing whose Size == local size and ModTime is within
    # nas.mtime_tolerance_s of the local file's mtime.
    ...
    assert status == "verified"
    assert check_calls == 0  # hash-verify NOT run on the routine path
```

(Use the existing `tests/unit/sync/_helpers.py` client builder; extend it with an `lsjson_callable_factory` injection — see Task 7.2.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/sync/test_nas_client.py -k routine_reconcile -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

Add an `lsjson_callable_factory` constructor param (mirrors `push_callable_factory`) and a `_build_lsjson`. Rewrite the verify section of `_drive_job` (lines ~622–683 in the current file): after `await self._queue.transition(job.id, SyncJobState.AWAITING_VERIFY)`, replace the `check` block with:

```python
            lsjson = self._build_lsjson(equipment)
            try:
                manifest = await lsjson(run_path)
            except TransportError as exc:
                # auth → terminal; network/unknown → backoff (reuse existing routing)
                await self._handle_verify_transport_error(job, exc)
                return

            tol = self._config.nas.mtime_tolerance_s
            synced_at = utc_now_iso()
            credited: list[str] = []
            for rel in verify_files:
                # Reuse the existing _file_signature helper for both checks
                # (returns (st_size, st_mtime_ns)); convert ns → epoch seconds.
                sig = self._file_signature(run_path / rel)
                if sig is None:
                    continue
                size, mtime_ns = sig
                if manifest.matches(rel, size, mtime_ns / 1e9, tolerance_s=tol):
                    with contextlib.suppress(Exception):
                        await self._sync_state_writer.upsert_file(
                            run_path, rel,
                            synced_signature=sig,
                            verified_at=synced_at,
                            verified_sha256=local_shas.get(rel),
                        )
                    credited.append(rel)

            if len(credited) != len(verify_files):
                # Some files missing / size- or mtime-mismatched on the remote:
                # re-queue the subset on the next sweep rather than mark VERIFIED.
                await self._queue.transition(
                    job.id, SyncJobState.QUEUED,
                    last_error="remote_reconcile_incomplete", next_attempt_at="",
                )
                return
```

Then fall through to the existing VERIFIED transition + `_mark_synced` + `_maybe_cleanup` (keep those). No new local-stat helper is needed — `_file_signature` (already on the class, returning `(st_size, st_mtime_ns)`) supplies both the size and the modtime, keeping the stat logic in one place.

Add `_handle_verify_transport_error(job, exc)` factoring the AUTH→terminal / NETWORK,UNKNOWN→backoff routing already present in `_drive_job`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/sync/test_nas_client.py -k routine_reconcile -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/exlab_wizard/sync/nas_client.py tests/unit/sync/test_nas_client.py tests/unit/sync/_helpers.py
git commit -m "feat(sync): routine post-push reconcile via lsjson size-match"
```

---

### Task 4.4: Cleanup hash-gate + real remote-existence probe

**Files:**
- Modify: `src/exlab_wizard/sync/nas_client.py` (`_maybe_cleanup`, default `_remote_stat_callable`)
- Test: `tests/unit/sync/test_nas_client.py`, `tests/unit/sync/test_cleanup.py`

Before `_delete_local`, (a) probe lsjson that every tracked file is present, (b) run `rclone check --download` over all tracked files; delete only on `verify_result.ok`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/sync/test_nas_client.py — add
def test_cleanup_runs_hash_gate_before_delete(tmp_path, ...):
    # cleanup enabled + interlocks satisfied + check_callable_factory returns ok
    # → local files deleted, state CLEANED, check --download was called once.
    ...

def test_cleanup_aborts_delete_on_hash_mismatch(tmp_path, ...):
    # check_callable_factory returns VerifyResult(ok=False, mismatched=(...))
    # → local files remain, state NOT CLEANED.
    ...
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/sync/test_nas_client.py -k cleanup_ -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

In `_maybe_cleanup`, after the interlocks pass and before `self._delete_local(...)`:

```python
        # Integrity gate: never delete local bytes that have not been
        # content-verified against the remote. This is the one place we pay
        # the full rclone check --download re-download (streamed, no disk).
        tracked = tuple(sorted(sync_state.files.keys()))
        if tracked:
            check = self._build_check(equipment)
            files_from = self._write_files_from(tracked)
            try:
                try:
                    check_result = await check(run_path, files_from=files_from)
                except TransportError as exc:
                    _log.warning("cleanup hash-gate transport error: %s", exc)
                    await self._queue.transition(job_id, SyncJobState.CLEANUP_ELIGIBLE)
                    return
                verify = VerifyResult.from_check_result(check_result)
            finally:
                with contextlib.suppress(OSError):
                    files_from.unlink()
            if not verify.ok:
                _log.warning("cleanup hash-gate mismatch on %s; not deleting", run_path)
                await self._queue.transition(job_id, SyncJobState.CLEANUP_ELIGIBLE)
                return
```

Replace the default `_remote_stat_callable` with an async lsjson existence probe wired through `_maybe_cleanup`. Simplest: change the interlock to call an async `self._remote_files_present(run_path, equipment, tracked)` that runs lsjson and returns `all(manifest.has(rel) for rel in tracked)`; keep `_remote_stat_callable` injectable for tests (default `None` → use the real probe).

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/unit/sync/test_nas_client.py -k cleanup_ tests/unit/sync/test_cleanup.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/exlab_wizard/sync/nas_client.py tests/unit/sync/test_nas_client.py tests/unit/sync/test_cleanup.py
git commit -m "feat(sync): hash-gate + lsjson existence probe before local deletion"
```

---

### Task 4.5: `force_verify` — drop env threading

**Files:**
- Modify: `src/exlab_wizard/sync/nas_client.py` (`force_verify`)
- Test: `tests/unit/sync/test_nas_client.py`

- [ ] **Step 1–4:** `force_verify` already calls `self._build_check(equipment)` → `check(run_path, files_from=...)`. After Task 4.1 `_build_check` no longer threads env, so `force_verify` needs no signature change — but verify the test that exercises it still passes:

Run: `uv run pytest tests/unit/sync/test_nas_client.py -k force_verify -v`
Expected: PASS (adjust the test's stub `check` signature to drop `env`/`mask_for_log` if present).

- [ ] **Step 5: Commit** (only if a test edit was needed)

```bash
git add tests/unit/sync/test_nas_client.py
git commit -m "test(sync): force_verify stub matches env-free check signature"
```

---

## Phase 5 — Setup gate + credential removal

### Task 5.1: Repurpose setup-state enums

**Files:**
- Modify: `src/exlab_wizard/constants/enums.py` (`SetupState`, `SetupNextAction`)
- Test: `tests/unit/constants/test_enums.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/constants/test_enums.py — add
from exlab_wizard.constants.enums import SetupState, SetupNextAction


def test_nas_remote_setup_members_exist():
    assert SetupState.INCOMPLETE_NO_NAS_REMOTE.value == "incomplete_no_nas_remote"
    assert SetupNextAction.CONFIGURE_RCLONE_REMOTE.value == "configure_rclone_remote"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/constants/test_enums.py -k nas_remote -v`
Expected: FAIL (`AttributeError`).

- [ ] **Step 3: Implement**

In `enums.py`: rename `INCOMPLETE_NO_NAS_CREDENTIAL = "incomplete_no_nas_credential"` → `INCOMPLETE_NO_NAS_REMOTE = "incomplete_no_nas_remote"`. Find `SetupNextAction` (around the `SET_NAS_CREDENTIALS` member) and rename `SET_NAS_CREDENTIALS = "set_nas_credentials"` → `CONFIGURE_RCLONE_REMOTE = "configure_rclone_remote"`.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/unit/constants/test_enums.py -k nas_remote -v`
Expected: PASS. (Other modules referencing the old names will fail import/grep — fixed in 5.2–5.4.)

- [ ] **Step 5: Commit**

```bash
git add src/exlab_wizard/constants/enums.py tests/unit/constants/test_enums.py
git commit -m "refactor(enums): NAS setup state is remote-availability not credential"
```

---

### Task 5.2: Rewrite the NAS setup-gate in `paths.py`

**Files:**
- Modify: `src/exlab_wizard/paths.py` (`_password_required_equipment` → remove; `_nas_slot_satisfied` → `_nas_remote_configured`; `evaluate_setup_state`; `setup_state_missing`; `_missing_nas_fields`; `setup_state_next_action`)
- Test: `tests/unit/test_paths.py`

The gate is satisfied when `config.nas.remote` is non-empty **and** present in `rclone listremotes` (via an injected callable, default "configured-name-is-enough" so unit tests need no rclone).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_paths.py — add
from exlab_wizard.paths import evaluate_setup_state
from exlab_wizard.constants import SetupState
from exlab_wizard.config.models import Config, NasConfig, EquipmentConfig


def _ready_config(remote=""):
    return Config(
        paths={"templates_dir": "/t", "plugin_dir": "/p", "local_root": "/l"},
        orchestrator={"label": "ws-1"},
        equipment=[EquipmentConfig(id="EQ_01", label="Eq", local_root="/l", nas_root="//n/x")],
        lims={"endpoint": "https://x", "email": "a@b.c"},
        nas=NasConfig(remote=remote, base_root="/srv"),
    )


def test_setup_incomplete_when_nas_remote_blank():
    state = evaluate_setup_state(_ready_config(remote=""), nas_remote_available=lambda n: True)
    assert state == SetupState.INCOMPLETE_NO_NAS_REMOTE


def test_setup_incomplete_when_remote_not_in_rclone_conf():
    state = evaluate_setup_state(_ready_config(remote="nas01"), nas_remote_available=lambda n: False)
    assert state == SetupState.INCOMPLETE_NO_NAS_REMOTE


def test_setup_ready_when_remote_present():
    state = evaluate_setup_state(_ready_config(remote="nas01"), nas_remote_available=lambda n: True)
    assert state == SetupState.READY
```

> EquipmentConfig no longer requires a `transport` for nas-mode after Task 7.1; until then this test needs a transport. Run this test AFTER Task 7.1, OR temporarily construct equipment with `sync_mode="stage"`. Mark dependency: **depends on Task 7.1**.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/test_paths.py -k nas_remote -v`
Expected: FAIL (`evaluate_setup_state` has no `nas_remote_available`).

- [ ] **Step 3: Implement**

Replace `_password_required_equipment` and `_nas_slot_satisfied` with:

```python
def _nas_in_use(config: Config) -> bool:
    """True when at least one nas-mode equipment exists (NAS sync is active)."""
    from exlab_wizard.constants import SyncMode
    return any(eq.sync_mode == SyncMode.NAS for eq in config.equipment)


def _nas_remote_satisfied(config: Config, *, nas_remote_available: Callable[[str], bool]) -> bool:
    """True when the nas: remote is configured AND present in rclone.conf.

    Only gates when NAS sync is actually in use (a stage-only device with no
    nas-mode equipment does not need a NAS remote).
    """
    if not _nas_in_use(config):
        return True
    remote = config.nas.remote
    return bool(remote) and nas_remote_available(remote)
```

In `evaluate_setup_state`: replace the `nas_password_present_for` param with `nas_remote_available: Callable[[str], bool] | None = None` (default `lambda _n: True`); replace gate 4 with:

```python
    remote_lookup = nas_remote_available if nas_remote_available is not None else (lambda _n: True)
    if not _nas_remote_satisfied(config, nas_remote_available=remote_lookup):
        return SetupState.INCOMPLETE_NO_NAS_REMOTE
```

In `setup_state_missing`: change the `INCOMPLETE_NO_NAS_CREDENTIAL` branch to `INCOMPLETE_NO_NAS_REMOTE` and have `_missing_nas_fields` return:

```python
def _missing_nas_fields(config: Config | None) -> list[dict[str, str]]:
    if config is None or not config.nas.remote:
        return [{"field": "nas.remote", "reason": "unset"}]
    return [{"field": "nas.remote", "reason": "not_found_in_rclone_conf"}]
```

In `setup_state_next_action`: `INCOMPLETE_NO_NAS_REMOTE → SetupNextAction.CONFIGURE_RCLONE_REMOTE`.

- [ ] **Step 4: Run to verify it passes** (after Task 7.1)

Run: `uv run pytest tests/unit/test_paths.py -k nas_remote -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/exlab_wizard/paths.py tests/unit/test_paths.py
git commit -m "feat(setup): gate on rclone remote availability, not keyring password"
```

---

### Task 5.3: Tray probe + NAS-sync wiring + password-presence removal

**Files:**
- Modify: `src/exlab_wizard/tray/dependencies.py` (`_make_equipment_probe`, `_build_nas_sync`, remove `_check_nas_passwords_present`/`_nas_keyring_password` + the `deps.nas_password_present` hydration ~line 143)
- Test: `tests/unit/tray/test_dependencies.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/tray/test_dependencies.py — add/replace probe test
def test_equipment_probe_uses_named_remote(monkeypatch):
    # deps.config.nas.remote == "nas01"; probe calls RcloneDriver.about("nas01:")
    # with NO env; returns ok on STUB about_success.
    ...

def test_nas_remote_available_checks_listremotes(monkeypatch):
    # boot hydrates deps.nas_remotes via RcloneDriver.listremotes(); the sync
    # predicate reports membership of f"{remote}:".
    ...
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/tray/test_dependencies.py -k remote -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

Rewrite `_make_equipment_probe._probe` to ignore the keyring and probe the `nas:` remote:

```python
    async def _probe(equipment: Any) -> dict[str, Any]:
        nas = getattr(deps.config, "nas", None)
        if nas is None or not nas.remote:
            return {"ok": False, "reason": "no NAS remote configured"}
        from exlab_wizard.sync.transports.rclone import RcloneDriver
        driver = RcloneDriver(config_path=nas.rclone_config_path or None)
        started = time.monotonic()
        try:
            about = await driver.about(f"{nas.remote}:")
        except Exception as exc:
            return {"ok": False, "reason": str(exc)}
        latency_ms = int((time.monotonic() - started) * 1000)
        if not about.ok:
            return {"ok": False, "reason": about.reason, "latency_ms": latency_ms}
        return {"ok": True, "reason": None, "latency_ms": latency_ms}
```

Hydrate the available remotes **through the driver op** (DRY — `tray` must not run rclone itself). At boot, in place of the `deps.nas_password_present` block (line ~143), one-shot the async op and cache the result, mirroring the existing "hydrate once at boot, sync lookup thereafter" pattern:

```python
    def _hydrate_nas_remotes(config: Any) -> tuple[str, ...]:
        nas = getattr(config, "nas", None)
        if nas is None:
            return ()
        from exlab_wizard.sync.transports.rclone import RcloneDriver
        driver = RcloneDriver(config_path=getattr(nas, "rclone_config_path", "") or None)
        try:
            return asyncio.run(driver.listremotes())   # sync boot context, no running loop
        except Exception:
            return ()

    deps.nas_remotes = _try("nas_remotes", _hydrate_nas_remotes, deps.config) or ()
    deps.nas_remote_available = lambda remote: f"{remote}:" in deps.nas_remotes
```

Delete `_check_nas_passwords_present` and `_nas_keyring_password`. `deps.nas_remote_available` is the single sync predicate the setup gate (Task 5.2) and API (Task 5.4) consume.

`_build_nas_sync`: remove the `keyring_store` argument from the `NASSyncClient(...)` call and from the function signature; remove the keyring import.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/unit/tray/test_dependencies.py -k remote -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/exlab_wizard/tray/dependencies.py tests/unit/tray/test_dependencies.py
git commit -m "feat(tray): equipment probe + setup gate use named rclone remote"
```

---

### Task 5.4: API surfaces — drop `nas_password_present`, thread `nas_remote_available`

**Files:**
- Modify: `src/exlab_wizard/api/_dependencies.py` (remove `nas_password_present`; add `nas_remote_available`), `src/exlab_wizard/api/app.py` (replace the `nas_password_present: set` field with `nas_remote_available: Callable`), `src/exlab_wizard/api/setup.py` and `src/exlab_wizard/api/routers/config.py` (swap the `nas_password_present_for=...` kwargs for `nas_remote_available=...`)
- Test: `tests/unit/api/test_setup.py`, `tests/unit/api/test_config_router.py`

- [ ] **Step 1: Write the failing test** — assert `GET /setup/status` returns `next_action == "configure_rclone_remote"` when `nas.remote` is blank and equipment is nas-mode.

- [ ] **Step 2: Run to verify it fails.**
Run: `uv run pytest tests/unit/api/test_setup.py -k rclone_remote -v` → FAIL.

- [ ] **Step 3: Implement** the three swaps. `api/app.py`: 
```python
    nas_remote_available: Callable[[str], bool] = field(default=lambda _n: True)
```
`api/_dependencies.py`: replace `nas_password_present(deps, eq_id)` with `nas_remote_available(deps, remote)` reading `deps.nas_remote_available`. `setup.py` / `config.py`: replace each `nas_password_present_for=lambda equipment_id: nas_password_present(deps, equipment_id)` with `nas_remote_available=lambda remote: deps.nas_remote_available(remote)` and pass it to `evaluate_setup_state` / `setup_state_missing`.

- [ ] **Step 4: Run to verify it passes.**
Run: `uv run pytest tests/unit/api/test_setup.py tests/unit/api/test_config_router.py -v` → PASS.

- [ ] **Step 5: Commit**
```bash
git add src/exlab_wizard/api/ tests/unit/api/
git commit -m "feat(api): setup status reports rclone-remote availability"
```

---

## Phase 6 — UI

### Task 6.1: Settings "NAS Remote" section (replaces NAS Credentials)

**Files:**
- Modify: `src/exlab_wizard/ui/pages/settings.py` (`_render_nas_credentials_section` → `_render_nas_remote_section`, section id/label, ordering helpers), `src/exlab_wizard/ui/mount.py` (`_nas_credential_handlers` removal; `_nas_credential_missing` → `_nas_remote_missing`; readiness list ~line 1592)
- Test: `tests/unit/ui/test_settings_nas_credentials.py` (rename → `test_settings_nas_remote.py`), `tests/unit/ui/test_mount.py`

- [ ] **Step 1: Write the failing test** — the NAS section renders the configured `nas.remote` + `base_root`, a found/not-found badge from `nas_remote_available`, and a "Test connection" button bound to the equipment probe; it has NO password input.

- [ ] **Step 2: Run → FAIL.**
Run: `uv run pytest tests/unit/ui/test_settings_nas_remote.py -v`

- [ ] **Step 3: Implement.** Replace `NAS_CREDENTIALS_SECTION = "nas_credentials"` with `NAS_REMOTE_SECTION = "nas_remote"` and label `"NAS Remote"`. `_render_nas_remote_section(container, *, nas, nas_remote_available, on_test_connection)` renders read-only remote/base_root + badge + Test button. Drop `nas_password_present_for` / `nas_credential_handlers` params throughout `settings.py`. In `mount.py`, delete `_nas_credential_handlers`, replace `_nas_credential_missing(deps, config)` with `_nas_remote_missing(deps, config)` (returns True when nas-mode equipment exist and `not config.nas.remote or not deps.nas_remote_available(config.nas.remote)`), update the readiness `missing.append("nas_credentials")` → `missing.append("nas_remote")`.

- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit**
```bash
git add src/exlab_wizard/ui/pages/settings.py src/exlab_wizard/ui/mount.py tests/unit/ui/
git rm tests/unit/ui/test_settings_nas_credentials.py 2>/dev/null || true
git commit -m "feat(ui): Settings shows NAS remote status, not password entry"
```

### Task 6.2: Equipment wizard/form — drop transport fields; main-page banner copy

**Files:**
- Modify: `src/exlab_wizard/ui/pages/wizard_equipment.py`, `src/exlab_wizard/ui/equipment_form.py`, `src/exlab_wizard/ui/pages/main.py` (banner subline map ~line 118), `src/exlab_wizard/ui/components/metadata_pane.py` (unchanged — `nas_root` display stays)
- Test: `tests/unit/ui/test_wizard_equipment.py`, `tests/unit/ui/test_dynamic_form.py`, `tests/unit/ui/test_mount.py`

- [ ] **Step 1: Write the failing test** — building a nas-mode equipment from the form produces an `EquipmentConfig` with no transport block and validates; the wizard exposes no host/port/user/share/password inputs for nas mode.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement.** Remove SFTP/SMB field state + widgets from `wizard_equipment.py` and the `transport=` construction in `equipment_form.py` for nas mode (nas-mode `build` sets `transport=None`). In `main.py`, change `"set_nas_credentials": (...)` entry to a `"configure_rclone_remote": ("Configure the rclone remote (see setup docs) to begin.")` keyed by the new next-action value.
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit**
```bash
git add src/exlab_wizard/ui/ tests/unit/ui/
git commit -m "feat(ui): equipment wizard drops NAS transport/credential fields"
```

---

## Phase 7 — Remove dead code (clean break)

### Task 7.1: Remove the transport union + EquipmentConfig.transport

**Files:**
- Modify: `src/exlab_wizard/config/models.py` (delete `RcloneSftpTransport`, `RcloneSmbTransport`, `EquipmentTransport`, `transport_requires_keyring_password`, `OrchestratorStagingTransport` IF Phase 8 not taken; `EquipmentConfig.transport` field + validator)
- Test: `tests/unit/config/test_models.py`

- [ ] **Step 1: Write the failing test**

```python
def test_nas_mode_equipment_needs_no_transport_block():
    eq = EquipmentConfig(id="EQ_01", label="Eq", local_root="/l", nas_root="//n/x", sync_mode="nas")
    assert eq.sync_mode.value == "nas"

def test_transport_symbols_removed():
    import exlab_wizard.config.models as m
    assert not hasattr(m, "RcloneSftpTransport")
    assert not hasattr(m, "EquipmentTransport")
```

- [ ] **Step 2: Run → FAIL** (`EquipmentConfig` still requires transport for nas).
- [ ] **Step 3: Implement.** Delete the transport classes (lines 227–285), remove `transport` and `orchestrator_staging_transport` fields' nas/stage coupling. Rewrite `_sync_mode_dictates_transport` to only validate `sync_mode` membership (no per-equipment transport requirement). Remove the deleted names from `__all__`. Keep `OrchestratorStagingTransport` only if Phase 8 is taken; otherwise delete it and the `OrchestratorTransportType` enum.
- [ ] **Step 4: Run → PASS.** Then `uv run pytest -q` whole suite.
- [ ] **Step 5: Commit**
```bash
git add src/exlab_wizard/config/models.py tests/unit/config/test_models.py
git commit -m "refactor(config): remove per-equipment transport blocks (clean break)"
```

### Task 7.2: Remove `build_rclone_env`/`obscure`/`pass_env_keys_for` + keyring helper + env plumbing

**Files:**
- Modify: `src/exlab_wizard/sync/transports/rclone.py` (delete `build_rclone_env`, `pass_env_keys_for`, `obscure`; trim `__all__`; remove `env`/`mask_for_log` params from `push`/`check`/`about`), `src/exlab_wizard/sync/transports/_run.py` (remove `env`/`mask_for_log` from `run_subprocess`; update docstring), `src/exlab_wizard/constants/keyring.py` (delete `keyring_nas_username` + `constants/__init__.py` export)
- Test: delete `tests/unit/sync/transports/test_rclone_env.py`

- [ ] **Step 1:** `git rm tests/unit/sync/transports/test_rclone_env.py`.
- [ ] **Step 2:** Run `uv run pytest -q` → expect failures only in files still importing the removed symbols; grep first: `grep -rn "build_rclone_env\|pass_env_keys_for\|obscure\|keyring_nas_username\|RCLONE_CONFIG_" src tests`.
- [ ] **Step 3: Implement** the deletions. Simplify `run_subprocess` to `run_subprocess(cmd, *, stdin=None)` (the only remaining caller passing stdin was `obscure`, now gone, so `stdin` can also go — verify with grep; if nothing uses stdin, drop it). Update `_run.py` docstring to describe a plain spawn helper.
- [ ] **Step 4:** `uv run pytest -q` → PASS. `grep` returns nothing in `src/`.
- [ ] **Step 5: Commit**
```bash
git add -A
git commit -m "refactor(transport): remove credential injection + NAS keyring helper"
```

---

## Phase 8 — Orchestrator staging via rclone (confirmed in scope)

> Today the stage-mode hop is a mounted-filesystem copy (`OrchestratorTransportType` = `smb_mount`/`file_transfer`) and `OrchestratorStagingTransport` is not wired to any push. This phase replaces it with an rclone push to a named staging remote, **reusing the same `RcloneDriver` ops and the same `_build_target_for_run` helper as the NAS leg — no new rclone operation is introduced** (DRY). `_target_for_equipment(equipment)` selects the staging remote/base_root when `sync_mode == 'stage'` and the NAS remote/base_root otherwise.

### Task 8.1: Staging config — `orchestrator.staging_remote` / `staging_base_root`

**Files:**
- Modify: `src/exlab_wizard/config/models.py` (`OrchestratorConfig`: add `staging_remote: str = ""`, `staging_base_root: str = ""`, `staging_perf: RclonePerf`; delete `OrchestratorStagingTransport`), `src/exlab_wizard/constants/enums.py` (delete `OrchestratorTransportType`), `src/exlab_wizard/ui/equipment_form.py`/`wizard_equipment.py` (drop mount fields)
- Test: `tests/unit/config/test_models.py`

- [ ] Steps mirror Task 1.1 (TDD): add fields + default test, then implement, then update the wizard/form to set `sync_mode="stage"` without a mount block. Commit `feat(config): orchestrator staging uses a named rclone remote`.

### Task 8.2: Stage-mode push routes through rclone

**Files:**
- Modify: `src/exlab_wizard/sync/nas_client.py` or a new `sync/staging_push.py` (decide: the quiescence poller already enqueues stage runs into `NASSyncClient`; the cleanest path is to give `NASSyncClient` a per-equipment target resolver that picks the staging remote+root for stage-mode and the nas remote+root for nas-mode)
- Test: `tests/unit/sync/test_nas_client.py`, `tests/integration/test_orchestrator_lifecycle.py`

- [ ] TDD: a stage-mode equipment's push target is `<staging_remote>:<staging_base_root>/<equipment_id>/<run-leaf>`. Implement a `_target_for_equipment(equipment)` that branches on `sync_mode`. Commit `feat(sync): stage-mode equipment push via rclone staging remote`.

---

## Phase 9 — Fixtures, integration, docker, docs

### Task 9.1: Teach `stub_rclone` lsjson + listremotes; drop env-require

**Files:**
- Modify: `tests/fixtures/stub_rclone.py`
- Test: exercised indirectly by sync/integration tests

- [ ] **Step 1:** Add to the module docstring + `main()`:
  - `lsjson` verb: read the copied dest tree under `STUB_RCLONE_DEST_ROOT` for the requested `<remote>:<path>` and print a JSON array of `{Path,Name,Size,ModTime,IsDir}` (Path relative to the listed root, recursive). Honor `STUB_RCLONE_BEHAVIOR` (`lsjson_empty` → `[]`; `auth_error`/`network_error` → stderr + rc 1).
  - `listremotes` verb: print remotes from `STUB_RCLONE_LISTREMOTES` (default `nas01:`), one per line.
  - Delete `_dump_env`, `_require_env`, and the `STUB_RCLONE_REQUIRE_ENV`/`STUB_RCLONE_ENV_DUMP` docs (no env injection anymore). Remove the `obscure` verb branch.

```python
    if verb == "lsjson":
        if behavior in ("auth_error", "network_error"):
            sys.stderr.write("401 Unauthorized\n" if behavior == "auth_error" else "network timeout\n")
            return 1
        remote_arg = next((a for a in sys.argv[2:] if not a.startswith("-")), "")
        _, _, path = remote_arg.partition(":")
        root = Path(dest_root) / path.lstrip("/") if dest_root else Path(path)
        rows = []
        if root.is_dir():
            for f in sorted(root.rglob("*")):
                rel = f.relative_to(root).as_posix()
                rows.append({
                    "Path": rel, "Name": f.name,
                    "Size": f.stat().st_size if f.is_file() else -1,
                    "ModTime": "2026-05-28T00:00:00Z", "IsDir": f.is_dir(),
                })
        sys.stdout.write(json.dumps(rows))
        return 0

    if verb == "listremotes":
        sys.stdout.write(os.environ.get("STUB_RCLONE_LISTREMOTES", "nas01:") + "\n")
        return 0
```

- [ ] **Step 2–4:** `uv run pytest tests/unit/sync -q` → PASS.
- [ ] **Step 5: Commit** `test(fixtures): stub rclone learns lsjson/listremotes, drops env injection`.

### Task 9.2: Integration + e2e + docker `rclone.conf`

**Files:**
- Create: `tests/docker/rclone.conf` (stanzas for the compose SFTP/SMB services)
- Modify: `tests/docker/docker-compose.yml`, `tests/integration/test_nas_sync.py`, `tests/e2e/test_flow_27_nas_credential_settings.py` (→ remote-availability flow), lifecycle flows, `tests/integration/api/test_full_flow.py`

- [ ] TDD/adjust: integration tests construct `Config(nas=NasConfig(remote="nas01", base_root=..., rclone_config_path="tests/docker/rclone.conf"))` and assert push→lsjson-reconcile→cleanup hash-gate end to end against the dockerised servers. Rename the e2e NAS-credential flow to assert the Settings "NAS Remote" section + Test connection. Commit `test: integration + e2e exercise named-remote NAS sync`.

### Task 9.3: Docs

**Files:**
- Create: `docs/setup/rclone-remote-setup.md`
- Modify: `design_specs/design_spec_sections/09_Configuration_File.md`, `07_Sync_and_Database_Integration.md`, `04_Backend_Architecture.md`, `README.md`

- [ ] Write the operator guide: `rclone config` walkthrough for SFTP and SMB, naming the remote to match `nas.remote`, setting `base_root`, the unencrypted-config assumption (encrypted ⇒ operator's own `RCLONE_CONFIG_PASS`), and the tray-app `rclone_config_path` caveat. Update §09 (new `nas:` block, removed `transport:`), §07 (lsjson reconcile + cleanup hash-gate), §04, and README. Commit `docs: rclone.conf setup guide + spec/README updates`.

### Task 9.4: Full-suite + lint/type gate

- [ ] Run: `uv run pytest -q` → all PASS.
- [ ] Run: `uv run ruff check src tests` and `uv run mypy src/exlab_wizard` → clean.
- [ ] Grep clean (removed symbols): `grep -rn "keyring_nas_username\|build_rclone_env\|RCLONE_CONFIG_\|INCOMPLETE_NO_NAS_CREDENTIAL\|SET_NAS_CREDENTIALS\|nas_password_present" src` → no hits.
- [ ] **DRY guard (single rclone-ops surface):** `grep -rnE "run_subprocess|[\"']rclone[\"']" src/exlab_wizard | grep -v "sync/transports/"` → **no hits** (only `sync/transports/` shells out to rclone; every other caller goes through `RcloneDriver`).
- [ ] Commit any final fixups: `chore: lint/type clean-up for rclone.conf migration`.

---

## Self-Review (completed during authoring)

- **Spec coverage:** §1 config (incl. `mtime_tolerance_s`, `--transfers`/`--checkers`) → Phase 1/7/8; §1 DRY ops surface → Tasks 2.1–2.3 + DRY guard in 9.4; §2 orchestrator → Phase 8; §3 driver → Phase 2/7; §4 manifest (size+mtime `matches`) → Phase 3; §5 verify/cleanup flow → Phase 4; §6 credentials/setup → Phase 5/7; §7 UI → Phase 6; §8 docs → Phase 9.3; §9 tests → Phase 9.1/9.2. ✓
- **Placeholder scan:** Tasks 4.3/4.4/5.3/5.4/6.x/8.x give the load-bearing code; a few UI/test tasks describe edits at method granularity with exact symbols rather than full bodies (the bodies are mechanical given the named functions). No "TBD"/"add error handling" left.
- **Type consistency:** `NasConfig.{remote,base_root,rclone_config_path,mtime_tolerance_s,perf,bandwidth}`, `RclonePerf.{transfers,checkers}`, `RcloneDriver(config_path=, transfers=, checkers=)` + `.lsjson()`/`.listremotes()`, `parse_lsjson(raw, strip_prefix=)`, `RemoteManifest.{has,size_matches,matches(rel,size,mtime,*,tolerance_s),_to_epoch}`, `_build_target_for_run(remote=, base_root=, equipment_id=, run=)`, `_target_for_equipment(equipment, run)`, `nas_remote_available(remote)` / `deps.nas_remotes`, `SetupState.INCOMPLETE_NO_NAS_REMOTE`, `SetupNextAction.CONFIGURE_RCLONE_REMOTE` are used consistently across tasks.
- **DRY:** all rclone invocations are `RcloneDriver` methods (`push`/`check`/`about`/`lsjson`/`listremotes`); target strings come from one `_build_target_for_run` + `_target_for_equipment`; both nas-mode and stage-mode (Phase 8) reuse them; guarded by the 9.4 grep.
- **Cross-task dependency:** Task 5.2's `test_paths.py` cases depend on Task 7.1 (nas-mode without transport). Flagged inline.
