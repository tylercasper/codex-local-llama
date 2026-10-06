#!/usr/bin/env bash
set -euo pipefail

allow_degraded=false
if [[ ${1:-} == --allow-degraded ]]; then
    allow_degraded=true
    shift
fi
if (($#)); then
    echo "usage: codex-local-verify [--allow-degraded]" >&2
    exit 2
fi

fail() {
    echo "verification failed: $*" >&2
    exit 1
}

for command_name in python3 curl systemctl; do
    command -v "$command_name" >/dev/null 2>&1 \
        || fail "required command not found: $command_name"
done

isolated_home="${CODEX_LOCAL_HOME:-${HOME:?}/.codex-local}"
deployment_path="$isolated_home/deployment.json"
[[ -r "$deployment_path" ]] || fail "deployment manifest not found: $deployment_path"

mapfile -t values < <(
    python3 -c '
import json, sys
p = json.load(open(sys.argv[1], encoding="utf-8"))
for value in (
    p["local"]["host"], p["local"]["provider_port"], p["model"]["id"],
    p["codex"]["version"], p["paths"]["runtime_root"],
): print(value)
' "$deployment_path"
)
provider_host="${values[0]}"
provider_port="${values[1]}"
model_id="${values[2]}"
codex_version="${values[3]}"
runtime_root="${values[4]}"
base_url="http://$provider_host:$provider_port"

codex_binary="$runtime_root/codex/current/bin/codex"
[[ -x "$codex_binary" ]] || fail "private Codex binary is missing: $codex_binary"
"$codex_binary" --version | grep -F "$codex_version" >/dev/null \
    || fail "private Codex version does not match $codex_version"

for unit in codex-local-tunnel.service codex-local-provider.service; do
    systemctl is-enabled --quiet "$unit" || fail "$unit is not enabled"
    systemctl is-active --quiet "$unit" || fail "$unit is not active"
done

health_file="$(mktemp)"
trap 'rm -f -- "$health_file"' EXIT
health_status=""
for _attempt in {1..10}; do
    health_status="$(
        curl --silent --max-time 5 --output "$health_file" \
            --write-out '%{http_code}' "$base_url/healthz" || true
    )"
    if [[ "$health_status" == 200 ]]; then
        break
    fi
    if $allow_degraded && [[ "$health_status" == 503 ]]; then
        break
    fi
    sleep 1
done
[[ "$health_status" == 200 || ("$allow_degraded" == true && "$health_status" == 503) ]] \
    || fail "health endpoint returned HTTP ${health_status:-unknown}"
python3 -c '
import json, sys
p = json.load(open(sys.argv[1], encoding="utf-8"))
allow_degraded = sys.argv[2] == "true"
assert p.get("upstream") is True, "llama.cpp upstream is unavailable"
if not allow_degraded:
    assert p.get("status") == "ok", "provider is degraded"
    assert p.get("tavily_configured") is True, "Tavily is not configured"
' "$health_file" "$allow_degraded" || fail "provider health payload is not ready"

curl --fail --silent --show-error --max-time 5 "$base_url/v1/models" \
    | python3 -c '
import json, sys
model = sys.argv[1]
p = json.load(sys.stdin)
assert any(
    item.get("id") == model or model in item.get("aliases", [])
    for item in p.get("data", [])
)
' "$model_id" || fail "model $model_id is not advertised by the provider"

echo "codex-local verification passed"
echo "  Codex: $codex_version"
echo "  model: $model_id"
echo "  provider: $base_url"
