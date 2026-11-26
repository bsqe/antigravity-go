#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLUGIN_SRC="$REPO_ROOT/plugin"
PLUGIN_DST="${HOME}/.gemini/config/plugins/antigravity-go"

PYTHON_BIN="${1:-}"
if [[ -z "$PYTHON_BIN" ]]; then
  if [[ -x "$REPO_ROOT/bridge/.venv/bin/python" ]]; then
    PYTHON_BIN="$REPO_ROOT/bridge/.venv/bin/python"
  else
    PYTHON_BIN="$(command -v python3 || command -v python)"
  fi
fi

mkdir -p "$PLUGIN_DST"
cp -R "$PLUGIN_SRC/"* "$PLUGIN_DST/"
chmod +x "$PLUGIN_DST/scripts/hook_forward.py"

"$PYTHON_BIN" - "$PLUGIN_DST" "$PYTHON_BIN" <<'PY'
import json, pathlib, sys
dst = pathlib.Path(sys.argv[1])
py = sys.argv[2]

sc_path = dst / "sidecars" / "bridge" / "sidecar.json"
sc = json.loads(sc_path.read_text(encoding="utf-8"))
sc["command"] = py
sc_path.write_text(json.dumps(sc, indent=2) + "\n", encoding="utf-8")

hooks_path = dst / "hooks.json"
hooks = hooks_path.read_text(encoding="utf-8").replace(
    '"python ./scripts/hook_forward.py"',
    json.dumps(f'"{py}" ./scripts/hook_forward.py'),
)
hooks_path.write_text(hooks, encoding="utf-8")
PY

echo "Installed Antigravity-Go plugin to $PLUGIN_DST (python: $PYTHON_BIN)"
