#!/bin/bash
# =============================================================
# Module voice — doublage TTS synchronisé des shorts
# Usage: voice.sh <run_dir>
# Sorties : cuts/cut_XX_dub.mp4
# =============================================================
set -euo pipefail

RUN_DIR="${1:-}"
if [ -z "$RUN_DIR" ]; then
    echo "[voice] Usage: voice.sh <run_dir>" >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../common/config.sh"

export MIHAWK_RUN="$(basename "$RUN_DIR")"

if [ ! -f "$RUN_DIR/moments.json" ]; then
    echo "[voice] ERREUR: $RUN_DIR/moments.json introuvable (cute doit être exécuté avant)." >&2
    exit 1
fi

echo "[voice] Doublage des shorts (moteur TTS: $TTS_ENGINE)..."
"$MIHAWK_PYTHON" "$SCRIPT_DIR/voice.py"
