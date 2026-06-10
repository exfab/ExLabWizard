# rsync-over-ssh NAS Transport Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an `rsync_ssh` NAS transport (selected per-instance by `nas.transport`) so cluster machines — where IT blocks SMB and rclone-SFTP but allows rsync-over-ssh — can push, reconcile, verify, and probe against the Synology NAS.

**Architecture:** A new `RsyncSshDriver` mirrors `RcloneDriver`'s thin-subprocess-wrapper shape behind a shared `NasTransportDriver` protocol and a `build_nas_driver` factory. Targets reuse the existing `<remote>:<path>` string (`user@host:/path` is valid rsync syntax), so `NASSyncClient`'s state machine, reconcile loop, and cleanup gate are untouched. All ops use rsync protocol primitives only (no remote command exec): push = `rsync -rt`, manifest = `--list-only` parse, verify = `--checksum` dry-run itemize, probe = non-recursive `--list-only`.

**Spec:** `docs/superpowers/specs/2026-06-10-rsync-ssh-nas-transport-design.md` (all OQs resolved 2026-06-10).

**Tech Stack:** Python 3.12, Pydantic v2, pytest (`asyncio_mode = "auto"`, so `async def` tests need no marker). Test runner: `uv run --extra test pytest`. Lint: `uvx ruff check`. **Run sync test suites one at a time** (they flake under CPU load).

**Out of scope (deliberate):** renaming `SetupState.INCOMPLETE_NO_NAS_REMOTE` / `SetupNextAction.CONFIGURE_RCLONE_REMOTE` (kept; only UI copy changes); the `error_kind=None` → HASH_MISMATCH retry-routing debt; `ui/pages/wizard_equipment.py` copy polish; staging-hop rsync support.

---

## File map

| File | Change |
| --- | --- |
| `src/exlab_wizard/constants/enums.py` | new `SyncTransport` StrEnum |
| `src/exlab_wizard/constants/__init__.py` | export `SyncTransport` |
| `src/exlab_wizard/config/models.py` | `NasConfig.transport/ssh_port/ssh_identity_file` + validator |
| `src/exlab_wizard/sync/manifest.py` | new `parse_rsync_listing()` |
| `src/exlab_wizard/sync/transports/__init__.py` | `NasTransportDriver` protocol, `build_nas_driver` factory |
| `src/exlab_wizard/sync/transports/rclone.py` | new `lsjson_manifest()` shim |
| `src/exlab_wizard/sync/transports/rsync_ssh.py` | **new** `RsyncSshDriver` |
| `src/exlab_wizard/sync/nas_client.py` | factory routing + stage-mode bypass + `lsjson_manifest` |
| `src/exlab_wizard/sync/verifier.py` | protocol typing, drop no-arg default |
| `src/exlab_wizard/tray/dependencies.py` | transport-aware gate hydration + probe |
| `src/exlab_wizard/paths.py` | `_missing_nas_fields` transport branch |
| `src/exlab_wizard/ui/pages/settings.py` | transport-aware NAS-Remote section |
| `src/exlab_wizard/ui/pages/main.py` | transport-neutral banner subline |
| `tests/fixtures/stub_rsync.py` | **new** stub rsync binary |
| `tests/unit/...` | per-task test files (see tasks) |
| `tests/docker/*` | rsync-over-ssh leg + characterization tests |
| `docs/setup/`, `design_specs/design_spec_sections/{04,07,09}_*.md`, `README.md` | docs |

---

### Task 1: `SyncTransport` enum

**Files:**
- Modify: `src/exlab_wizard/constants/enums.py` (after `SyncMode`, ~line 176)
- Modify: `src/exlab_wizard/constants/__init__.py` (import list ~line 38, `__all__` ~line 264)
- Test: `tests/unit/constants/test_enum_literal_alignment.py`, `tests/unit/constants/test_enums.py`

- [ ] **Step 1: Write the failing test**

Add to the `@pytest.mark.parametrize` list in `tests/unit/constants/test_enum_literal_alignment.py` (alongside the `SetupNextAction` entry):

```python
        (enums.SyncTransport, frozenset({"rclone", "rsync_ssh"})),
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra test pytest tests/unit/constants/test_enum_literal_alignment.py -v`
Expected: FAIL with `AttributeError: module 'exlab_wizard.constants.enums' has no attribute 'SyncTransport'`

- [ ] **Step 3: Implement the enum**

In `src/exlab_wizard/constants/enums.py`, directly after the `SyncMode` class:

```python
class SyncTransport(StrEnum):
    """Which binary the NAS sync subsystem shells out to.

    rsync-over-ssh NAS transport design (2026-06-10). Selected per
    instance by ``nas.transport``: ``rclone`` (default) drives the named
    remote in the operator's rclone.conf; ``rsync_ssh`` drives
    ``rsync -e ssh`` against ``nas.remote`` as a ``user@host`` target.
    Stage-mode equipment always uses rclone regardless of this value.
    """

    RCLONE = "rclone"
    RSYNC_SSH = "rsync_ssh"
```

In `src/exlab_wizard/constants/__init__.py`: add `SyncTransport,` to the `from exlab_wizard.constants.enums import (...)` block (alphabetical, next to `SyncMode`) and `"SyncTransport",` to `__all__` (next to `"SyncMode"`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra test pytest tests/unit/constants/ -v`
Expected: PASS (alignment test plus the existing `test_enums.py` suite; if `test_enums.py` asserts a closed member list of the module, add `SyncTransport` there as well — check its failure output).

- [ ] **Step 5: Commit**

```bash
git add src/exlab_wizard/constants/ tests/unit/constants/
git commit -m "feat(constants): add SyncTransport enum for NAS transport selection"
```

---

### Task 2: `NasConfig` transport fields + validator

**Files:**
- Modify: `src/exlab_wizard/config/models.py` (`NasConfig`, ~line 273; import block ~line 36)
- Test: `tests/unit/config/test_models.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/config/test_models.py`:

```python
class TestNasTransport:
    def test_transport_defaults_to_rclone(self) -> None:
        nas = NasConfig()
        assert nas.transport == SyncTransport.RCLONE
        assert nas.ssh_port == 22
        assert nas.ssh_identity_file == ""

    def test_rsync_ssh_accepts_user_at_host(self) -> None:
        nas = NasConfig(
            transport="rsync_ssh",
            remote="svc-sync@nas01.lab.example",
            base_root="/volume1/lab",
            ssh_identity_file="~/.ssh/id_exlab",
        )
        assert nas.transport == SyncTransport.RSYNC_SSH
        assert nas.remote == "svc-sync@nas01.lab.example"

    def test_rsync_ssh_rejects_empty_remote(self) -> None:
        with pytest.raises(ValidationError, match="requires nas.remote"):
            NasConfig(transport="rsync_ssh", remote="")

    @pytest.mark.parametrize("bad", ["nas01", "@nas01", "svc-sync@"])
    def test_rsync_ssh_rejects_non_user_at_host(self, bad: str) -> None:
        with pytest.raises(ValidationError, match="user@host"):
            NasConfig(transport="rsync_ssh", remote=bad)

    def test_rclone_mode_remote_shape_unrestricted(self) -> None:
        assert NasConfig(remote="nas01").remote == "nas01"

    def test_transport_serializes_as_string(self) -> None:
        dumped = NasConfig(
            transport="rsync_ssh", remote="u@h"
        ).model_dump()
        assert dumped["transport"] == "rsync_ssh"
```

Imports needed at top of the test module (add to existing import blocks): `from pydantic import ValidationError` and `from exlab_wizard.constants import SyncTransport` (the module already imports `NasConfig` and `pytest`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra test pytest tests/unit/config/test_models.py -k TestNasTransport -v`
Expected: FAIL with `ValidationError` ("Extra inputs are not permitted" — `extra="forbid"` rejects the unknown `transport` key).

- [ ] **Step 3: Implement the fields**

In `src/exlab_wizard/config/models.py`: add `SyncTransport,` to the existing `from exlab_wizard.constants import (...)` block. Then inside `NasConfig`, after the `rclone_config_path: str = ""` field:

```python
    # rsync-over-ssh NAS transport (2026-06-10 design). ``rsync_ssh`` drives
    # ``rsync -e ssh`` with ``remote`` as a ``user@host`` target prefix;
    # ``rclone_config_path`` and ``perf`` are rclone-only and ignored in
    # rsync mode. Stage-mode equipment always uses rclone regardless.
    transport: SyncTransport = SyncTransport.RCLONE
    ssh_port: int = Field(default=22, ge=1, le=65535)
    ssh_identity_file: str = ""
```

And after the existing fields, the serializer + validator (mirroring `EquipmentConfig._serialize_sync_mode`):

```python
    @field_serializer("transport")
    def _serialize_transport(self, value: SyncTransport) -> str:
        return value.value

    @model_validator(mode="after")
    def _validate_rsync_ssh(self) -> NasConfig:
        if self.transport != SyncTransport.RSYNC_SSH:
            return self
        if not self.remote:
            msg = "nas.transport 'rsync_ssh' requires nas.remote ('user@host')"
            raise ValueError(msg)
        user, sep, host = self.remote.partition("@")
        if not (sep and user and host):
            msg = (
                f"nas.remote {self.remote!r} must be 'user@host' when "
                "nas.transport is 'rsync_ssh'"
            )
            raise ValueError(msg)
        return self
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra test pytest tests/unit/config/test_models.py -v`
Expected: PASS (whole module — confirms no regression in existing `NasConfig` tests).

- [ ] **Step 5: Commit**

```bash
git add src/exlab_wizard/config/models.py tests/unit/config/test_models.py
git commit -m "feat(config): nas.transport selector + ssh fields on NasConfig"
```

---

### Task 3: `parse_rsync_listing` manifest parser

**Files:**
- Modify: `src/exlab_wizard/sync/manifest.py`
- Test: `tests/unit/sync/test_manifest.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/sync/test_manifest.py`:

```python
from datetime import datetime

from exlab_wizard.sync.manifest import parse_rsync_listing


def _stamp(epoch: float) -> str:
    """Format ``epoch`` the way the rsync client formats listing mtimes
    (local timezone, 1 s resolution)."""
    return datetime.fromtimestamp(epoch).strftime("%Y/%m/%d %H:%M:%S")


class TestParseRsyncListing:
    def test_plain_file(self) -> None:
        raw = f"-rw-r--r--          2048 {_stamp(1_750_000_000)} data/reading_001.csv\n"
        manifest = parse_rsync_listing(raw)
        assert manifest.has("data/reading_001.csv")
        assert manifest.entries["data/reading_001.csv"].size == 2048

    def test_filename_with_spaces_survives(self) -> None:
        raw = f"-rw-r--r--          1234 {_stamp(1_750_000_000)} image data 001.tif\n"
        manifest = parse_rsync_listing(raw)
        assert manifest.has("image data 001.tif")

    def test_comma_grouped_size(self) -> None:
        raw = f"-rw-r--r--     1,234,567 {_stamp(1_750_000_000)} big.bin\n"
        assert parse_rsync_listing(raw).entries["big.bin"].size == 1234567

    def test_mtime_roundtrips_through_local_timezone(self) -> None:
        epoch = 1_750_000_000.0
        raw = f"-rw-r--r--           100 {_stamp(epoch)} f.txt\n"
        manifest = parse_rsync_listing(raw)
        assert manifest.matches("f.txt", 100, epoch, tolerance_s=2)
        assert not manifest.matches("f.txt", 100, epoch + 3600, tolerance_s=2)

    def test_directories_and_dot_dropped(self) -> None:
        stamp = _stamp(1_750_000_000)
        raw = (
            f"drwxr-xr-x          4096 {stamp} .\n"
            f"drwxr-xr-x          4096 {stamp} data\n"
            f"-rw-r--r--            10 {stamp} data/x.csv\n"
        )
        manifest = parse_rsync_listing(raw)
        assert set(manifest.entries) == {"data/x.csv"}

    def test_octal_escapes_unescaped(self) -> None:
        raw = f"-rw-r--r--            10 {_stamp(1_750_000_000)} weird\\#012name.txt\n"
        assert parse_rsync_listing(raw).has("weird\nname.txt")

    def test_garbage_lines_ignored(self) -> None:
        assert parse_rsync_listing("sending incremental file list\n\n") .entries == {}

    def test_empty_input(self) -> None:
        assert parse_rsync_listing("").entries == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra test pytest tests/unit/sync/test_manifest.py -k TestParseRsyncListing -v`
Expected: FAIL with `ImportError: cannot import name 'parse_rsync_listing'`

- [ ] **Step 3: Implement the parser**

In `src/exlab_wizard/sync/manifest.py`: extend the imports (`from datetime import UTC, datetime`), add `"parse_rsync_listing"` to `__all__`, and append:

```python
# ``rsync --list-only`` line: perms, size (possibly digit-grouped), the
# fixed-format timestamp, then EVERYTHING after the single separating
# space is the path — filenames containing spaces appear literally
# (spec-review blocker, 2026-06-10), so the line must be anchored on the
# timestamp, never whitespace-split.
_RSYNC_LIST_RE = re.compile(
    r"^(?P<perms>\S+)\s+(?P<size>[\d,.]+)\s+"
    r"(?P<date>\d{4}/\d{2}/\d{2})\s+(?P<time>\d{2}:\d{2}:\d{2})\s(?P<path>.+)$"
)

# rsync escapes unusual bytes in listings as ``\#ooo`` (3 octal digits).
_RSYNC_ESCAPE_RE = re.compile(r"\\#([0-7]{3})")


def _unescape_rsync_path(path: str) -> str:
    """Decode rsync's ``\\#ooo`` octal escapes back into the real bytes."""
    if "\\#" not in path:
        return path
    out = bytearray()
    idx = 0
    for match in _RSYNC_ESCAPE_RE.finditer(path):
        out += path[idx : match.start()].encode("utf-8")
        out.append(int(match.group(1), 8))
        idx = match.end()
    out += path[idx:].encode("utf-8")
    return out.decode("utf-8", errors="replace")


def parse_rsync_listing(raw: str) -> RemoteManifest:
    """Parse ``rsync --list-only -r`` output into a :class:`RemoteManifest`.

    rsync-over-ssh NAS transport (2026-06-10). Paths are already relative
    to the listed target, so there is no ``strip_prefix``. Only regular
    files (``-`` perm prefix) are kept — directories (including the ``.``
    top entry) and symlinks are dropped, matching :func:`parse_lsjson`.
    The listing timestamp has 1 s resolution and is formatted in the
    local timezone of the process that renders the file list; it is
    parsed as local time and stored as an RFC3339 UTC string so
    :meth:`RemoteManifest.matches` works identically for both transports.
    Unparseable lines are skipped (a listing we can't parse must not
    crash the sync worker).
    """
    out: dict[str, RemoteEntry] = {}
    for raw_line in raw.splitlines():
        match = _RSYNC_LIST_RE.match(raw_line.rstrip())
        if match is None:
            continue
        if not match.group("perms").startswith("-"):
            continue
        path = _unescape_rsync_path(match.group("path"))
        if not path or path == ".":
            continue
        try:
            size = int(re.sub(r"[,.]", "", match.group("size")))
        except ValueError:
            continue
        try:
            local_dt = datetime.strptime(  # noqa: DTZ007 -- naive-as-local is the point
                f"{match.group('date')} {match.group('time')}", "%Y/%m/%d %H:%M:%S"
            )
        except ValueError:
            continue
        mod_time = local_dt.astimezone(UTC).isoformat().replace("+00:00", "Z")
        out[path] = RemoteEntry(size=size, mod_time=mod_time, is_dir=False)
    return RemoteManifest(entries=out)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra test pytest tests/unit/sync/test_manifest.py -v`
Expected: PASS (new class plus all pre-existing lsjson tests).

- [ ] **Step 5: Commit**

```bash
git add src/exlab_wizard/sync/manifest.py tests/unit/sync/test_manifest.py
git commit -m "feat(sync): parse rsync --list-only output into RemoteManifest"
```

---

### Task 4: `NasTransportDriver` protocol + `RcloneDriver.lsjson_manifest`

**Files:**
- Modify: `src/exlab_wizard/sync/transports/__init__.py`
- Modify: `src/exlab_wizard/sync/transports/rclone.py`
- Test: `tests/unit/sync/test_transports.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/sync/test_transports.py`:

```python
async def test_rclone_lsjson_manifest_parses_and_strips(monkeypatch) -> None:
    from exlab_wizard.sync.transports.rclone import RcloneDriver

    raw = (
        '[{"Path": "base/EQ1/Run_1/a.csv", "Size": 10,'
        ' "ModTime": "2026-06-10T00:00:00Z", "IsDir": false}]'
    )

    async def fake_lsjson(self, remote, *, recursive=True):
        return raw

    monkeypatch.setattr(RcloneDriver, "lsjson", fake_lsjson)
    manifest = await RcloneDriver().lsjson_manifest(
        "nas01:/base/EQ1/Run_1", strip_prefix="base/EQ1/Run_1"
    )
    assert manifest.has("a.csv")


def test_rclone_driver_satisfies_protocol() -> None:
    from exlab_wizard.sync.transports import NasTransportDriver
    from exlab_wizard.sync.transports.rclone import RcloneDriver

    driver: NasTransportDriver = RcloneDriver()
    assert driver is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra test pytest tests/unit/sync/test_transports.py -k "lsjson_manifest or protocol" -v`
Expected: FAIL (`AttributeError: lsjson_manifest` / `ImportError: NasTransportDriver`).

- [ ] **Step 3: Implement**

In `src/exlab_wizard/sync/transports/__init__.py`: extend the module imports and `__all__`:

```python
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from pathlib import Path

    from exlab_wizard.sync.manifest import RemoteManifest
    from exlab_wizard.sync.transports.rclone import AboutResult, CheckResult

__all__ = [
    "NasTransportDriver",
    "TransportError",
    "TransportErrorKind",
    "TransportResult",
]
```

and append after `TransportError`:

```python
class NasTransportDriver(Protocol):
    """The four network ops the NAS sync subsystem consumes.

    rsync-over-ssh NAS transport (2026-06-10). Implemented by
    :class:`~exlab_wizard.sync.transports.rclone.RcloneDriver` and
    :class:`~exlab_wizard.sync.transports.rsync_ssh.RsyncSshDriver`.
    ``listremotes`` is deliberately NOT part of this protocol — it is
    rclone.conf introspection, used only by the rclone-mode setup gate.
    """

    async def push(
        self,
        local: Path,
        remote: str,
        *,
        bwlimit_kibps: int | None = None,
        files_from: Path | None = None,
    ) -> TransportResult: ...

    async def check(
        self, local: Path, remote: str, *, files_from: Path
    ) -> CheckResult: ...

    async def lsjson_manifest(
        self, remote: str, *, strip_prefix: str = ""
    ) -> RemoteManifest: ...

    async def about(self, remote: str) -> AboutResult: ...
```

In `src/exlab_wizard/sync/transports/rclone.py`, add to `RcloneDriver` after `lsjson`:

```python
    async def lsjson_manifest(self, remote: str, *, strip_prefix: str = "") -> RemoteManifest:
        """Run :meth:`lsjson` and parse it into a :class:`RemoteManifest`.

        The raw-JSON wire shape is rclone-specific, so parsing lives
        behind the driver boundary (the shared
        :class:`~exlab_wizard.sync.transports.NasTransportDriver`
        protocol returns manifests, never raw text).
        """
        from exlab_wizard.sync.manifest import parse_lsjson

        raw = await self.lsjson(remote)
        return parse_lsjson(raw, strip_prefix=strip_prefix)
```

with a `TYPE_CHECKING` import for `RemoteManifest` at the top of `rclone.py`:

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from exlab_wizard.sync.manifest import RemoteManifest
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra test pytest tests/unit/sync/test_transports.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/exlab_wizard/sync/transports/ tests/unit/sync/test_transports.py
git commit -m "feat(sync): NasTransportDriver protocol + RcloneDriver.lsjson_manifest"
```

---

### Task 5: `RsyncSshDriver` — classifier, ssh args, `push`

**Files:**
- Create: `src/exlab_wizard/sync/transports/rsync_ssh.py`
- Test: `tests/unit/sync/transports/test_rsync_ssh.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/sync/transports/test_rsync_ssh.py`:

```python
"""Unit tests for the rsync-over-ssh transport driver.

rsync-over-ssh NAS transport design (2026-06-10). ``run_subprocess`` is
monkeypatched so no real binary is spawned; the docker integration leg
covers the real wire path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from exlab_wizard.sync.transports import TransportError, TransportErrorKind
from exlab_wizard.sync.transports.rsync_ssh import (
    RsyncSshDriver,
    _classify_failure,
)

TARGET = "svc-sync@nas01:/volume1/lab/EQ1/Run_1"


class FakeRun:
    """Recordable stand-in for ``run_subprocess``."""

    def __init__(self, rc: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.rc, self.stdout, self.stderr = rc, stdout, stderr
        self.cmds: list[list[str]] = []

    async def __call__(self, cmd: list[str]) -> tuple[int, str, str]:
        self.cmds.append(cmd)
        return self.rc, self.stdout, self.stderr


@pytest.fixture
def fake_run(monkeypatch):
    def _install(rc: int = 0, stdout: str = "", stderr: str = "") -> FakeRun:
        fake = FakeRun(rc, stdout, stderr)
        monkeypatch.setattr(
            "exlab_wizard.sync.transports.rsync_ssh.run_subprocess", fake
        )
        return fake

    return _install


class TestClassifyFailure:
    @pytest.mark.parametrize(
        "stderr",
        [
            "svc-sync@nas01: Permission denied (publickey,password).",
            "Host key verification failed.",
            "Too many authentication failures",
        ],
    )
    def test_auth_markers(self, stderr: str) -> None:
        assert _classify_failure(stderr, 255) == TransportErrorKind.AUTH

    def test_ssh_failure_without_auth_marker_is_network(self) -> None:
        assert (
            _classify_failure("ssh: connect to host nas01: Connection refused", 255)
            == TransportErrorKind.NETWORK
        )

    @pytest.mark.parametrize("rc", [5, 10, 12, 23, 24, 30, 35])
    def test_protocol_and_partial_codes_are_network(self, rc: int) -> None:
        assert _classify_failure("", rc) == TransportErrorKind.NETWORK

    def test_other_nonzero_is_unknown(self) -> None:
        assert _classify_failure("rsync: syntax error", 1) == TransportErrorKind.UNKNOWN


class TestPush:
    async def test_success_argv_shape(self, fake_run, tmp_path: Path) -> None:
        run = fake_run(rc=0)
        driver = RsyncSshDriver(ssh_port=2222, ssh_identity_file="/keys/id_exlab")
        result = await driver.push(tmp_path, TARGET, bwlimit_kibps=512)
        assert result.ok
        cmd = run.cmds[0]
        assert cmd[0] == "rsync"
        assert "-rt" in cmd
        assert "--bwlimit=512" in cmd
        assert cmd[-2] == f"{tmp_path}/"
        assert cmd[-1] == TARGET
        ssh_arg = cmd[cmd.index("-e") + 1]
        assert "-p 2222" in ssh_arg
        assert "-i /keys/id_exlab" in ssh_arg
        assert "BatchMode=yes" in ssh_arg

    async def test_files_from_forwarded(self, fake_run, tmp_path: Path) -> None:
        run = fake_run(rc=0)
        listing = tmp_path / "files.txt"
        listing.write_text("a.csv\n", encoding="utf-8")
        await RsyncSshDriver().push(tmp_path, TARGET, files_from=listing)
        assert f"--files-from={listing}" in run.cmds[0]

    async def test_no_bwlimit_flag_when_unset(self, fake_run, tmp_path: Path) -> None:
        run = fake_run(rc=0)
        await RsyncSshDriver().push(tmp_path, TARGET)
        assert not any(arg.startswith("--bwlimit") for arg in run.cmds[0])

    async def test_auth_failure_classified(self, fake_run, tmp_path: Path) -> None:
        fake_run(rc=255, stderr="Permission denied (publickey).")
        result = await RsyncSshDriver().push(tmp_path, TARGET)
        assert not result.ok
        assert result.error_kind == TransportErrorKind.AUTH

    async def test_missing_binary_raises_transport_error(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        async def boom(cmd):
            raise FileNotFoundError(cmd[0])

        monkeypatch.setattr(
            "exlab_wizard.sync.transports.rsync_ssh.run_subprocess", boom
        )
        with pytest.raises(TransportError) as excinfo:
            await RsyncSshDriver().push(tmp_path, TARGET)
        assert excinfo.value.error_kind is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra test pytest tests/unit/sync/transports/test_rsync_ssh.py -v`
Expected: FAIL with `ModuleNotFoundError: ... rsync_ssh`

- [ ] **Step 3: Implement the driver skeleton + push**

Create `src/exlab_wizard/sync/transports/rsync_ssh.py`:

```python
"""rsync-over-ssh transport driver.

rsync-over-ssh NAS transport design (2026-06-10). Mirrors
:class:`~exlab_wizard.sync.transports.rclone.RcloneDriver`'s
thin-wrapper philosophy: build an argv, hand it to
:func:`~exlab_wizard.sync.transports._run.run_subprocess`, classify the
outcome. Every op is built from rsync protocol primitives only — the
NAS allowlists exactly the ``rsync --server`` ssh channel (no other
remote command execution, no interactive login, no SFTP), so there is
no ``ssh host df`` / ``find`` / ``sha256sum`` anywhere in this module.

Targets are full ``user@host:/path`` strings composed by the caller
(the same ``<remote>:<path>`` shape the rclone driver receives).
Credentials are ssh keys only (``BatchMode=yes``); host keys are
pre-provisioned in ``known_hosts`` via ``ssh-keyscan`` during setup.
"""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import TYPE_CHECKING

from exlab_wizard.logging import get_logger
from exlab_wizard.sync.manifest import parse_rsync_listing
from exlab_wizard.sync.transports import (
    TransportError,
    TransportErrorKind,
    TransportResult,
)
from exlab_wizard.sync.transports._run import run_subprocess
from exlab_wizard.sync.transports.rclone import AboutResult, CheckResult

if TYPE_CHECKING:
    from exlab_wizard.sync.manifest import RemoteManifest

__all__ = ["RsyncSshDriver"]

_log = get_logger(__name__)


# ssh-level failures that are configuration problems, not transient
# network errors. Matched case-insensitively against stderr.
_AUTH_FAILURE_MARKERS: tuple[str, ...] = (
    "permission denied",
    "host key verification failed",
    "too many authentication failures",
    "no supported authentication",
)

# rsync exit codes that are transient / retryable: 5 client-server
# protocol start, 10 socket I/O, 12 protocol data stream, 23/24 partial
# transfer (incl. vanished source files), 30 timeout, 35 daemon-connect
# timeout. 255 is the ssh transport itself failing.
_NETWORK_RETURNCODES: frozenset[int] = frozenset({5, 10, 12, 23, 24, 30, 35, 255})


def _classify_failure(stderr: str, returncode: int) -> TransportErrorKind:
    """Map a (stderr, returncode) into a :class:`TransportErrorKind`."""
    lowered = stderr.lower()
    if any(marker in lowered for marker in _AUTH_FAILURE_MARKERS):
        return TransportErrorKind.AUTH
    if returncode in _NETWORK_RETURNCODES:
        return TransportErrorKind.NETWORK
    return TransportErrorKind.UNKNOWN


class RsyncSshDriver:
    """rsync-over-ssh transport driver (implements ``NasTransportDriver``)."""

    def __init__(
        self,
        *,
        binary: str = "rsync",
        ssh_port: int = 22,
        ssh_identity_file: str = "",
    ) -> None:
        self._binary = binary
        self._ssh_port = ssh_port
        self._ssh_identity_file = ssh_identity_file

    def _ssh_command(self) -> str:
        """Render the ``-e`` remote-shell string.

        ``BatchMode=yes`` is mandatory: an unknown host key or a key
        passphrase prompt must fail fast (classified AUTH) rather than
        hang the sync worker. No ``StrictHostKeyChecking`` relaxation —
        host keys are pre-provisioned via ``ssh-keyscan``.
        """
        parts = ["ssh", "-p", str(self._ssh_port), "-o", "BatchMode=yes"]
        if self._ssh_identity_file:
            parts.extend(["-i", str(Path(self._ssh_identity_file).expanduser())])
        return shlex.join(parts)

    async def push(
        self,
        local: Path,
        remote: str,
        *,
        bwlimit_kibps: int | None = None,
        files_from: Path | None = None,
    ) -> TransportResult:
        """Run ``rsync -rt`` from ``local`` to ``remote``.

        ``-t`` is mandatory — the manifest reconcile tolerance and the
        quiescence poller both depend on mtime preservation. The
        trailing slash on the source copies the *contents* of ``local``
        (matching ``rclone copy`` semantics). No ``--checksum`` on push:
        integrity is owned by the verify path, not the transfer.
        """
        cmd: list[str] = [self._binary, "-rt", "-e", self._ssh_command()]
        if bwlimit_kibps is not None and bwlimit_kibps > 0:
            cmd.append(f"--bwlimit={bwlimit_kibps}")
        if files_from is not None:
            cmd.append(f"--files-from={files_from}")
        cmd.extend([f"{local}/", remote])
        _log.debug("rsync cmd: %s", shlex.join(cmd))

        try:
            rc, stdout, stderr = await run_subprocess(cmd)
        except FileNotFoundError as exc:
            msg = f"rsync binary not found: {self._binary!r}"
            raise TransportError(msg) from exc

        if rc == 0:
            return TransportResult(ok=True, returncode=0, stdout=stdout, stderr=stderr)
        kind = _classify_failure(stderr, rc)
        _log.warning("rsync push failed rc=%d kind=%s", rc, kind.value)
        return TransportResult(
            ok=False, error_kind=kind, stderr=stderr, stdout=stdout, returncode=rc
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra test pytest tests/unit/sync/transports/test_rsync_ssh.py -v`
Expected: PASS (classifier + push tests; check/manifest/about tests come next).

- [ ] **Step 5: Commit**

```bash
git add src/exlab_wizard/sync/transports/rsync_ssh.py tests/unit/sync/transports/test_rsync_ssh.py
git commit -m "feat(sync): RsyncSshDriver skeleton — classifier, ssh args, push"
```

---

### Task 6: `RsyncSshDriver.check` (itemize dry-run verify)

**Files:**
- Modify: `src/exlab_wizard/sync/transports/rsync_ssh.py`
- Test: `tests/unit/sync/transports/test_rsync_ssh.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/sync/transports/test_rsync_ssh.py`:

```python
def _files_from(tmp_path: Path, names: list[str]) -> Path:
    listing = tmp_path / "files-from.txt"
    listing.write_text("".join(f"{n}\n" for n in names), encoding="utf-8")
    return listing


class TestCheck:
    async def test_all_equal_when_nothing_itemized(self, fake_run, tmp_path: Path) -> None:
        run = fake_run(rc=0, stdout="")
        listing = _files_from(tmp_path, ["a.csv", "sub/b.csv"])
        result = await RsyncSshDriver().check(tmp_path, TARGET, files_from=listing)
        assert set(result.equal) == {"a.csv", "sub/b.csv"}
        assert result.differ == () and result.missing_on_dst == ()
        cmd = run.cmds[0]
        assert "--checksum" in cmd
        assert "-rni" in cmd  # dry-run + itemize

    async def test_checksum_differ_flag(self, fake_run, tmp_path: Path) -> None:
        fake_run(rc=0, stdout=">fcst...... a.csv\n")
        listing = _files_from(tmp_path, ["a.csv", "b.csv"])
        result = await RsyncSshDriver().check(tmp_path, TARGET, files_from=listing)
        assert result.differ == ("a.csv",)
        assert result.equal == ("b.csv",)

    async def test_all_plus_means_missing_on_dst(self, fake_run, tmp_path: Path) -> None:
        fake_run(rc=0, stdout=">f+++++++++ new file.csv\n")
        listing = _files_from(tmp_path, ["new file.csv"])
        result = await RsyncSshDriver().check(tmp_path, TARGET, files_from=listing)
        assert result.missing_on_dst == ("new file.csv",)
        assert result.equal == ()

    async def test_short_openrsync_flags_handled(self, fake_run, tmp_path: Path) -> None:
        # openrsync (macOS dev machines) emits 7-char itemize tails.
        fake_run(rc=0, stdout=">f+++++++ x.csv\n")
        listing = _files_from(tmp_path, ["x.csv"])
        result = await RsyncSshDriver().check(tmp_path, TARGET, files_from=listing)
        assert result.missing_on_dst == ("x.csv",)

    async def test_attr_only_lines_count_as_equal(self, fake_run, tmp_path: Path) -> None:
        # '.' update-type = no transfer needed; content equal under --checksum.
        fake_run(rc=0, stdout=".f..t...... a.csv\n")
        listing = _files_from(tmp_path, ["a.csv"])
        result = await RsyncSshDriver().check(tmp_path, TARGET, files_from=listing)
        assert result.equal == ("a.csv",)

    async def test_directory_lines_ignored(self, fake_run, tmp_path: Path) -> None:
        fake_run(rc=0, stdout="cd+++++++++ sub/\n>fcst...... sub/b.csv\n")
        listing = _files_from(tmp_path, ["sub/b.csv"])
        result = await RsyncSshDriver().check(tmp_path, TARGET, files_from=listing)
        assert result.differ == ("sub/b.csv",)

    async def test_nonzero_rc_raises_classified(self, fake_run, tmp_path: Path) -> None:
        fake_run(rc=255, stderr="Permission denied (publickey).")
        listing = _files_from(tmp_path, ["a.csv"])
        with pytest.raises(TransportError) as excinfo:
            await RsyncSshDriver().check(tmp_path, TARGET, files_from=listing)
        assert excinfo.value.error_kind == TransportErrorKind.AUTH
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra test pytest tests/unit/sync/transports/test_rsync_ssh.py -k TestCheck -v`
Expected: FAIL with `AttributeError: ... 'check'`

- [ ] **Step 3: Implement `check`**

Append to `RsyncSshDriver` in `rsync_ssh.py`:

```python
    async def check(
        self, local: Path, remote: str, *, files_from: Path
    ) -> CheckResult:
        """Content verify via ``rsync -rni --checksum`` dry-run (resolved OQ-1).

        The remote rsync hashes whole files inside the rsync protocol
        (the same allowlisted channel as push — no separate remote
        command). Itemized output is synthesized into a
        :class:`CheckResult`: checksum-differ flag → ``differ``, all-+
        creation → ``missing_on_dst``, and ``equal`` is reconstructed as
        the ``files_from`` list minus the itemized paths (matching files
        are simply omitted from dry-run output). ``extra_on_dst`` is
        always ``()`` — a push-direction dry-run cannot see remote-only
        files; harmless, it never flips ``VerifyResult.ok``.

        Raises :class:`TransportError` with a classified ``error_kind``
        on any non-zero exit (rsync dry-runs exit 0 even when files
        differ; non-zero means the run itself failed).
        """
        cmd: list[str] = [
            self._binary,
            "-rni",
            "--checksum",
            "-e",
            self._ssh_command(),
            f"--files-from={files_from}",
            f"{local}/",
            remote,
        ]
        _log.debug("rsync check cmd: %s", shlex.join(cmd))
        try:
            rc, stdout, stderr = await run_subprocess(cmd)
        except FileNotFoundError as exc:
            msg = f"rsync binary not found: {self._binary!r}"
            raise TransportError(msg) from exc
        if rc != 0:
            kind = _classify_failure(stderr, rc)
            msg = f"rsync check failed rc={rc} kind={kind.value}: {stderr.strip()}"
            raise TransportError(msg, error_kind=kind)

        try:
            requested = tuple(
                line.strip()
                for line in files_from.read_text(encoding="utf-8").splitlines()
                if line.strip()
            )
        except OSError:
            requested = ()
        return _synthesize_check(requested, stdout)
```

and the module-level synthesizer (after `_classify_failure`):

```python
def _synthesize_check(requested: tuple[str, ...], stdout: str) -> CheckResult:
    """Translate dry-run itemized output into a :class:`CheckResult`.

    Itemize lines are ``YXcstpoguax <path>`` — an update-type char, a
    file-type char, then attribute flags. The attribute tail is 9 chars
    in rsync 3.x and shorter in openrsync, so flags are read
    positionally from the front, never by total length. Only
    regular-file lines (``f`` at index 1) participate; ``.``-type lines
    (no transfer needed) and unmatched lines fall through to ``equal``.
    """
    differ: list[str] = []
    missing: list[str] = []
    flagged: set[str] = set()
    for raw_line in stdout.splitlines():
        line = raw_line.rstrip()
        flags, sep, path = line.partition(" ")
        path = path.strip()
        if not sep or not path or len(flags) < 3:
            continue
        if flags[0] not in "<>ch." or flags[1] != "f":
            continue
        tail = flags[2:]
        if tail and all(ch == "+" for ch in tail):
            missing.append(path)
            flagged.add(path)
        elif flags[2] == "c":
            differ.append(path)
            flagged.add(path)
        # any other combination (e.g. ``.f..t......``) is an attr-only
        # change: content equal under --checksum -> credited via `equal`.
    equal = tuple(rel for rel in requested if rel not in flagged)
    return CheckResult(
        equal=equal, differ=tuple(differ), missing_on_dst=tuple(missing)
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra test pytest tests/unit/sync/transports/test_rsync_ssh.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/exlab_wizard/sync/transports/rsync_ssh.py tests/unit/sync/transports/test_rsync_ssh.py
git commit -m "feat(sync): RsyncSshDriver.check via --checksum dry-run itemize"
```

---

### Task 7: `RsyncSshDriver.lsjson_manifest` + `about`

**Files:**
- Modify: `src/exlab_wizard/sync/transports/rsync_ssh.py`
- Test: `tests/unit/sync/transports/test_rsync_ssh.py`

- [ ] **Step 1: Write the failing tests**

Append:

```python
class TestLsjsonManifest:
    async def test_listing_parsed_and_prefix_ignored(self, fake_run, tmp_path: Path) -> None:
        from datetime import datetime

        stamp = datetime.fromtimestamp(1_750_000_000).strftime("%Y/%m/%d %H:%M:%S")
        run = fake_run(
            rc=0,
            stdout=(
                f"drwxr-xr-x          4096 {stamp} .\n"
                f"-rw-r--r--          2048 {stamp} data file.csv\n"
            ),
        )
        manifest = await RsyncSshDriver().lsjson_manifest(
            TARGET, strip_prefix="volume1/lab/EQ1/Run_1"
        )
        assert set(manifest.entries) == {"data file.csv"}
        cmd = run.cmds[0]
        assert "--list-only" in cmd and "-r" in cmd and "--no-h" in cmd
        assert cmd[-1] == f"{TARGET}/"

    async def test_failure_raises_classified(self, fake_run) -> None:
        fake_run(rc=255, stderr="ssh: connect to host nas01: Connection refused")
        with pytest.raises(TransportError) as excinfo:
            await RsyncSshDriver().lsjson_manifest(TARGET)
        assert excinfo.value.error_kind == TransportErrorKind.NETWORK


class TestAbout:
    async def test_reachable_returns_ok_empty_info(self, fake_run) -> None:
        run = fake_run(rc=0, stdout="drwxr-xr-x 4096 2026/06/10 00:00:00 .\n")
        result = await RsyncSshDriver().about("svc-sync@nas01:/volume1/lab")
        assert result.ok and result.info == {}
        assert "-r" not in run.cmds[0]  # non-recursive probe

    async def test_missing_base_root_is_config_reason(self, fake_run) -> None:
        fake_run(rc=23, stdout="", stderr='rsync: change_dir "/volume1/lab" failed')
        result = await RsyncSshDriver().about("svc-sync@nas01:/volume1/lab")
        assert not result.ok
        assert result.reason is not None
        assert "base_root" in result.reason

    async def test_auth_failure_reason(self, fake_run) -> None:
        fake_run(rc=255, stderr="Permission denied (publickey).")
        result = await RsyncSshDriver().about("svc-sync@nas01:/volume1/lab")
        assert not result.ok
        assert result.reason is not None and result.reason.startswith("auth")

    async def test_missing_binary_reason(self, monkeypatch) -> None:
        async def boom(cmd):
            raise FileNotFoundError(cmd[0])

        monkeypatch.setattr(
            "exlab_wizard.sync.transports.rsync_ssh.run_subprocess", boom
        )
        result = await RsyncSshDriver().about("svc-sync@nas01:/volume1/lab")
        assert not result.ok and result.reason == "rsync binary not found"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra test pytest tests/unit/sync/transports/test_rsync_ssh.py -k "TestLsjsonManifest or TestAbout" -v`
Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Implement**

Append to `RsyncSshDriver`:

```python
    async def lsjson_manifest(
        self, remote: str, *, strip_prefix: str = ""
    ) -> RemoteManifest:
        """List the remote subtree via ``--list-only -r`` (resolved OQ-2).

        ``strip_prefix`` is accepted for protocol parity but ignored —
        rsync listings are already relative to the listed target (the
        rclone lsjson path is run-rooted and needs stripping; this one
        is not). Raises :class:`TransportError` with a classified
        ``error_kind`` on non-zero exit, mirroring
        :meth:`RcloneDriver.lsjson`.
        """
        del strip_prefix
        cmd: list[str] = [
            self._binary,
            "--list-only",
            "-r",
            "--no-h",
            "-e",
            self._ssh_command(),
            f"{remote}/",
        ]
        _log.debug("rsync list cmd: %s", shlex.join(cmd))
        try:
            rc, stdout, stderr = await run_subprocess(cmd)
        except FileNotFoundError as exc:
            msg = f"rsync binary not found: {self._binary!r}"
            raise TransportError(msg) from exc
        if rc != 0:
            kind = _classify_failure(stderr, rc)
            msg = f"rsync list failed rc={rc} kind={kind.value}: {stderr.strip()}"
            raise TransportError(msg, error_kind=kind)
        return parse_rsync_listing(stdout)

    async def about(self, remote: str) -> AboutResult:
        """Reachability + auth probe: non-recursive ``--list-only`` of ``remote``.

        Free-space info is unobtainable without remote command exec
        (which IT blocks), so ``info`` is always empty — the Settings
        panel already tolerates that (resolved OQ-5). rc 23 with no
        listing output means the path itself is absent: surfaced as a
        configuration reason, not a retryable network error
        (spec-review carve-out).
        """
        cmd: list[str] = [
            self._binary,
            "--list-only",
            "--no-h",
            "-e",
            self._ssh_command(),
            f"{remote}/",
        ]
        _log.debug("rsync about cmd: %s", shlex.join(cmd))
        try:
            rc, stdout, stderr = await run_subprocess(cmd)
        except FileNotFoundError:
            return AboutResult(ok=False, reason="rsync binary not found")
        if rc == 0:
            return AboutResult(ok=True, info={})
        if rc == 23 and not stdout.strip():
            return AboutResult(
                ok=False, reason="base_root not found or inaccessible on the NAS"
            )
        kind = _classify_failure(stderr, rc)
        return AboutResult(ok=False, reason=f"{kind.value}: {stderr.strip()}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra test pytest tests/unit/sync/transports/test_rsync_ssh.py -v`
Expected: PASS (whole module). Then lint: `uvx ruff check src/exlab_wizard/sync/transports/rsync_ssh.py` — fix anything it flags.

- [ ] **Step 5: Commit**

```bash
git add src/exlab_wizard/sync/transports/rsync_ssh.py tests/unit/sync/transports/test_rsync_ssh.py
git commit -m "feat(sync): RsyncSshDriver listing manifest + reachability probe"
```

---

### Task 8: `build_nas_driver` factory

**Files:**
- Modify: `src/exlab_wizard/sync/transports/__init__.py`
- Test: `tests/unit/sync/test_transports.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/sync/test_transports.py`:

```python
class TestBuildNasDriver:
    def test_rclone_default(self) -> None:
        from exlab_wizard.config.models import NasConfig, RclonePerf
        from exlab_wizard.sync.transports import build_nas_driver
        from exlab_wizard.sync.transports.rclone import RcloneDriver

        driver = build_nas_driver(NasConfig(remote="nas01"), RclonePerf())
        assert isinstance(driver, RcloneDriver)

    def test_rsync_ssh_selected(self) -> None:
        from exlab_wizard.config.models import NasConfig, RclonePerf
        from exlab_wizard.sync.transports import build_nas_driver
        from exlab_wizard.sync.transports.rsync_ssh import RsyncSshDriver

        nas = NasConfig(
            transport="rsync_ssh",
            remote="svc-sync@nas01",
            ssh_port=2222,
            ssh_identity_file="~/.ssh/id_exlab",
        )
        driver = build_nas_driver(nas, RclonePerf())
        assert isinstance(driver, RsyncSshDriver)
        assert driver._ssh_port == 2222
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra test pytest tests/unit/sync/test_transports.py -k TestBuildNasDriver -v`
Expected: FAIL with `ImportError: cannot import name 'build_nas_driver'`

- [ ] **Step 3: Implement the factory**

In `src/exlab_wizard/sync/transports/__init__.py`: add `"build_nas_driver",` to `__all__`, extend the `TYPE_CHECKING` block with `from exlab_wizard.config.models import NasConfig, RclonePerf`, and append:

```python
def build_nas_driver(nas: NasConfig, perf: RclonePerf) -> NasTransportDriver:
    """Construct the NAS transport driver selected by ``nas.transport``.

    The single factory every construction site routes through
    (``nas_client``, the tray probe/gate hydration, test helpers).
    Imports are local so importing this package stays cheap and free of
    cycles (the driver modules import this package's DTOs).

    NOTE: this selects for **nas-mode** equipment only. Stage-mode
    equipment always uses a directly-constructed ``RcloneDriver`` (its
    targets are rclone named remotes) — see
    ``NASSyncClient._driver_for_equipment``.
    """
    from exlab_wizard.constants import SyncTransport

    if nas.transport == SyncTransport.RSYNC_SSH:
        from exlab_wizard.sync.transports.rsync_ssh import RsyncSshDriver

        return RsyncSshDriver(
            ssh_port=nas.ssh_port,
            ssh_identity_file=nas.ssh_identity_file,
        )
    from exlab_wizard.sync.transports.rclone import RcloneDriver

    return RcloneDriver(
        config_path=nas.rclone_config_path or None,
        transfers=perf.transfers,
        checkers=perf.checkers,
    )
```

- [ ] **Step 4: Run tests + verify no stray constructions**

Run: `uv run --extra test pytest tests/unit/sync/test_transports.py -v` — Expected: PASS.
Run: `grep -rn "RcloneDriver(\|RsyncSshDriver(" src/exlab_wizard | grep -v "sync/transports/"` — Expected (for now): hits in `nas_client.py` and `tray/dependencies.py` only; Tasks 9 and 11 remove them.

- [ ] **Step 5: Commit**

```bash
git add src/exlab_wizard/sync/transports/__init__.py tests/unit/sync/test_transports.py
git commit -m "feat(sync): build_nas_driver transport factory"
```

---

### Task 9: `NASSyncClient` wiring (factory + stage-mode bypass + manifest op)

**Files:**
- Modify: `src/exlab_wizard/sync/nas_client.py` (imports ~lines 44-57; delete `_build_driver` ~line 132; `_driver_for_equipment` ~line 729; `_build_lsjson` ~line 786)
- Test: `tests/unit/sync/test_nas_client.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/sync/test_nas_client.py` (it already imports `Config`, `EquipmentConfig`, `NasConfig`, `OrchestratorConfig`, `PathsConfig`, `SyncMode`, and constructs `NASSyncClient` — follow `_client_for_target_test` at ~line 774 for the constructor shape):

```python
class TestDriverSelection:
    # _client_for_target_test (defined ~line 774 of this module) already
    # builds a NASSyncClient from a bare Config — reuse it.

    def test_nas_mode_rsync_transport_selected(self, tmp_path: Path) -> None:
        from exlab_wizard.sync.transports.rsync_ssh import RsyncSshDriver

        config = Config(
            paths=PathsConfig(app_root=str(tmp_path)),
            nas=NasConfig(
                transport="rsync_ssh", remote="svc-sync@nas01", base_root="/volume1/lab"
            ),
            equipment=[EquipmentConfig(id="EQ1", label="Eq 1", nas_root="/nas")],
        )
        client = _client_for_target_test(config, tmp_path)
        driver = client._driver_for_equipment(config.equipment[0])
        assert isinstance(driver, RsyncSshDriver)

    def test_stage_mode_always_rclone_even_with_rsync_transport(
        self, tmp_path: Path
    ) -> None:
        from exlab_wizard.sync.transports.rclone import RcloneDriver

        config = Config(
            paths=PathsConfig(app_root=str(tmp_path)),
            nas=NasConfig(
                transport="rsync_ssh", remote="svc-sync@nas01", base_root="/volume1/lab"
            ),
            orchestrator=OrchestratorConfig(
                label="WS1", staging_remote="stagepc", staging_base_root="/staging"
            ),
            equipment=[
                EquipmentConfig(
                    id="EQ1", label="Eq 1", nas_root="/nas", sync_mode=SyncMode.STAGE
                )
            ],
        )
        client = _client_for_target_test(config, tmp_path)
        driver = client._driver_for_equipment(config.equipment[0])
        assert isinstance(driver, RcloneDriver)
```

(If `OrchestratorConfig` requires other fields for stage mode, follow the existing stage-mode test at ~line 785 of the same file for the exact constructor arguments.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra test pytest tests/unit/sync/test_nas_client.py -k TestDriverSelection -v`
Expected: FAIL — `test_nas_mode_rsync_transport_selected` gets an `RcloneDriver`.

- [ ] **Step 3: Rewire the client**

In `src/exlab_wizard/sync/nas_client.py`:

1. Imports: change `from exlab_wizard.sync.manifest import RemoteManifest, parse_lsjson` → `from exlab_wizard.sync.manifest import RemoteManifest`; change the transports import block to:

```python
from exlab_wizard.sync.transports import (
    NasTransportDriver,
    TransportError,
    TransportErrorKind,
    TransportResult,
    build_nas_driver,
)
from exlab_wizard.sync.transports.rclone import RcloneDriver
```

2. Delete the module-level `_build_driver` function (~lines 132-150) entirely. Run `grep -rn "_build_driver" src tests` — update/remove any other references (expected: none outside `nas_client.py`).

3. Replace `_driver_for_equipment` (~line 729):

```python
    def _driver_for_equipment(self, equipment: EquipmentConfig) -> NasTransportDriver:
        """Build the transport driver for ``equipment``.

        ``nas.transport`` selects the driver **only for nas-mode
        equipment**. Stage-mode targets are rclone named-remote strings
        (``<staging_remote>:<path>``), so stage-mode always gets a
        directly-constructed :class:`RcloneDriver` regardless of
        ``nas.transport`` — handing an rsync driver a staging target
        would attempt ssh to a host named after the staging remote
        (spec-review blocker, 2026-06-10). The hidden staging backend
        must keep working (see CLAUDE.md).
        """
        resolution = self._resolve_remote(equipment)
        if equipment.sync_mode == SyncMode.STAGE:
            return RcloneDriver(
                config_path=self._config.nas.rclone_config_path or None,
                transfers=resolution.perf.transfers,
                checkers=resolution.perf.checkers,
            )
        return build_nas_driver(self._config.nas, resolution.perf)
```

4. In `_build_lsjson` (~line 786), replace the closure body:

```python
        async def _lsjson(run: Path) -> RemoteManifest:
            target = self._target_for_equipment(equipment, run)
            prefix = _remote_subpath(base_root, equipment.id, run)
            return await driver.lsjson_manifest(target, strip_prefix=prefix)
```

(the surrounding factory-injection guard and `base_root` capture are unchanged).

- [ ] **Step 4: Run the sync suites (one at a time — they flake under CPU load)**

```
uv run --extra test pytest tests/unit/sync/test_nas_client.py -v
uv run --extra test pytest tests/unit/sync/test_nas_client_extra.py -v
uv run --extra test pytest tests/unit/sync/test_nas_client_stability.py -v
uv run --extra test pytest tests/unit/sync/test_transports.py tests/unit/sync/test_manifest.py -v
```
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/exlab_wizard/sync/nas_client.py tests/unit/sync/test_nas_client.py
git commit -m "feat(sync): route NASSyncClient through build_nas_driver with stage-mode rclone bypass"
```

---

### Task 10: `Verifier` protocol typing

**Files:**
- Modify: `src/exlab_wizard/sync/verifier.py` (~lines 33-34, 93-107)

- [ ] **Step 1: Make the change** (no construction sites exist for `Verifier()` — verified during spec review — so this is a typing-only change covered by the existing suite)

In `verifier.py`: change the `TYPE_CHECKING` import to:

```python
if TYPE_CHECKING:
    from exlab_wizard.sync.transports import NasTransportDriver
    from exlab_wizard.sync.transports.rclone import CheckResult
```

and replace `Verifier.__init__`:

```python
    def __init__(self, driver: NasTransportDriver) -> None:
        self._driver = driver
```

Update the class docstring's first line to "Constructed with a :class:`NasTransportDriver`; …" and delete the no-arg default branch. Update the module docstring's first sentence to note the verifier wraps the transport's ``check`` op (rclone `check --download` or the rsync `--checksum` dry-run).

- [ ] **Step 2: Verify**

Run: `uv run --extra test pytest tests/unit/sync/ -v --ignore=tests/unit/sync/test_nas_client.py --ignore=tests/unit/sync/test_nas_client_extra.py --ignore=tests/unit/sync/test_nas_client_stability.py`
Expected: PASS. Then `grep -rn "Verifier(" src tests | grep -v "def \|class "` — expected: no no-arg constructions.

- [ ] **Step 3: Commit**

```bash
git add src/exlab_wizard/sync/verifier.py
git commit -m "refactor(sync): Verifier takes NasTransportDriver, drop vestigial no-arg default"
```

---

### Task 11: tray gate hydration + Test-connection probe

**Files:**
- Modify: `src/exlab_wizard/tray/dependencies.py` (~lines 148-149, 622-679)
- Test: `tests/unit/tray/test_dependencies.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/tray/test_dependencies.py` (follow the module's existing config-stub pattern; if it builds real `Config` objects, reuse that, otherwise these `SimpleNamespace` stubs match the `getattr`-based access):

```python
class TestRsyncSshGate:
    def _nas(self, **kw):
        from exlab_wizard.config.models import NasConfig

        return NasConfig(
            transport="rsync_ssh", remote="svc-sync@nas01", base_root="/v1/lab", **kw
        )

    def test_hydrate_never_spawns_rclone_in_rsync_mode(self, monkeypatch) -> None:
        from types import SimpleNamespace

        from exlab_wizard.tray.dependencies import _hydrate_nas_remotes

        def boom(*a, **k):
            raise AssertionError("rclone must not be constructed in rsync mode")

        monkeypatch.setattr(
            "exlab_wizard.sync.transports.rclone.RcloneDriver", boom
        )
        config = SimpleNamespace(nas=self._nas())
        assert _hydrate_nas_remotes(config) == ()

    def test_predicate_true_when_identity_unset(self) -> None:
        from types import SimpleNamespace

        from exlab_wizard.tray.dependencies import _nas_available_predicate

        config = SimpleNamespace(nas=self._nas())
        predicate = _nas_available_predicate(config, ())
        assert predicate("svc-sync@nas01") is True

    def test_predicate_false_when_identity_missing(self, tmp_path) -> None:
        from types import SimpleNamespace

        from exlab_wizard.tray.dependencies import _nas_available_predicate

        config = SimpleNamespace(
            nas=self._nas(ssh_identity_file=str(tmp_path / "nope_key"))
        )
        predicate = _nas_available_predicate(config, ())
        assert predicate("svc-sync@nas01") is False

    def test_predicate_true_when_identity_exists(self, tmp_path) -> None:
        from types import SimpleNamespace

        from exlab_wizard.tray.dependencies import _nas_available_predicate

        key = tmp_path / "id_exlab"
        key.write_text("KEY", encoding="utf-8")
        config = SimpleNamespace(nas=self._nas(ssh_identity_file=str(key)))
        assert _nas_available_predicate(config, ())("svc-sync@nas01") is True

    def test_rclone_predicate_unchanged(self) -> None:
        from types import SimpleNamespace

        from exlab_wizard.config.models import NasConfig
        from exlab_wizard.tray.dependencies import _nas_available_predicate

        config = SimpleNamespace(nas=NasConfig(remote="nas01"))
        predicate = _nas_available_predicate(config, ("nas01:",))
        assert predicate("nas01") is True
        assert predicate("other") is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra test pytest tests/unit/tray/test_dependencies.py -k TestRsyncSshGate -v`
Expected: FAIL with `ImportError: cannot import name '_nas_available_predicate'`

- [ ] **Step 3: Implement**

In `src/exlab_wizard/tray/dependencies.py`:

1. `_hydrate_nas_remotes` (~line 622) — insert the rsync bypass after the `nas is None` guard:

```python
    from exlab_wizard.constants import SyncTransport

    if getattr(nas, "transport", SyncTransport.RCLONE) == SyncTransport.RSYNC_SSH:
        # rsync-over-ssh transport (2026-06-10): rclone.conf is irrelevant
        # and the rclone binary is likely absent on a cluster node — never
        # spawn it; the gate uses the static ssh predicate instead.
        return ()
```

2. Add the predicate helper directly below `_hydrate_nas_remotes`:

```python
def _nas_available_predicate(config: Any, remotes: tuple[str, ...]) -> Any:
    """Build the setup-gate ``nas_remote_available`` callable by transport.

    rclone mode keeps the historical "name present in the listremotes
    snapshot" check. rsync_ssh mode is a static, offline predicate:
    remote non-empty, and when ``ssh_identity_file`` is set the file
    must exist. Network reachability stays behind Test-connection.
    """
    from exlab_wizard.constants import SyncTransport

    nas = getattr(config, "nas", None)
    if nas is not None and getattr(nas, "transport", None) == SyncTransport.RSYNC_SSH:

        def _rsync_available(remote: str) -> bool:
            if not remote:
                return False
            identity = str(getattr(nas, "ssh_identity_file", "") or "")
            if identity and not Path(identity).expanduser().exists():
                return False
            return True

        return _rsync_available
    return lambda remote: f"{remote}:" in remotes
```

3. Replace the hydration line ~149 (`deps.nas_remote_available = lambda remote: f"{remote}:" in deps.nas_remotes`) with:

```python
    deps.nas_remote_available = _nas_available_predicate(deps.config, deps.nas_remotes)
```

4. In `_make_equipment_probe` (~line 659), replace the driver construction and the `about` call:

```python
        from exlab_wizard.sync.transports import build_nas_driver

        driver = build_nas_driver(nas, nas.perf)
        target = _probe_target(nas)
        started = time.monotonic()
        try:
            about = await driver.about(target)
```

and add the target helper below `_nas_available_predicate`:

```python
def _probe_target(nas: Any) -> str:
    """Compose the Test-connection probe target by transport.

    rclone probes the bare remote root (``<remote>:``); rsync probes the
    base root (``user@host:/<base_root>``) so a missing/locked path
    surfaces as the configuration error it is.
    """
    from exlab_wizard.constants import SyncTransport

    if getattr(nas, "transport", None) == SyncTransport.RSYNC_SSH:
        base = str(getattr(nas, "base_root", "") or "").strip("/")
        return f"{nas.remote}:/{base}" if base else f"{nas.remote}:/"
    return f"{nas.remote}:"
```

Confirm `Path` is already imported at the top of `dependencies.py` (it is — `from pathlib import Path` per `state_dir` handling); add it if not. Update `_make_equipment_probe`'s docstring to mention both transports.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra test pytest tests/unit/tray/test_dependencies.py -v`
Expected: PASS (new class + all pre-existing probe/hydration tests — the rclone path is behavior-identical).

- [ ] **Step 5: Commit**

```bash
git add src/exlab_wizard/tray/dependencies.py tests/unit/tray/test_dependencies.py
git commit -m "feat(tray): transport-aware NAS setup gate + Test-connection probe"
```

---

### Task 12: `paths._missing_nas_fields` transport branch

**Files:**
- Modify: `src/exlab_wizard/paths.py` (~line 699)
- Test: `tests/unit/test_paths.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_paths.py` (reuse the module's existing minimal-config builder for `INCOMPLETE_NO_NAS_REMOTE` cases; the assertions below are the contract):

```python
def test_missing_nas_fields_rsync_mode_names_identity_file() -> None:
    from exlab_wizard.config.models import Config, EquipmentConfig, NasConfig, PathsConfig
    from exlab_wizard.constants import SetupState
    from exlab_wizard.paths import setup_state_missing

    config = Config(
        paths=PathsConfig(app_root="/tmp/x"),
        nas=NasConfig(transport="rsync_ssh", remote="svc-sync@nas01"),
        equipment=[EquipmentConfig(id="EQ1", label="Eq 1", nas_root="/nas")],
    )
    rows = setup_state_missing(SetupState.INCOMPLETE_NO_NAS_REMOTE, config)
    assert rows == [
        {"field": "nas.ssh_identity_file", "reason": "identity_file_missing"}
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra test pytest tests/unit/test_paths.py -k identity_file -v`
Expected: FAIL (reason is `not_found_in_rclone_conf`).

- [ ] **Step 3: Implement**

Replace `_missing_nas_fields` in `paths.py`:

```python
def _missing_nas_fields(config: Config | None) -> list[dict[str, str]]:
    """Missing-field row(s) for ``INCOMPLETE_NO_NAS_REMOTE``.

    Distinguishes an unset ``nas.remote`` (shared by both transports)
    from the per-transport "configured but unavailable" reasons: the
    named remote absent from rclone.conf, or (rsync_ssh) the pinned
    ssh identity file not found on disk.
    """
    if config is None or not config.nas.remote:
        return [{"field": "nas.remote", "reason": "unset"}]
    from exlab_wizard.constants import SyncTransport

    if config.nas.transport == SyncTransport.RSYNC_SSH:
        return [
            {"field": "nas.ssh_identity_file", "reason": "identity_file_missing"}
        ]
    return [{"field": "nas.remote", "reason": "not_found_in_rclone_conf"}]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra test pytest tests/unit/test_paths.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/exlab_wizard/paths.py tests/unit/test_paths.py
git commit -m "feat(paths): rsync-mode missing-field reason for the NAS setup gate"
```

---

### Task 13: UI — Settings NAS-Remote section + main-page banner copy

**Files:**
- Modify: `src/exlab_wizard/ui/pages/settings.py` (NAS-remote section, ~lines 780-845)
- Modify: `src/exlab_wizard/ui/pages/main.py` (~line 172)
- Test: `tests/unit/ui/test_settings_nas_remote.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/ui/test_settings_nas_remote.py`, following that module's existing render-and-query pattern for the section under test (it already renders the section with a `NasConfig`-like stub and asserts on `data-testid` labels — mirror the closest existing test for setup):

```python
def test_rsync_mode_shows_transport_row_and_ssh_badge(...existing fixture args...):
    # nas stub: transport="rsync_ssh", remote="svc-sync@nas01",
    # base_root="/volume1/lab"; nas_remote_available returns True.
    # Assert: a label with data-testid="settings-nas-transport" exists with
    # text "rsync over ssh"; the status badge text is "ssh access configured".
    ...


def test_rsync_mode_identity_missing_badge(...existing fixture args...):
    # Same stub but nas_remote_available returns False.
    # Assert badge text == "Identity file missing — see setup docs".
    ...


def test_rclone_mode_badges_unchanged(...existing fixture args...):
    # transport absent/"rclone": badge stays "Found in rclone.conf" /
    # "Not found — run `rclone config`" (regression pin).
    ...
```

Write these as real tests by copying the closest existing test in the module and changing the stub + assertions — the module's helpers dictate the exact render mechanics.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra test pytest tests/unit/ui/test_settings_nas_remote.py -v`
Expected: new tests FAIL (no transport row; rclone badge text rendered).

- [ ] **Step 3: Implement the section branch**

In `settings.py`'s NAS-remote section function, after `base_root = ...`:

```python
    transport = str(getattr(nas, "transport", "") or "rclone")
    rsync_mode = transport == "rsync_ssh"
```

Replace the static description label text with:

```python
        description = (
            "NAS sync uses rsync over ssh: `remote` is the user@host target, "
            "authenticated by your ssh key (no password is stored here). "
            "See the setup docs for key + known_hosts provisioning."
            if rsync_mode
            else "NAS sync references a single rclone remote configured in your "
            "rclone.conf (run `rclone config` to create it). No password is "
            "stored here."
        )
        ui.label(description).style(
            "font-size: var(--text-sm); color: var(--color-muted);"
        )
```

After the Base-root row, add a Transport row:

```python
        with ui.row().classes("items-center w-full").style("gap: 0.5rem;"):
            ui.label("Transport").style("color: var(--color-body); min-width: 6rem;")
            ui.label("rsync over ssh" if rsync_mode else "rclone").props(
                'data-testid="settings-nas-transport"'
            ).style("font-family: var(--font-mono);")
```

Replace the badge branch:

```python
        if rsync_mode:
            badge_text = (
                "ssh access configured"
                if available
                else "Identity file missing — see setup docs"
            )
        else:
            badge_text = (
                "Found in rclone.conf" if available else "Not found — run `rclone config`"
            )
        badge_color = "var(--color-success)" if available else "var(--color-warning)"
```

In `main.py` ~line 172, change the banner subline value:

```python
        "configure_rclone_remote": ("Configure the NAS connection (see setup docs) to begin."),
```

Then run `grep -rn "Configure the rclone remote" src tests` and update every assertion site (expect hits in `tests/unit/ui/test_pages.py` and possibly `tests/e2e/ux_catalog.py`; if the e2e UX doc is generated, regenerate it the way commit `c8c3349` did — check `git show c8c3349 --stat` for the command/files).

- [ ] **Step 4: Run tests to verify they pass**

```
uv run --extra test pytest tests/unit/ui/test_settings_nas_remote.py -v
uv run --extra test pytest tests/unit/ui/test_pages.py tests/unit/ui/test_mount.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/exlab_wizard/ui/ tests/unit/ui/ tests/e2e/
git commit -m "feat(ui): transport-aware NAS-remote settings section + banner copy"
```

---

### Task 14: `stub_rsync` fixture + driver-against-stub test

**Files:**
- Create: `tests/fixtures/stub_rsync.py`
- Test: `tests/unit/sync/transports/test_rsync_ssh.py` (append)

- [ ] **Step 1: Create the stub**

Create `tests/fixtures/stub_rsync.py` (sibling of `stub_rclone.py`; same env-selected-behavior pattern):

```python
#!/usr/bin/env python3
"""Stub ``rsync`` binary for tests.

rsync-over-ssh NAS transport (2026-06-10). Behaviors are selected via
``STUB_RSYNC_BEHAVIOR``:

- ``success`` (default): push copies the ``--files-from`` subset (or the
  whole source tree) into ``STUB_RSYNC_DEST_ROOT/<remote-path>``;
  ``--list-only`` emits a real listing of that subtree; ``-n`` dry-runs
  emit nothing (all equal).
- ``network_error`` — "Connection refused" on stderr, exit 255.
- ``auth_error`` — "Permission denied (publickey)." on stderr, exit 255.
- ``check_differ`` — dry-run itemizes every files-from entry as
  ``>fcst...... <path>``.
- ``check_missing`` — dry-run itemizes every entry as ``>f+++++++++ <path>``.

``STUB_RSYNC_RECORD_PATH`` appends one JSON line of argv per invocation.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path


def _flag_value(argv: list[str], prefix: str) -> str | None:
    for arg in argv:
        if arg.startswith(prefix + "="):
            return arg.split("=", 1)[1]
    return None


def _positional(argv: list[str]) -> list[str]:
    out: list[str] = []
    skip_next = False
    for arg in argv[1:]:
        if skip_next:
            skip_next = False
            continue
        if arg == "-e":
            skip_next = True
            continue
        if arg.startswith("-"):
            continue
        out.append(arg)
    return out


def _files_from_paths(argv: list[str]) -> list[str]:
    listing = _flag_value(argv, "--files-from")
    if listing is None:
        return []
    try:
        return [
            line.strip()
            for line in Path(listing).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except OSError:
        return []


def _remote_path(spec: str) -> str:
    # "user@host:/path" -> "/path"
    return spec.split(":", 1)[1] if ":" in spec else spec


def main() -> int:
    argv = sys.argv
    record = os.environ.get("STUB_RSYNC_RECORD_PATH")
    if record:
        with open(record, "a") as f:
            f.write(json.dumps(argv) + "\n")

    behavior = os.environ.get("STUB_RSYNC_BEHAVIOR", "success")
    dest_root = os.environ.get("STUB_RSYNC_DEST_ROOT", "")

    if behavior == "network_error":
        sys.stderr.write("ssh: connect to host nas01: Connection refused\n")
        return 255
    if behavior == "auth_error":
        sys.stderr.write("Permission denied (publickey).\n")
        return 255

    positional = _positional(argv)

    # ---- list-only --------------------------------------------------------
    if "--list-only" in argv:
        target = _remote_path(positional[-1]).rstrip("/")
        root = Path(dest_root) / target.lstrip("/")
        if not root.is_dir():
            sys.stderr.write(f'rsync: change_dir "{target}" failed\n')
            return 23
        recursive = "-r" in argv
        paths = sorted(root.rglob("*")) if recursive else sorted(root.iterdir())
        for path in [root, *paths]:
            st = path.stat()
            stamp = datetime.fromtimestamp(st.st_mtime).strftime("%Y/%m/%d %H:%M:%S")
            rel = "." if path == root else path.relative_to(root).as_posix()
            kind = "d" if path.is_dir() else "-"
            sys.stdout.write(f"{kind}rw-r--r-- {st.st_size:>14,} {stamp} {rel}\n")
        return 0

    # ---- dry-run check ----------------------------------------------------
    if "-n" in argv or any("n" in a for a in argv if a.startswith("-") and "=" not in a):
        rels = _files_from_paths(argv)
        if behavior == "check_differ":
            for rel in rels:
                sys.stdout.write(f">fcst...... {rel}\n")
        elif behavior == "check_missing":
            for rel in rels:
                sys.stdout.write(f">f+++++++++ {rel}\n")
        return 0

    # ---- push -------------------------------------------------------------
    if len(positional) < 2:
        sys.stderr.write(f"stub_rsync: unexpected argv {argv!r}\n")
        return 1
    src = Path(positional[-2].rstrip("/"))
    if not dest_root:
        return 0
    dst = Path(dest_root) / _remote_path(positional[-1]).lstrip("/")
    rels = _files_from_paths(argv)
    if rels:
        for rel in rels:
            source = src / rel
            if not source.is_file():
                continue
            target = dst / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    else:
        dst.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dst, dirs_exist_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Write the round-trip test**

Append to `tests/unit/sync/transports/test_rsync_ssh.py`:

```python
import os
import stat
import sys


@pytest.fixture
def stub_rsync(tmp_path: Path, monkeypatch) -> Path:
    """Executable wrapper around tests/fixtures/stub_rsync.py."""
    fixture = Path(__file__).parents[3] / "fixtures" / "stub_rsync.py"
    wrapper = tmp_path / "rsync"
    wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{fixture}" "$@"\n')
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("STUB_RSYNC_DEST_ROOT", str(tmp_path / "nas"))
    return wrapper


class TestAgainstStub:
    async def test_push_then_list_roundtrip(self, stub_rsync: Path, tmp_path: Path) -> None:
        local = tmp_path / "run"
        (local / "data").mkdir(parents=True)
        (local / "data" / "file with spaces.csv").write_text("x" * 10)
        driver = RsyncSshDriver(binary=str(stub_rsync))

        result = await driver.push(local, "u@h:/v1/lab/EQ1/run")
        assert result.ok

        manifest = await driver.lsjson_manifest("u@h:/v1/lab/EQ1/run")
        assert manifest.has("data/file with spaces.csv")
        local_stat = (local / "data" / "file with spaces.csv").stat()
        assert manifest.matches(
            "data/file with spaces.csv",
            local_stat.st_size,
            local_stat.st_mtime,
            tolerance_s=2,
        )

    async def test_about_missing_base_root(self, stub_rsync: Path) -> None:
        driver = RsyncSshDriver(binary=str(stub_rsync))
        result = await driver.about("u@h:/does/not/exist")
        assert not result.ok and "base_root" in (result.reason or "")
```

- [ ] **Step 3: Run tests to verify they pass**

Run: `uv run --extra test pytest tests/unit/sync/transports/test_rsync_ssh.py -v`
Expected: PASS (the stub's listing format must satisfy the Task 3 parser — comma-grouped sizes, spaces in names, `.` top entry dropped; if a format detail mismatches, fix the stub, not the parser).

- [ ] **Step 4: Commit**

```bash
git add tests/fixtures/stub_rsync.py tests/unit/sync/transports/test_rsync_ssh.py
git commit -m "test(sync): stub rsync binary + driver round-trip coverage"
```

---

### Task 15: docker rsync-over-ssh leg + characterization tests

**Files:**
- Modify: `tests/docker/Dockerfile`, `tests/docker/entrypoint.sh`, `tests/docker/docker-compose.yml`, `tests/docker/README.md`
- Create: `tests/integration/test_rsync_ssh_characterization.py`

This task pins the empirical questions the unit stubs cannot answer: **whose timezone formats `--list-only` output**, real `--files-from` trailing-slash semantics, real itemize flag strings, and rc behavior on missing paths — against a genuine rsync+sshd, key-auth, no-SFTP, no-shell posture mirroring the Synology.

- [ ] **Step 1: Extend the SFTP container for rsync-over-ssh**

In `tests/docker/Dockerfile`: add `rsync` to the `apt-get install` list. In `tests/docker/entrypoint.sh`: append provisioning for a key-only rsync user (generate `/keys/id_exlab` if absent, install `authorized_keys` with `restrict,command="rsync --server ..."`-style forced command is NOT used — Synology allows plain `rsync --server`; instead set the user's shell to `/bin/sh` but `Match User` in `sshd_config` with `ForceCommand` left unset and `Subsystem sftp` removed for that user, mirroring "rsync works, sftp does not"). Set the container `TZ=America/New_York` in `docker-compose.yml` (`environment:` of the sshd service) so it differs from typical client TZ. Document the new user/keys in `tests/docker/README.md`. Follow the existing entrypoint's user-provisioning style exactly — read it first; it already creates the SFTP user.

- [ ] **Step 2: Write the characterization tests**

Create `tests/integration/test_rsync_ssh_characterization.py`, gated exactly like `tests/integration/test_nas_sync.py` (open that file and copy its docker-availability skip marker/fixtures verbatim). Tests, each driving the real `RsyncSshDriver` at the container:

```python
async def test_push_list_reconcile_roundtrip(...):
    # push a run dir containing "file with spaces.tif" and a nested dir;
    # lsjson_manifest must return matches() == True for every file with
    # tolerance_s=2 — THIS is the timezone characterization: it fails if
    # --list-only formats in the server's TZ (container TZ is pinned
    # non-UTC) and passes if it formats in the client's.

async def test_check_differ_and_missing(...):
    # corrupt one remote file's bytes (same size, same mtime, via the
    # bind-mounted nas-data volume), delete another; check() must report
    # exactly {corrupt} differ and {deleted} missing_on_dst.

async def test_list_only_on_missing_run_dir_raises_network(...):
    # lsjson_manifest on a nonexistent run dir -> TransportError NETWORK.

async def test_about_ok_and_missing_base_root(...):
    # about(base_root) ok; about(bogus path) reason mentions base_root.
```

Fill in the bodies using the same container connection constants the existing integration test uses (host port, user, key path from `tests/docker/.env`/compose).

**If the timezone round-trip test FAILS** (meaning the listing is server-TZ-formatted): change `parse_rsync_listing` to accept a `tz_offset_s` parameter threaded from a new optional `nas.remote_tz` config field, and record the finding in the spec's risk section. Do not silently widen the tolerance.

- [ ] **Step 3: Run**

Run: `docker compose -f tests/docker/docker-compose.yml up -d --build`, then
`uv run --extra test pytest tests/integration/test_rsync_ssh_characterization.py -v`
Expected: PASS (or the documented tz contingency above).

- [ ] **Step 4: Commit**

```bash
git add tests/docker/ tests/integration/test_rsync_ssh_characterization.py
git commit -m "test(integration): rsync-over-ssh docker leg + characterization suite"
```

---

### Task 16: Documentation

**Files:**
- Create: `docs/setup/rsync-ssh-setup.md`
- Modify: `docs/setup/rclone-remote-setup.md` (cross-link), `design_specs/design_spec_sections/09_Configuration_File.md` (new `nas.transport`/`ssh_port`/`ssh_identity_file` fields), `design_specs/design_spec_sections/07_Sync_and_Database_Integration.md` (transport matrix + verify-authority note), `design_specs/design_spec_sections/04_Backend_Architecture.md` (driver protocol + factory), `README.md` (transport mention)

- [ ] **Step 1: Write `docs/setup/rsync-ssh-setup.md`** covering, with exact commands: generating a dedicated keypair (`ssh-keygen -t ed25519 -f ~/.ssh/id_exlab -N ""`), installing the public key on the Synology service account, host-key pre-provisioning (`ssh-keyscan -p <port> nas01.lab.example >> ~/.ssh/known_hosts`) **with the note that no ssh login permission is needed** (key exchange precedes auth) and an out-of-band fingerprint check against DSM (`ssh-keygen -lf <(ssh-keyscan -p <port> nas01 2>/dev/null)`), the `config.yaml` block (copy from the spec's "New (cluster instance…)" example), the degraded Test-connection (reachable-only, no free space), recording `rsync --version` from the NAS in the runbook, and the BatchMode failure modes table (Permission denied → key not installed; Host key verification failed → run ssh-keyscan; Connection refused/timeout → network/IT).

- [ ] **Step 2: Update the four design-spec/docs files** per the spec's §6 — each is a focused diff: config table row additions (09), a two-row transport matrix + "verify authority differs by transport" paragraph (07), the protocol/factory sentence (04), one README line.

- [ ] **Step 3: Commit**

```bash
git add docs/ design_specs/ README.md
git commit -m "docs: rsync-over-ssh NAS transport setup walkthrough + spec sections"
```

---

### Task 17: Final validation sweep

- [ ] **Step 1: Lint everything**

Run: `uvx ruff check src tests` and `uvx ruff format --check src tests`
Expected: clean (fix anything flagged).

- [ ] **Step 2: Full unit suites, sync suites one at a time** (flake note in memory):

```
uv run --extra test pytest tests/unit/constants tests/unit/config -v
uv run --extra test pytest tests/unit/sync/test_manifest.py tests/unit/sync/test_transports.py -v
uv run --extra test pytest tests/unit/sync/transports -v
uv run --extra test pytest tests/unit/sync/test_nas_client.py -v
uv run --extra test pytest tests/unit/sync/test_nas_client_extra.py -v
uv run --extra test pytest tests/unit/sync/test_nas_client_stability.py -v
uv run --extra test pytest tests/unit/sync/test_cleanup.py tests/unit/sync/test_queue.py -v
uv run --extra test pytest tests/unit/tray tests/unit/test_paths.py tests/unit/api -v
uv run --extra test pytest tests/unit/ui -v
```
Expected: all PASS.

- [ ] **Step 3: e2e** — `uv run --extra test pytest tests/e2e -v` (expect the lifecycle + settings flows green; fix copy assertions if Task 13's grep missed one).

- [ ] **Step 4: Spec/grep invariants**

```
grep -rn "RcloneDriver(\|RsyncSshDriver(" src/exlab_wizard | grep -v "sync/transports/\|nas_client.py"
```
Expected: no output (`nas_client.py` keeps one documented stage-mode construction).

- [ ] **Step 5: Update the spec status line** in `docs/superpowers/specs/2026-06-10-rsync-ssh-nas-transport-design.md` to `Status: Implemented (plan: docs/superpowers/plans/2026-06-10-rsync-ssh-nas-transport.md)`, commit:

```bash
git add docs/superpowers/specs/2026-06-10-rsync-ssh-nas-transport-design.md
git commit -m "docs(spec): mark rsync-over-ssh transport spec implemented"
```
