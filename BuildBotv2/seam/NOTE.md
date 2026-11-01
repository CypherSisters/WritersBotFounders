# Seam note: lane L3 (Vault) to lane L1 (Steward)

From the L3 security fix pass (verifier items 3 and 4, D27, D41). Lane L1 wires the Steward side.

## What L3 exposes

L1's `core/puffpai_core/seams.py` (on `lane/l1-packages`) looks these up by name. L3 now has all three, as module functions in `core/puffpai_core/zone1/archivist.py`, each on the one Archivist of `paths.state_dir()`:

| Seam name | L3 function | What it does |
|---|---|---|
| `zone1.archivist.lease_for_start(package_id, key_names) -> {name: value}` | `lease_for_start` -> `Archivist.inject` | The package start. **Ticked keys only, always** (vault open or locked). Unticked, missing or unreadable keys are left out, never an error. Each key it returns is a lease held by `pkg:<package_id>`. |
| `zone1.archivist.end_lease(package_id) -> int` | `end_lease` -> `Archivist.end_package` | The package stopped: ends every lease `pkg:<package_id>` holds. Returns how many ended. |
| `zone1.archivist.verify_pin(pin) -> bool` | `verify_pin` -> `Archivist.verify_pin` | Floor-level Yes cards (D29). Throttled with the PIN. Never opens the vault. |

## What Steward MUST do

1. **Call `end_lease(package_id)` on every stop**: a hand stop, a crash, a restart (stop then start), a removal, and Caretaker's restarts. L1's `Steward.stop` already calls `seams.END_LEASE` after `tokens.revoke_package`; keep that on every path that ends a package's process, including the "stopped right after it started" path.
2. Call `lease_for_start` only from the package start, and only with the manifest's approved `needs.keys` names (L1's `_keys` already drops `hand: per_action` keys and walled raw readers).
3. Never call `Archivist.lend(..., purpose="package_env")` or lend to a `pkg:` holder: `lend` refuses both. `package_env` is reachable only through `inject`.

## Honest limits

- A key in a running process's environment can't be pulled back. Ending the lease stops new uses through the Core and clears the Core's copy (best effort, a zeroed `bytearray`); the process keeps its own copy until it stops. Stopping the package is what removes it.
- A hand lock ends every lease, package leases too; it does not stop running packages, so their copies stay until they stop.
- The lease's 3 hour cap ends the Core's record, not the process's copy.
