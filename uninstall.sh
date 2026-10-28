#!/usr/bin/env bash
# Remove StopGate's Stop hook entry (user and project settings) and its files.
set -euo pipefail
DEST="$HOME/.claude/hooks/stopgate"
for SETTINGS in "$HOME/.claude/settings.json" "$PWD/.claude/settings.json"; do
  [[ -f "$SETTINGS" ]] || continue
  python3 - "$SETTINGS" "$DEST/stopgate.py" <<'PY'
import json, pathlib, sys
path, cmd = pathlib.Path(sys.argv[1]), sys.argv[2]
cfg = json.loads(path.read_text())
stop = cfg.get("hooks", {}).get("Stop", [])
for g in stop:
    g["hooks"] = [h for h in g.get("hooks", []) if h.get("command") != cmd]
cfg.get("hooks", {})["Stop"] = [g for g in stop if g.get("hooks")]
path.write_text(json.dumps(cfg, indent=2) + "\n")
PY
done
rm -rf "$DEST"
echo "StopGate removed"
