#!/bin/bash
# =============================================================
# Module render — habillage final (sous-titres incrustés + 9:16)
# Usage: render.sh <run_dir>
# Env    : RENDER_MODE=vertical|original
# Sorties : shorts/short_XX.mp4, publish.json
# =============================================================
set -euo pipefail

RUN_DIR="${1:-}"
if [ -z "$RUN_DIR" ]; then
    echo "[render] Usage: render.sh <run_dir>" >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../common/config.sh"

export MIHAWK_RUN="$(basename "$RUN_DIR")"

if [ ! -f "$RUN_DIR/moments.json" ]; then
    echo "[render] ERREUR: $RUN_DIR/moments.json introuvable (cute doit être exécuté avant)." >&2
    exit 1
fi

echo "[render] Rendu final des shorts (mode: ${RENDER_MODE:-vertical})..."
"$MIHAWK_PYTHON" "$SCRIPT_DIR/render.py"
