#!/bin/bash
# =============================================================
# Module cute — découpage intelligent en shorts
# Usage: cute.sh <run_dir>
# Sorties : cuts/cut_XX.mp4, cuts/cut_XX.srt, moments.json
# =============================================================
set -euo pipefail

RUN_DIR="${1:-}"
if [ -z "$RUN_DIR" ]; then
    echo "[cute] Usage: cute.sh <run_dir>" >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../common/config.sh"

export MIHAWK_RUN="$(basename "$RUN_DIR")"

if [ ! -f "$RUN_DIR/video.mp4" ]; then
    echo "[cute] ERREUR: $RUN_DIR/video.mp4 introuvable." >&2
    exit 1
fi

echo "[cute] Sélection intelligente des moments (LLM) et découpe..."
echo "[cute] Paramètres: min=${SHORT_MIN_SEC}s max=${SHORT_MAX_SEC}s cible=${SHORT_TARGET_SEC}s max=${MAX_SHORTS} shorts"
"$MIHAWK_PYTHON" "$SCRIPT_DIR/cute.py"
