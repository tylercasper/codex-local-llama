"""Expose no reasoning in current desktop settings without replacing preferences."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import tomllib

from merge_gui_config import section_path

# Defaults in the pinned vendor frontends. Keep their other choices available.
DEFAULT_EFFORTS = ["low", "medium", "high", "xhigh", "ultra", "persistent"]
ATOM_STATE = "electron-persisted-atom-state"
EFFORTS = "enabled-reasoning-efforts"


def enable_no_reasoning(config: str, legacy_state: str = "{}") -> str:
    original = tomllib.loads(config)
    desktop = original.get("desktop", {})
    if not isinstance(desktop, dict):
        raise ValueError("Desktop settings must be a table")
    if EFFORTS in desktop:
        efforts = desktop[EFFORTS]
    else:
        state = json.loads(legacy_state)
        if not isinstance(state, dict) or not isinstance(state.get(ATOM_STATE, {}), dict):
            raise ValueError("Legacy desktop state must contain an atom-state object")
        efforts = state.get(ATOM_STATE, {}).get(EFFORTS, DEFAULT_EFFORTS)
    if not isinstance(efforts, list) or not all(isinstance(e, str) for e in efforts):
        raise ValueError("Desktop reasoning preferences must be a string list")
    if EFFORTS in desktop and "none" in efforts:
        return config
    enabled = efforts if "none" in efforts else ["none", *efforts]
    assignment = EFFORTS + " = " + json.dumps(enabled) + "\n"
    lines = config.splitlines(keepends=True)
    path, insertion, replacement = (), None, None
    for i, line in enumerate(lines):
        if line.lstrip().startswith("["):
            path = section_path(line)
            if path == ("desktop",):
                insertion = i + 1
        elif path == ("desktop",) and "=" in line:
            key = line.split("=", 1)[0].strip().strip("\"'")
            if key == EFFORTS:
                # Consume the complete assignment, including a multiline array.
                for end in range(i + 1, len(lines) + 1):
                    try:
                        tomllib.loads("".join(lines[i:end]))
                    except tomllib.TOMLDecodeError:
                        continue
                    replacement = (i, end)
                    break
                if replacement is None:
                    raise ValueError("Cannot safely update desktop reasoning preferences")
                break
    if replacement:
        lines[replacement[0]:replacement[1]] = [assignment]
    elif insertion is not None:
        lines[insertion - 1] = lines[insertion - 1].rstrip("\r\n") + "\n"
        lines.insert(insertion, assignment)
    else:
        lines.extend(["\n[desktop]\n", assignment])
    result = "".join(lines)
    expected = deepcopy(original)
    expected.setdefault("desktop", {})[EFFORTS] = enabled
    if tomllib.loads(result) != expected:
        raise ValueError("Cannot safely update desktop reasoning preferences")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--existing", type=Path, required=True, help="Legacy desktop state, if present")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    legacy = args.existing.read_text(encoding="utf-8-sig") if args.existing.exists() else "{}"
    config = args.config.read_text(encoding="utf-8-sig")
    args.output.write_text(enable_no_reasoning(config, legacy), encoding="utf-8")
