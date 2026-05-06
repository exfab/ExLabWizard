# 13. Equipment-to-Orchestrator Data Flow

Parent: [[ExLab-Wizard_Design_Spec]]

In orchestrator mode, equipment machines are offline from the NAS and push data to the orchestrator over the local lab network. The orchestrator stages the data, monitors run completeness, syncs the complete run to NAS, then clears the local staging copy.

## 13.1 Topology

```
[Equipment Machine A]  --push-->  [Orchestrator /staging/EQUIP_A/<project>/]
[Equipment Machine B]  --push-->  [Orchestrator /staging/EQUIP_B/<project>/]
                                           |
                                     run complete?
                                           | yes
                                           v
                                    [NAS sync]
                                           |
                                     sync verified?
                                           | yes
                                           v
                                    [Staging cleared]
```

Equipment machines write into a staging area on the orchestrator using either a shared network drive mount or direct file transfer. The orchestrator treats both transports identically once data lands in staging.

## 13.2 Staging Area Layout

```
/staging/
  CONFOCAL_01/                          # equipment (matches final NAS structure)
    PROJ-0042/                          # project
      Run_2026-04-17T14-32-00/          # experimental run
        .exlab-wizard/
          creation.json                 # copied from equipment machine at run start
          ingest.json                   # orchestrator-side lifecycle metadata
          wizard.<hostname>.log
        [data files as they arrive]
      TestRuns/
        TestRun_2026-04-17T09-12-00/    # test run on the same instrument
          .exlab-wizard/
            creation.json               # run_kind: "test"
            ingest.json
            wizard.<hostname>.log
          [data files as they arrive]
```

`creation.json` from the equipment machine is copied into staging at the start of the push, giving the orchestrator full provenance context before any data files arrive. The orchestrator reads `run_kind` from `creation.json` and uses it to: (a) place the staged directory under `TestRuns/` with a `TestRun_<DATE>` leaf when appropriate, and (b) expose the test classification as an attribute on the staging state (the client may flag it visually; see frontend doc). v1 has no per-run LIMS write; `run_kind` flows only into the on-disk `creation.json` and into the orchestrator's in-memory staging-state view. The LIMS-side run record returns in v1.x (see [[07_Sync_and_Database_Integration#7.2 LIMS Integration|§7.2.7]]).

The staging layout mirrors the final NAS structure (equipment-first, with `TestRuns/` parallel to experimental runs and the `TestRun_` leaf prefix preserved) so that sync is a direct subtree copy with no path rewriting.

## 13.3 Run Lifecycle States

The orchestrator tracks each staged run through five states, recorded in `ingest.json`. State transitions are append-only -- each transition adds a timestamped entry rather than overwriting, preserving the full history.

| State | Meaning |
|---|---|
| `staging` | Data is actively being pushed from the equipment machine |
| `complete` | Equipment machine has signaled end of run; all expected files present |
| `sync_queued` | NAS sync job has been submitted |
| `sync_verified` | NAS sync confirmed via checksum; staging eligible for deletion |
| `cleared` | Staging copy deleted; NAS is the only copy |

## 13.4 `ingest.json` Schema

```json
{
  "schema_version": "1.1",
  "project_name": "Cortex Q3 Pilot",
  "equipment_id": "CONFOCAL_01",
  "run_kind": "experimental",
  "run_path": "CONFOCAL_01/PROJ-0042/Run_2026-04-17T14-32-00",
  "transport": "smb_mount",
  "current_state": "sync_verified",
  "history": [
    {
      "state": "staging",
      "at": "2026-04-17T14:35:00Z",
      "host": "labpc-04"
    },
    {
      "state": "complete",
      "at": "2026-04-17T16:12:00Z",
      "host": "labpc-04",
      "files_received": 142,
      "bytes_received": 48293847234
    },
    {
      "state": "sync_queued",
      "at": "2026-04-17T16:12:05Z",
      "host": "labpc-04"
    },
    {
      "state": "sync_verified",
      "at": "2026-04-17T16:18:43Z",
      "host": "labpc-04",
      "nas_path": "//nas01/lab/CONFOCAL_01/PROJ-0042/Run_2026-04-17T14-32-00",
      "checksum_file": ".exlab-wizard/checksums.sha256"
    }
  ]
}
```

For a test run the same structure applies, with `run_kind: "test"` and the `run_path` / `nas_path` containing the `TestRuns/` segment and the `TestRun_` leaf prefix, for example `CONFOCAL_01/PROJ-0042/TestRuns/TestRun_2026-04-17T09-12-00`.

`ingest.json` is written by the orchestrator only and is not present on equipment machines. It syncs to NAS as part of the run directory so the full lifecycle history travels with the data.

## 13.5 Run Completeness Signal

The orchestrator needs to know when a run is finished before triggering NAS sync. Two mechanisms are supported, configurable per equipment:

| Mechanism | How it works | Best for |
|---|---|---|
| **Sentinel file** | Equipment machine writes a `run_complete` marker file into the run directory when acquisition ends | Equipment software that can write arbitrary files |
| **Manifest comparison** | Equipment machine writes a `manifest.json` listing expected files and sizes; orchestrator polls until all are present and sizes match | Equipment with predictable output file sets |

Both mechanisms result in the same state transition: `staging` -> `complete`, after which NAS sync is triggered.

## 13.6 Transport Handling

The two supported transports are configured per equipment in `config.yaml` (see [[09_Configuration_File|Section 9]]). File transfer protocol mechanics (rsync, SFTP, Robocopy, SMB mount) are **outside the app's scope** -- the orchestrator watches the staging directory for incoming files and completeness signals regardless of how they arrived. This decouples the app from transport implementation and avoids duplicating NASSync functionality.

## 13.7 Staging Cleanup

After `sync_verified` is confirmed, the orchestrator deletes the staging directory for that run. Cleanup mode is configurable:

```yaml
orchestrator:
  staging_cleanup:
    mode: "manual"      # or "scheduled"
    retain_hours: 24    # only used if mode is "scheduled"
```

**Manual mode (default for v1):** The operator explicitly initiates deletion of sync-verified runs. The backend exposes an action that lists sync-verified staged runs with sizes and deletes the selected set.

**Scheduled mode:** Verified runs older than `retain_hours` are automatically deleted by a background task.

Deletion is logged to `wizard.<hostname>.log` with file count and bytes freed. Manual mode is the safer default for v1.

## 13.8 Staging State Query (Backend Contract)

The backend exposes a read-only query that enumerates staged runs with:

- Current lifecycle state ([[#13.3 Run Lifecycle States|Section 13.3]])
- File count and total size
- Elapsed time since last activity
- Per-run actions available (force sync, clear if `sync_verified`, view log)

How the client renders this is in `ExLab-Wizard_Frontend_Spec.md`.
