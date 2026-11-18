#!/bin/bash
# =============================================================
# Mihawk — Configuration centrale du projet
# Ce fichier est sourcé par prog.sh et tous les modules *.sh.
# Toutes les variables de configuration se trouvent ici.
# =============================================================

# --- Racine du projet (chemins relatifs, plus aucun /home/nono) ---
export MIHAWK_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

export START_DIR="$MIHAWK_ROOT/start"
export TREATMENT_DIR="$MIHAWK_ROOT/treatment"
export TRANSLATE_DIR="$MIHAWK_ROOT/translate"
export CUTE_DIR="$MIHAWK_ROOT/cute"
export VOICE_DIR="$MIHAWK_ROOT/voice"
export DESCRIPTION_DIR="$MIHAWK_ROOT/description"
export RENDER_DIR="$MIHAWK_ROOT/render"
export COMMON_DIR="$MIHAWK_ROOT/common"

# --- Dossier de travail : chaque exécution de prog.sh y crée un
#     sous-dossier numéroté (run_001, run_002, ...) qui contient
#     tous les fichiers intermédiaires et les rendus finaux. ---
export WORK_ROOT="$MIHAWK_ROOT/work"

# --- Python à utiliser (venv si présent, sinon python3 du système) ---
if [ -f "$MIHAWK_ROOT/myenv/bin/python" ]; then
    export MIHAWK_PYTHON="$MIHAWK_ROOT/myenv/bin/python"
else
    export MIHAWK_PYTHON="$(command -v python3)"
fi

# --- Modèles et options IA ---
export WHISPER_MODEL="${WHISPER_MODEL:-small}"        # tiny|base|small|medium|large-v3 (faster-whisper)
export WHISPER_DEVICE="${WHISPER_DEVICE:-cpu}"         # cpu ou cuda
export WHISPER_COMPUTE="${WHISPER_COMPUTE:-int8}"      # int8 pour CPU, float16 pour GPU
export TTS_ENGINE="${TTS_ENGINE:-edge}"               # edge (edge-tts) | zai (z-ai tts)
export ZAI_TTS_VOICE="${ZAI_TTS_VOICE:-tongtong}"     # voix si TTS_ENGINE=zai

# --- Paramètres de découpage des shorts ---
export SHORT_MIN_SEC="${SHORT_MIN_SEC:-25}"           # durée min d'un short (s)
export SHORT_MAX_SEC="${SHORT_MAX_SEC:-90}"           # durée max d'un short (s)
export SHORT_TARGET_SEC="${SHORT_TARGET_SEC:-60}"    # durée cible d'un short (s)
export MAX_SHORTS="${MAX_SHORTS:-10}"                 # nombre max de shorts produits

# --- Paramètres vidéos ---
export VIDEO_CRF="${VIDEO_CRF:-23}"                   # qualité x264 (plus bas = mieux)
export VIDEO_HEIGHT="${VIDEO_HEIGHT:-1280}"           # hauteur max des rendus (2D -> 1280x720)

# --- Logging ---
export MIHAWK_LOGS="$MIHAWK_ROOT/logs"
mkdir -p "$MIHAWK_LOGS" "$WORK_ROOT" 2>/dev/null || true

# --- Voix de doublage ---
export TTS_VOICE_GENDER="${TTS_VOICE_GENDER:-male}"   # male | female

# --- Rendu final ---
export RENDER_MODE="${RENDER_MODE:-vertical}"         # vertical (9:16) | original
