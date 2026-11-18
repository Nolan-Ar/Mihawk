#!/bin/bash
# =============================================================
# Module treatment — transcription de l'audio en SRT multilingue
# Usage: treatment.sh <run_dir>
# Sorties : output.srt, original.txt, language.txt, meta.json
# =============================================================
set -euo pipefail

RUN_DIR="${1:-}"
if [ -z "$RUN_DIR" ]; then
    echo "[treatment] Usage: treatment.sh <run_dir>" >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../common/config.sh"

export MIHAWK_RUN="$(basename "$RUN_DIR")"

if [ ! -f "$RUN_DIR/audio.wav" ]; then
    echo "[treatment] ERREUR: $RUN_DIR/audio.wav introuvable." >&2
    exit 1
fi

echo "[treatment] Transcription (modèle $WHISPER_MODEL)..."
"$MIHAWK_PYTHON" "$SCRIPT_DIR/treatment.py"
