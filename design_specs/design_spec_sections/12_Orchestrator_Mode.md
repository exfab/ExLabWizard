# 12. Orchestrator Mode (Multi-Equipment Workstations)

Parent: [[ExLab-Wizard_Design_Spec]]

Some lab computers manage multiple pieces of equipment simultaneously. The app supports this through an explicit orchestrator mode that is identifiable in logs and DB records, while keeping the core creation flow unchanged.

## 12.1 What Changes in Orchestrator Mode

| Concern | Single-equipment workstation | Orchestrator |
|---|---|---|
| Equipment scope | One equipment ID configured | Multiple equipment IDs; selected per creation session |
| Concurrent runs | Not expected | Supported; separate concurrent creation sessions per equipment |
| `created_by` in logs and DB | OS username | OS username + `orchestrator:<hostname>` tag |
| `wizard.<hostname>.log` | One active log per directory level | One log per `<equipment>/<project>/` pair; all written by the same hostname |
| DB record | Standard fields | Additional `orchestrator_host` field populated |
| Staging pipeline | Not used | Active; runs land in `/staging/` before NAS sync ([[13_Equipment_to_Orchestrator_Data_Flow|Section 13]]) |

Client-side presentation differences (equipment selector, staging panel) are specified in `ExLab-Wizard_Frontend_Spec.md`. The backend differences are captured above.

## 12.2 Configuration

See [[09_Configuration_File|Section 9]] for the full `config.yaml` structure including per-equipment `local_root`, `nas_root`, transport, and completeness signal fields.

## 12.3 Concurrent Run Handling

Per-machine, per-equipment log files (`wizard.<hostname>.log`) already isolate concurrent writes by equipment root -- no additional locking is required. NASSync jobs are scoped per equipment root and queued independently, consistent with the existing NASSync architecture.

## 12.4 Logging and DB Changes

`creation.json` gains an `orchestrator` block when orchestrator mode is active (absent, not null, in single-equipment mode -- see [[11_Cache_Folders#11.3 `creation.json` Schema|Section 11.3]]).

The DB record gains `orchestrator_host` (nullable string), set to `<label>/<hostname>` in orchestrator mode. This allows LIMS queries across all equipment types for a given orchestrator workstation.

Log entries gain an `[equip:]` tag (see [[11_Cache_Folders#11.5 `wizard.<hostname>.log` Format|Section 11.5]]), which is essential for distinguishing concurrent activity from the same host across different equipment roots when logs are aggregated.
