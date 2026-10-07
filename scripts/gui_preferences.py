"""Expose no reasoning in the desktop picker; run with local GUIs closed."""
import argparse
import json
from pathlib import Path

# Defaults in the pinned vendor frontends. Keep their other choices available.
DEFAULT_EFFORTS = ["low", "medium", "high", "xhigh", "ultra", "persistent"]
ATOM_STATE = "electron-persisted-atom-state"
EFFORTS = "enabled-reasoning-efforts"


def enable_no_reasoning(existing: str) -> str:
    state = json.loads(existing)
    if not isinstance(state, dict):
        raise ValueError("Desktop state must be an object")
    atoms = state.setdefault(ATOM_STATE, {})
    if not isinstance(atoms, dict):
        raise ValueError("Desktop atom state must be an object")
    efforts = atoms.get(EFFORTS, DEFAULT_EFFORTS)
    if not isinstance(efforts, list) or not all(isinstance(e, str) for e in efforts):
        raise ValueError("Desktop reasoning preferences must be a string list")
    if "none" in efforts:
        return existing
    atoms[EFFORTS] = ["none", *efforts]
    return json.dumps(state, ensure_ascii=False, indent=2) + "\n"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--existing", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    existing = args.existing.read_text(encoding="utf-8-sig") if args.existing.exists() else "{}"
    args.output.write_text(enable_no_reasoning(existing), encoding="utf-8")
