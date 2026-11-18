#!/bin/bash
# =============================================================
# Module description — titres/descriptions/hashtags via LLM
# Usage: description.sh <run_dir>
# Sorties : cuts/cut_XX_meta.json|.txt, video_meta.json|.txt
# =============================================================
set -euo pipefail

RUN_DIR="${1:-}"
if [ -z "$RUN_DIR" ]; then
    echo "[description] Usage: description.sh <run_dir>" >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../common/config.sh"

export MIHAWK_RUN="$(basename "$RUN_DIR")"

if [ ! -f "$RUN_DIR/timeline.json" ]; then
    echo "[description] ERREUR: $RUN_DIR/timeline.json introuvable (translate doit être exécuté avant)." >&2
    exit 1
fi

echo "[description] Génération des titres / descriptions / hashtags (LLM)..."
"$MIHAWK_PYTHON" "$SCRIPT_DIR/description.py"
