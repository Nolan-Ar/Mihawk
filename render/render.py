"""Module render — habillage final des shorts (v2.1).

Améliorations v2.1 :
  - DIMENSIONS PAIRES garanties (crop/scale -2) : le H.264 4:2:0
    exige des largeurs/hauteurs multiples de 2 — évite les fichiers
    illisibles sur certains lecteurs ;
  - pix_fmt yuv420p explicite partout (compatibilité maximale) ;
  - SOUS-TITRES ADAPTÉS AU PORTRAIT : taille proportionnelle à la
    hauteur de la vidéo (plus gros en 9:16), position remontée pour
    laisser la place aux boutons UI TikTok/Shorts ;
  - option RENDER_FPS de remappage (30 fps standard shorts) ;
  - MINIATURES : thumbnail_XX.jpg (image-clé au 1/3 du short) pour
    chaque vidéo — utile à la publication ;
  - option RENDER_UPSCALE=1080 : mise à l'échelle verticale propre
    (1080x1920) pour les sources basse résolution ;
  - reprise : short_XX.mp4 existant sauf --force.

Sorties : shorts/short_XX.mp4, shorts/thumbnail_XX.jpg, publish.json
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "common"))
from mihawk_common import ffprobe_video_size, mark_step, run_rel, setup_logging  # noqa: E402

log = setup_logging("render")

# Style de sous-titres : gras, contour noir, centré bas (shorts).
# La taille est relative ( \\fs ) : adaptée en portrait via scale2ref.
SUBTITLE_STYLE_BASE = (
    "FontName=DejaVu Sans,Bold=1,"
    "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline=2,Shadow=1,"
    "Alignment=2,MarginV=48"
)


def escape_filter_path(path):
    """Échappe un chemin pour le filtre subtitles= de ffmpeg."""
    path = os.path.abspath(path)
    return path.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def subtitle_style(video_height):
    """Style adapté à la hauteur réelle : gros texte en portrait."""
    # ~40px pour 720p de haut ; proportionnel
    size = max(18, int(video_height * 0.055))
    return SUBTITLE_STYLE_BASE.replace("Bold=1", f"Fontsize={size},Bold=1")


def build_video_filters(src, srt_path, mode, upscale):
    """Construit le filtre vidéo (crop 9:16 pair + sous-titres)."""
    w, h = ffprobe_video_size(src)
    parts = []
    if mode == "vertical" and w and h and w > h:
        target_w = int(h * 9 / 16)
        if target_w <= w:
            target_w -= target_w % 2      # pair obligatoire (yuv420)
            parts.append(f"crop={target_w}:{h}:(iw-{target_w})/2:0")
    if upscale and h:
        # upscale vertical 1080p (la hauteur finale détermine la largeur)
        target_h = 1080
        parts.append(f"scale=-2:{target_h}")
    if parts:
        # sous-titres APRÈS crop/scale : incrustés sur l'image finale
        style = subtitle_style(upscale and 1080 or (h or 720))
        parts.append("subtitles=" + escape_filter_path(srt_path) + f":force_style='{style}'")
        return ",".join(parts)
    # pas de transformation : sous-titres seuls
    style = subtitle_style(h or 720)
    return "subtitles=" + escape_filter_path(srt_path) + f":force_style='{style}'"


def render_short(cut_id, mode="vertical", crf="23", fps=None, upscale=None):
    """Habilles un short doublé : sous-titres + 9:16 -> shorts/short_XX.mp4."""
    src = run_rel("cuts", f"cut_{cut_id}_dub.mp4")
    srt = run_rel("cuts", f"cut_{cut_id}.srt")
    if not os.path.exists(src):
        log.warning("cut_%s_dub.mp4 absent — ignoré", cut_id)
        return None
    if not os.path.exists(srt):
        log.warning("cut_%s.srt absent — sous-titres non incrustés", cut_id)

    os.makedirs(run_rel("shorts"), exist_ok=True)
    dst = run_rel("shorts", f"short_{cut_id}.mp4")

    vf = build_video_filters(src, srt, mode, upscale)

    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", src,
        "-vf", vf,
    ]
    if fps:
        cmd += ["-r", str(fps)]
    cmd += [
        "-c:v", "libx264", "-preset", "veryfast",
        "-crf", str(crf),
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        dst,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not os.path.exists(dst):
        log.error("cut_%s : échec rendu ffmpeg — %s", cut_id, (proc.stderr or "")[-400:])
        return None

    # miniature : frame au tiers du short (moment représentatif)
    thumb = run_rel("shorts", f"thumbnail_{cut_id}.jpg")
    dur_proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", dst],
        capture_output=True, text=True,
    )
    try:
        dur = float(dur_proc.stdout.strip())
    except ValueError:
        dur = 5.0
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-ss", f"{dur / 3:.2f}", "-i", dst, "-frames:v", "1", "-q:v", "3", thumb],
        capture_output=True, text=True,
    )
    if not os.path.exists(thumb):
        log.warning("cut_%s : miniature non générée", cut_id)

    return dst


def main():
    force = "--force" in sys.argv
    mode = os.environ.get("RENDER_MODE", "vertical")  # vertical | original
    crf = os.environ.get("VIDEO_CRF", "23")
    fps = os.environ.get("RENDER_FPS")               # ex: 30, sinon natif
    upscale = os.environ.get("RENDER_UPSCALE")       # ex: 1080

    with open(run_rel("moments.json"), encoding="utf-8") as f:
        moments = json.load(f)

    publish = []
    for m in moments:
        cut_id = m.get("id")
        if not cut_id:
            continue
        dst = run_rel("shorts", f"short_{cut_id}.mp4")
        if not force and os.path.exists(dst):
            log.info("short_%s déjà rendu — sauté (--force)", cut_id)
        else:
            out = render_short(cut_id, mode, crf, fps, upscale)
            if not out:
                continue
        entry = {
            "fichier": os.path.basename(dst),
            "titre": m.get("title", ""),
            "debut": m.get("start"), "fin": m.get("end"),
            "score": m.get("score"),
        }
        meta_path = run_rel("cuts", f"cut_{cut_id}_meta.json")
        if os.path.exists(meta_path):
            with open(meta_path, encoding="utf-8") as f:
                entry["publication"] = json.load(f)
        publish.append(entry)

    if not publish:
        log.error("Aucun short rendu")
        mark_step("render", "error")
        sys.exit(1)

    with open(run_rel("publish.json"), "w", encoding="utf-8") as f:
        json.dump(publish, f, indent=2, ensure_ascii=False)

    mark_step("render", "ok", {"shorts": len(publish)})
    log.info("Rendu terminé : %d shorts prêts dans shorts/", len(publish))


if __name__ == "__main__":
    main()
