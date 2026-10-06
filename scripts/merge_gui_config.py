"""Refresh generated GUI connection settings without replacing user preferences."""
import argparse
from copy import deepcopy
from pathlib import Path
import tomllib

MANAGED_KEYS = frozenset(("model", "model_provider", "model_catalog_json", "model_instructions_file", "model_context_window", "sqlite_home"))
PROVIDER_PATH = ("model_providers", "llamacpp")


def section_path(header):
    """Let TOML itself interpret quoted/dotted table names."""
    parsed = tomllib.loads(header + "\n__codex_merge_marker = true\n")
    path = []
    while isinstance(parsed, dict) and "__codex_merge_marker" not in parsed:
        if len(parsed) != 1:
            return None
        key, parsed = next(iter(parsed.items()))
        path.append(key)
    return tuple(path)


def merge_gui_config(existing: str, rendered: str) -> str:
    source = tomllib.loads(rendered)
    original = tomllib.loads(existing)
    provider = source.get("model_providers", {}).get("llamacpp", {})
    targets = {(): {k: source[k] for k in MANAGED_KEYS if k in source}, PROVIDER_PATH: provider}
    replacements = {}
    path = ()
    # Values come directly from validated renderer output, retaining TOML syntax.
    for line in rendered.splitlines(keepends=True):
        if line.lstrip().startswith("["):
            path = section_path(line)
        elif "=" in line:
            name = line.split("=", 1)[0].strip().strip('"\'')
            if path in targets and name in targets[path]:
                replacements[path, name] = line.rstrip("\r\n") + "\n"
    if len(replacements) != sum(len(v) for v in targets.values()):
        raise ValueError("Generated GUI settings must use scalar assignments")
    sections = {(): []}
    order = [()]
    path = ()
    seen = set()
    for line in existing.splitlines(keepends=True):
        if line.lstrip().startswith("["):
            path = section_path(line)
            # Keep unique block identities for arrays of tables and repeated nested paths.
            block = (path, len(order))
            order.append(block)
            sections[block] = [line]
        else:
            name = line.split("=", 1)[0].strip().strip('"\'') if "=" in line else None
            key = (path, name)
            if key in replacements:
                line = replacements[key]
                seen.add(key)
            sections[order[-1]].append(line)
    for path, values in targets.items():
        pending = [replacements[path, key] for key in values if (path, key) not in seen]
        if not pending:
            continue
        if path == ():
            sections[()].extend(["\n"] + pending)
        else:
            block = next((b for b in order[1:] if b[0] == path), None)
            if block is None:
                block = (path, len(order))
                order.append(block)
                sections[block] = ["\n[model_providers.llamacpp]\n"]
            sections[block].extend(["\n"] + pending)
    result = "".join("".join(sections[b]) for b in order)
    expected = deepcopy(original)
    expected.update(targets[()])
    if provider:
        expected.setdefault("model_providers", {}).setdefault("llamacpp", {}).update(provider)
    # Unsupported formatting fails before installation instead of changing unrelated data.
    if tomllib.loads(result) != expected:
        raise ValueError("Cannot safely refresh GUI configuration; unsupported TOML formatting")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--existing", type=Path, required=True)
    parser.add_argument("--rendered", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rendered = args.rendered.read_text(encoding="utf-8-sig")
    tomllib.loads(rendered)
    merged = merge_gui_config(args.existing.read_text(encoding="utf-8-sig"), rendered) if args.existing.exists() else rendered
    args.output.write_text(merged, encoding="utf-8")
