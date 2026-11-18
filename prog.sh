#!/bin/bash
# =============================================================
#  MIHAWK v2.1 — Pipeline automatisé de traduction et production
#  de shorts à partir d'une vidéo YouTube.
#
#  Usage :
#    ./prog.sh                                (mode interactif)
#    ./prog.sh run <URL> [langue]             (pipeline complet)
#    ./prog.sh run <URL> <langue> --quick      (modèle tiny, 3 shorts)
#    ./prog.sh resume [run_dir]               (reprend un run interrompu)
#    ./prog.sh batch urls.txt [langue]        (plusieurs vidéos à la suite)
#    ./prog.sh report [run_dir]               (rapport récapitulatif)
#    ./prog.sh doctor                         (vérifie l'installation)
#    ./prog.sh voices                          (liste les langues TTS)
#
#  Pipeline :
#    start       -> télécharge la vidéo + audio
#    treatment   -> transcription multilingue (faster-whisper, détection auto)
#    translate   -> traduction LLM + accord avec la timeline
#    cute        -> sélection LLM des meilleurs moments + découpe
#    voice       -> doublage TTS synchronisé (edge-tts, langue cible)
#    description-> titres/descriptions/hashtags/SEO (LLM)
#    render      -> sous-titres incrustés + 9:16 + miniatures
#
#  Variables utiles (cf common/config.sh) :
#    MIHAWK_STEPS, WHISPER_MODEL, MAX_SHORTS, SHORT_TARGET_SEC,
#    TTS_VOICE_GENDER, RENDER_MODE, RENDER_UPSCALE, TRANSLATE_BILINGUAL
# =============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common/config.sh"

# ---------- helpers ----------
say()  { echo -e "\033[1;36m[mihawk]\033[0m $*"; }
die()  { echo -e "\033[1;31m[ERREUR]\033[0m $*" >&2; exit 1; }
step() { echo -e "\n\033[1;35m=== ÉTAPE : $1 ===\033[0m"; }

usage() {
    grep -E "^#  " "$SCRIPT_DIR/prog.sh" | head -20 | sed 's/^#  //'
}

# ---------- doctor : vérification de l'installation ----------
cmd_doctor() {
    say "Vérification de l'installation Mihawk…"
    local fail=0
    for tool in ffmpeg ffprobe; do
        if command -v "$tool" &>/dev/null; then
            echo "  ✔ $tool ($($tool -version 2>/dev/null | head -1 | cut -d' ' -f3))"
        else
            echo "  ✘ $tool MANQUANT (apt install ffmpeg)"; fail=1
        fi
    done
    if command -v yt-dlp &>/dev/null; then
        echo "  ✔ yt-dlp ($(yt-dlp --version 2>/dev/null))"
    else
        echo "  ✘ yt-dlp MANQUANT (pip install yt-dlp)"; fail=1
    fi
    if command -v z-ai &>/dev/null; then
        echo "  ✔ z-ai CLI détecté"
    else
        echo "  ✘ z-ai MANQUANT (LLM indisponible)"; fail=1
    fi
    for mod in faster_whisper edge_tts srt; do
        if "$MIHAWK_PYTHON" -c "import $mod" 2>/dev/null; then
            echo "  ✔ python:$mod"
        else
            echo "  ✘ python:$mod MANQUANT (pip install -r requirements.txt)"; fail=1
        fi
    done
    # tests fonctionnels légers
    if "$MIHAWK_PYTHON" -c "import av; av.open" 2>/dev/null; then
        :
    fi
    if [ "$fail" -eq 0 ]; then
        say "Installation OK — modèle Whisper par défaut : $WHISPER_MODEL ($WHISPER_DEVICE)"
        say "Test du LLM…"
        if timeout 60 z-ai chat --prompt "Réponds juste: OK" --output /tmp/mihawk_doctor.json >/dev/null 2>&1 \
           && "$MIHAWK_PYTHON" -c "import json;print('  ✔ LLM répond:', json.load(open('/tmp/mihawk_doctor.json'))['choices'][0]['message']['content'][:20])" 2>/dev/null; then
            rm -f /tmp/mihawk_doctor.json
        else
            echo "  ⚠ LLM n'a pas répondu au test (réseau ?) — la traduction/découpage tomberont en fallback"
        fi
    else
        die "Installation incomplète — voir ci-dessus."
    fi
}

# ---------- voices : langues TTS disponibles ----------
cmd_voices() {
    say "Langues avec voix edge-tts mappées (voix masculine / féminine) :"
    "$MIHAWK_PYTHON" - <<'PYEOF'
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath("prog.sh")), "common"))
sys.path.insert(0, "common")
from mihawk_common import TTS_VOICE_MAP, language_name
for code, (m, f) in sorted(TTS_VOICE_MAP.items()):
    print(f"  {code}  {language_name(code):<12} {m} / {f}")
PYEOF
}

# ---------- exécution du pipeline ----------
run_pipeline() {
    local URL="$1" TARGET_LANG="$2" RUN_DIR="$3"

    say "Dossier de travail : $RUN_DIR"

    STEPS="${MIHAWK_STEPS:-start,treatment,translate,cute,voice,description,render}"

    run_step() {
        local name="$1"; shift
        if [[ ",$STEPS," == *",$name,"* ]]; then
            step "$name"
            "$@"
            return $?
        fi
        say "Étape '$name' sautée (MIHAWK_STEPS)."
        return 0
    }

    run_step start       "$START_DIR/start.sh" "$URL" "$RUN_DIR"
    run_step treatment   "$TREATMENT_DIR/treatment.sh" "$RUN_DIR"

    if [ -f "$RUN_DIR/language.txt" ]; then
        say "Langue détectée : $(cat "$RUN_DIR/language.txt")"
    fi

    run_step translate   "$TRANSLATE_DIR/translate.sh" "$RUN_DIR" "$TARGET_LANG"
    run_step cute        "$CUTE_DIR/cute.sh" "$RUN_DIR"
    run_step voice       "$VOICE_DIR/voice.sh" "$RUN_DIR"
    run_step description "$DESCRIPTION_DIR/description.sh" "$RUN_DIR"
    run_step render      "$RENDER_DIR/render.sh" "$RUN_DIR"
}

# ---------- résumé d'un run ----------
summarize_run() {
    local RUN_DIR="$1"
    echo ""
    echo "============================================================"
    say "PIPELINE TERMINÉ — $RUN_DIR"
    echo "============================================================"
    say "Langue source   : $(cat "$RUN_DIR/language.txt" 2>/dev/null || echo '?')"
    say "Shorts produits : $(ls "$RUN_DIR/shorts/"*.mp4 2>/dev/null | wc -l)"
    if [ -f "$RUN_DIR/publish.json" ]; then
        say "Récapitulatif : $RUN_DIR/publish.json"
        "$MIHAWK_PYTHON" "$SCRIPT_DIR/scripts/show_publish.py" "$RUN_DIR/publish.json" 2>/dev/null || true
    fi
    say "Vidéos dans : $RUN_DIR/shorts/"
}

# ---------- commande run ----------
cmd_run() {
    local URL="${1:-}" TARGET_LANG="${2:-fr}"
    shift 2 2>/dev/null || true

    if [ -z "$URL" ]; then
        echo "============================================================"
        echo " MIHAWK — traduction & production automatisée de shorts"
        echo "============================================================"
        echo ""
        read -r -p "URL de la vidéo YouTube : " URL
        [ -z "$URL" ] && die "Aucune URL fournie."
        echo ""
        echo "Langue cible de la traduction/doublage :"
        echo "  fr  français        en  anglais"
        echo "  es  espagnol         de  allemand"
        echo "  it  italien          pt  portugais"
        echo "  ja  japonais         ko  coréen"
        echo "  zh  chinois          ru  russe"
        read -r -p "Langue cible [fr] : " TARGET_LANG
        TARGET_LANG="${TARGET_LANG:-fr}"
        echo ""
    fi

    # flags
    for arg in "$@"; do
        case "$arg" in
            --quick)
                export WHISPER_MODEL="tiny"
                export MAX_SHORTS="3"
                export SHORT_TARGET_SEC="${SHORT_TARGET_SEC:-45}"
                say "Mode --quick : modèle tiny, 3 shorts max"
                ;;
            --hd)
                export RENDER_UPSCALE="1080"
                export VIDEO_CRF="21"
                say "Mode --hd : upscale 1080p, CRF 21"
                ;;
            --bilingual) export TRANSLATE_BILINGUAL="1" ;;
            --female)    export TTS_VOICE_GENDER="female" ;;
        esac
    done

    # vérifications
    for tool in ffmpeg ffprobe yt-dlp; do
        command -v "$tool" &> /dev/null || die "$tool n'est pas installé."
    done
    "$MIHAWK_PYTHON" -c "import faster_whisper" 2>/dev/null || die "faster-whisper manquant (pip install -r requirements.txt)."
    "$MIHAWK_PYTHON" -c "import srt" 2>/dev/null || die "srt manquant (pip install srt)."
    command -v z-ai &> /dev/null || die "le CLI z-ai n'est pas installé/configuré."

    local RUN_ID RUN_DIR
    RUN_ID=$(printf "run_%03d" $(( $(ls "$WORK_ROOT" 2>/dev/null | grep -c '^run_' || echo 0) + 1 )))
    RUN_DIR="$WORK_ROOT/$RUN_ID"
    mkdir -p "$RUN_DIR"

    run_pipeline "$URL" "$TARGET_LANG" "$RUN_DIR"
    summarize_run "$RUN_DIR"
}

# ---------- commande resume ----------
cmd_resume() {
    local RUN_DIR="${1:-}"
    if [ -z "$RUN_DIR" ]; then
        # dernier run modifié
        RUN_DIR=$(ls -dt "$WORK_ROOT"/run_* 2>/dev/null | head -1)
        [ -z "$RUN_DIR" ] && die "Aucun run existant dans $WORK_ROOT."
    fi
    [ -d "$RUN_DIR" ] || die "Dossier introuvable : $RUN_DIR"
    say "Reprise de $RUN_DIR (les étapes déjà faites sont sautées)"
    local URL TARGET_LANG
    URL=$(cat "$RUN_DIR/url.txt" 2>/dev/null || echo "")
    TARGET_LANG=$("$MIHAWK_PYTHON" -c "import json;print(json.load(open('$RUN_DIR/timeline.json')).get('target_lang','fr'))" 2>/dev/null || echo fr)
    [ -z "$URL" ] && die "url.txt introuvable dans le run — impossible de reprendre."
    run_pipeline "$URL" "$TARGET_LANG" "$RUN_DIR"
    summarize_run "$RUN_DIR"
}

# ---------- commande batch ----------
cmd_batch() {
    local URLFILE="${1:?usage: batch urls.txt [langue]}" TARGET_LANG="${2:-fr}"
    [ -f "$URLFILE" ] || die "Fichier introuvable : $URLFILE"
    local total ok failed=0 n=0
    total=$(grep -c . "$URLFILE")
    say "Batch : $total vidéo(s) à traiter (langue $TARGET_LANG)"
    while IFS= read -r url; do
        [ -z "$url" ] && continue
        case "$url" in \#*) continue ;; esac
        n=$((n + 1))
        say "[$n/$total] $url"
        if ./prog.sh run "$url" "$TARGET_LANG"; then
            ok=$((ok + 1))
        else
            failed=$((failed + 1))
            say "ÉCHEC de $url — on continue"
        fi
    done < "$URLFILE"
    say "Batch terminé : $((ok)) réussite(s), $failed échec(s)"
}

# ---------- dispatch ----------
case "${1:-}" in
    run)       shift; cmd_run "$@" ;;
    resume)    shift; cmd_resume "$@" ;;
    batch)     shift; cmd_batch "$@" ;;
    doctor)    cmd_doctor ;;
    voices)    cmd_voices ;;
    report)
        shift
        RUN_DIR="${1:-}"
        [ -z "$RUN_DIR" ] && RUN_DIR=$(ls -dt "$WORK_ROOT"/run_* 2>/dev/null | head -1)
        "$MIHAWK_PYTHON" "$SCRIPT_DIR/scripts/show_publish.py" "$RUN_DIR/publish.json"
        ;;
    help|-h|--help|*)
        if [ -z "${1:-}" ] || [[ "${1:-}" == http* ]] || [[ "${1:-}" == -* ]]; then
            # compat v2 : ./prog.sh <URL> [langue] == ./prog.sh run <URL> [langue]
            if [[ "${1:-}" == http* || "${1:-}" == file://* || -z "${1:-}" ]]; then
                cmd_run "${1:-}" "${2:-fr}"
            else
                usage
            fi
        else
            usage
        fi
        ;;
esac
