#!/bin/bash
# =============================================================
# Module start — téléchargement de la vidéo YouTube
# Usage: start.sh <url> <run_dir>
# Sorties dans <run_dir>:
#   - video.mp4 : la vidéo originale téléchargée
#   - audio.wav : piste audio extraite (16 kHz mono, pour Whisper)
#   - title.txt  : titre de la vidéo (pour le contexte LLM)
#   - url.txt    : l'URL d'origine
# =============================================================
set -euo pipefail

URL="${1:-}"
RUN_DIR="${2:-}"

if [ -z "$URL" ] || [ -z "$RUN_DIR" ]; then
    echo "[start] Usage: start.sh <url> <run_dir>" >&2
    exit 1
fi

mkdir -p "$RUN_DIR"
cd "$RUN_DIR"

echo "[start] Téléchargement de la vidéo..."

# -- Vérification des outils --
for tool in yt-dlp ffmpeg ffprobe; do
    if ! command -v "$tool" &> /dev/null; then
        echo "[start] ERREUR: $tool n'est pas installé." >&2
        exit 1
    fi
done

# -- Téléchargement (mp4 si possible, sinon meilleur format) --
# --enable-file-urls : permet aussi de tester le pipeline avec une vidéo locale --
yt-dlp -f 'bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b' \
       --merge-output-format mp4 \
       --no-playlist \
       --enable-file-urls \
       -o "video.mp4" "$URL" 2>&1 | tee download.log

if [ ! -f "video.mp4" ]; then
    echo "[start] ERREUR: échec du téléchargement." >&2
    exit 1
fi

# -- Titre de la vidéo (contexte utile aux LLM) --
yt-dlp --skip-download --print "%(title)s" "$URL" > title.txt 2>/dev/null || \
    yt-dlp --get-filename -o "%(title)s" --skip-download "$URL" > title.txt 2>/dev/null || true

# -- Extraction audio en WAV 16 kHz mono (format attendu par Whisper) --
ffmpeg -y -i video.mp4 -vn -acodec pcm_s16le -ar 16000 -ac 1 audio.wav \
    -hide_banner -loglevel error

echo "$URL" > url.txt

# -- Durée --
DURATION=$(ffprobe -v error -show_entries format=duration \
    -of default=noprint_wrappers=1:nokey=1 video.mp4)
echo "$DURATION" > duration.txt

echo "[start] OK — vidéo: $(du -h video.mp4 | cut -f1), durée: ${DURATION}s"
