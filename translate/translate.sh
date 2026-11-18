#!/bin/bash
# =============================================================
# Module translate — traduction du SRT + accord avec la timeline
# Usage: translate.sh <run_dir> [langue_cible]
# Sorties : translated.srt, translated.txt, timeline.json
# =============================================================
set -euo pipefail

RUN_DIR="${1:-}"
TARGET_LANG="${2:-fr}"

if [ -z "$RUN_DIR" ]; then
    echo "[translate] Usage: translate.sh <run_dir> [langue_cible]" >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../common/config.sh"

export MIHAWK_RUN="$(basename "$RUN_DIR")"

if [ ! -f "$RUN_DIR/output.srt" ]; then
    echo "[translate] ERREUR: $RUN_DIR/output.srt introuvable." >&2
    exit 1
fi

echo "[translate] Traduction vers '$TARGET_LANG' + réalignement timeline..."
"$MIHAWK_PYTHON" "$SCRIPT_DIR/translate.py" "$TARGET_LANG"
