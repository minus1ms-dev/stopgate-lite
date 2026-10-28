#!/usr/bin/env bash
# Install StopGate into ~/.claude/hooks/stopgate and register it as a Stop hook.
# Usage: ./install.sh [--project]   (--project registers in ./.claude/settings.json instead)
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)/stopgate"
DEST="$HOME/.claude/hooks/stopgate"
SETTINGS="$HOME/.claude/settings.json"
[[ "${1:-}" == "--project" ]] && SETTINGS="$PWD/.claude/settings.json"
command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 1; }
command -v claude >/dev/null || echo "warning: 'claude' CLI not on PATH; reviewers will fail until it is" >&2
mkdir -p "$DEST" "$(dirname "$SETTINGS")"
cp -r "$SRC/." "$DEST/"
chmod +x "$DEST/stopgate.py"
python3 - "$SETTINGS" "$DEST/stopgate.py" <<'PY'
import json, pathlib, sys
path, cmd = pathlib.Path(sys.argv[1]), sys.argv[2]
cfg = json.loads(path.read_text()) if path.exists() and path.read_text().strip() else {}
stop = cfg.setdefault("hooks", {}).setdefault("Stop", [])
if not any(h.get("command") == cmd for g in stop for h in g.get("hooks", [])):
    stop.append({"hooks": [{"type": "command", "command": cmd, "timeout": 600}]})
path.write_text(json.dumps(cfg, indent=2) + "\n")
print(f"StopGate registered in {path}")
PY
