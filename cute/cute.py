"""Module cute — découpage intelligent de la vidéo en shorts (v2.1).

Améliorations v2.1 :
  - parsing de temps ROBUSTE : le LLM répond en secondes, 'MM:SS',
    'HH:MM:SS' ou 'MM:SS.mmm' — tout est accepté (parse_any_timestamp) ;
  - validation renforcée des moments : gap minimum entre shorts
    (MIN_GAP_SEC), fusion des chevauchements partiels au lieu de les
    jeter, priorité au score ;
  - découpe PARALLÉLISÉE (ThreadPool) : N ffmpeg simultanés ;
  - découpe ffmpeg fiabilisée : -ss AVANT -i (seek rapide) + re-encode
    du flux (cuts précis sur image-clé), audio normalisé plus tard ;
  - si le LLM renvoie moins de moments que MAX_SHORTS, on complète
    avec les meilleures fenêtres algorithmiques restantes ;
  - reprise : moments.json + cuts déjà là -> saut sauf --force.

Sorties : moments.json, cuts/cut_XX.mp4, cuts/cut_XX.srt
"""
import json
import math
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "common"))
from mihawk_common import (  # noqa: E402
    llm_json,
    mark_step,
    parse_any_timestamp,
    parse_srt,
    run_rel,
    setup_logging,
    write_srt,
)

log = setup_logging("cute")

MIN_GAP_SEC = 3.0  # gap minimal entre deux shorts


def load_segments():
    """Sous-titres traduits si dispo, sinon transcription source."""
    for name in ("translated.srt", "output.srt"):
        path = run_rel(name)
        if os.path.exists(path):
            return parse_srt(path), name
    log.error("Aucun fichier SRT trouvé (output.srt / translated.srt)")
    sys.exit(1)


def video_duration():
    meta_path = run_rel("meta.json")
    if os.path.exists(meta_path):
        with open(meta_path, encoding="utf-8") as f:
            return json.load(f).get("duration", 0)
    return 0


def seconds_to_str(t):
    return f"{int(t // 60):02d}:{int(t % 60):02d}"


# ------------------------------------------------------------------
# Sélection intelligente (LLM)
# ------------------------------------------------------------------
def smart_selection(subs, duration, min_s, max_s, target_s, max_count, title):
    """Demande au LLM les moments les plus pertinents pour des shorts."""
    seg_lines = "\n".join(
        f'{sub["index"]}| {seconds_to_str(sub["start"])} -> {seconds_to_str(sub["end"])} | {sub["text"]}'
        for sub in subs
    )
    prompt = f"""Tu es un expert en création de contenu viral (YouTube Shorts, TikTok, Reels).
Analyse la transcription horodatée de cette vidéo et sélectionne les {max_count} meilleurs
moments pour en faire des shorts.

Titre de la vidéo : {title}
Durée totale : {int(duration)} secondes

TRANSCRIPTION (numéro | début -> fin | texte) :
{seg_lines}

CRITÈRES de sélection :
- Un moment AUTONOME : il commence par une accroche et se termine par une conclusion
  ou une punchline, sans couper une phrase en deux.
- Un moment VIRAL : anecdote surprenante, révélation, histoire forte, conseil utile.
- Durée entre {min_s} et {max_s} secondes (idéalement ~{target_s} s).
- Ne chevauche jamais un autre moment sélectionné.

Réponds UNIQUEMENT en JSON valide :
[
  {{"start": <secondes, ex 12.5 ou "01:30">, "end": <secondes>, "title": "<titre court du moment>", "score": <0-10>}}
]"""

    data = llm_json(prompt, system="Tu réponds uniquement en JSON valide, sans autre texte.",
                    expect_type="list")
    moments = []
    if isinstance(data, list):
        for m in data:
            if not isinstance(m, dict):
                continue
            start = parse_any_timestamp(m.get("start"))
            end = parse_any_timestamp(m.get("end"))
            if start is None or end is None:
                log.warning("moment ignoré (temps illisible): %r / %r", m.get("start"), m.get("end"))
                continue
            try:
                score = float(m.get("score", 5))
            except (TypeError, ValueError):
                score = 5.0
            moments.append({
                "start": start, "end": end,
                "title": str(m.get("title", ""))[:120], "score": score,
            })
        log.info("LLM a proposé %d moment(s) (%d illisible(s) ignoré(s))",
                 len(moments), len(data) - len(moments))
    else:
        log.warning("Réponse LLM invalide — fallback algorithmique")
    return moments


# ------------------------------------------------------------------
# Fallback algorithmique
# ------------------------------------------------------------------
def algo_selection(subs, duration, target_s, max_count, existing):
    """Fenêtres équidistantes sur les zones non couvertes par `existing`."""
    moments = []
    if duration <= 0:
        return moments
    covered = sorted([(m["start"], m["end"]) for m in existing])
    # zones libres
    free = []
    cursor = 0.0
    for s, e in covered:
        if s - cursor >= target_s * 0.6:
            free.append((cursor, s))
        cursor = max(cursor, e)
    if duration - cursor >= target_s * 0.6:
        free.append((cursor, duration))
    for fs, fe in free:
        if len(moments) + len(existing) >= max_count:
            break
        # découpe de la zone libre en fenêtres cibles
        t = fs
        while t < fe - target_s * 0.5 and len(moments) + len(existing) < max_count:
            moments.append({"start": t, "end": min(fe, t + target_s), "title": "", "score": 3.0})
            t += target_s
    return moments


# ------------------------------------------------------------------
# Normalisation des moments
# ------------------------------------------------------------------
def clamp_moments(moments, subs, duration, min_s, max_s, target_s, max_count):
    """Normalise : bornes, alignement sur phrases, gap minimum, fusion."""
    boundaries = [0.0] + [s["start"] for s in subs] + [duration]
    boundaries = sorted(set(b for b in boundaries if b <= duration))

    def snap(t):
        """Aligne un temps sur la frontière la plus proche (max 2 s d'écart)."""
        best = min(boundaries, key=lambda b: abs(b - t))
        return best if abs(best - t) <= 2.0 else t

    # tri par score décroissant : on garde les meilleurs en priorité
    cleaned = []
    for m in sorted(moments, key=lambda x: -x.get("score", 0)):
        start = max(0.0, min(float(m["start"]), duration - min_s))
        end = min(duration, max(float(m["end"]), start + min_s))
        start, end = snap(start), snap(end)
        if end - start < min_s:
            end = min(duration, start + target_s)
        if end - start > max_s:
            end = start + max_s
        if end - start < min_s:  # encore trop court après bornage -> abandon
            continue

        # fusion si chevauchement/gap insuffisant avec un moment déjà gardé
        merged = False
        for kept in cleaned:
            overlap = not (end + MIN_GAP_SEC <= kept["start"] or start - MIN_GAP_SEC >= kept["end"])
            if overlap:
                # fusion des deux moments, bornée à max_s
                ns = min(kept["start"], start)
                ne = max(kept["end"], end)
                if ne - ns <= max_s:
                    kept["start"], kept["end"] = ns, ne
                    kept["score"] = max(kept["score"], m.get("score", 0))
                    if not kept["title"] and m.get("title"):
                        kept["title"] = m["title"]
                    merged = True
                else:
                    # trop grand pour fusionner : le meilleur score gagne
                    if m.get("score", 0) > kept["score"]:
                        kept.update(m)
                    merged = True
                break
        if not merged:
            cleaned.append(
                {"start": round(start, 2), "end": round(end, 2),
                 "title": m.get("title", "")[:120], "score": float(m.get("score", 5))}
            )
        if len(cleaned) >= max_count:
            break

    cleaned.sort(key=lambda m: m["start"])
    for i, m in enumerate(cleaned, 1):
        m["id"] = f"{i:02d}"
    return cleaned


# ------------------------------------------------------------------
# Découpe vidéo
# ------------------------------------------------------------------
def cut_one(video, out_path, start, end, crf):
    """Découpe un moment : -ss avant -i (seek rapide) + re-encode précis."""
    dur = end - start
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{start:.3f}", "-i", video, "-t", f"{dur:.3f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        out_path,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        ok = proc.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 1000
        if not ok and proc.stderr:
            log.error("ffmpeg: %s", proc.stderr[-200:])
        return ok
    except subprocess.TimeoutExpired:
        log.error("timeout ffmpeg sur %s", out_path)
        return False


def cut_video(moments, crf, workers=3):
    """Découpe TOUS les moments en parallèle."""
    video = run_rel("video.mp4")
    cuts_dir = run_rel("cuts")
    os.makedirs(cuts_dir, exist_ok=True)
    log.info("Découpe de %d moment(s) (%d parallèles)...", len(moments), workers)

    def job(m):
        out = os.path.join(cuts_dir, f'cut_{m["id"]}.mp4')
        m["cut"] = os.path.basename(out) if cut_one(video, out, m["start"], m["end"], crf) else None
        return m["id"]

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(job, moments))
    return moments


def write_cut_srts(moments, all_subs):
    """SRT traduit restreint à chaque cut, repositionné à 0."""
    for m in moments:
        segs = [
            {"start": max(0, s["start"] - m["start"]),
             "end": max(0.5, min(m["end"], s["end"]) - m["start"]),
             "text": s["text"]}
            for s in all_subs if s["start"] >= m["start"] - 0.3 and s["end"] <= m["end"] + 0.3
        ]
        write_srt(run_rel("cuts", f'cut_{m["id"]}.srt'), segs)


def main():
    min_s = float(os.environ.get("SHORT_MIN_SEC", 25))
    max_s = float(os.environ.get("SHORT_MAX_SEC", 90))
    target_s = float(os.environ.get("SHORT_TARGET_SEC", 60))
    max_count = int(os.environ.get("MAX_SHORTS", 10))
    crf = os.environ.get("VIDEO_CRF", "23")

    if os.path.exists(run_rel("moments.json")) and "--force" not in sys.argv:
        log.info("moments.json existe déjà — découpage sauté (--force pour refaire)")
        mark_step("cute", "skipped")
        return

    subs, srt_name = load_segments()
    duration = video_duration() or (subs[-1]["end"] + 2)
    log.info("Analyse de %d sous-titres (%s), durée %.1f s — %d shorts max, cible %ds",
             len(subs), srt_name, duration, max_count, target_s)

    title = ""
    title_path = run_rel("title.txt")
    if os.path.exists(title_path):
        with open(title_path, encoding="utf-8") as f:
            title = f.read().strip()

    # 1) sélection LLM
    moments = smart_selection(subs, duration, min_s, max_s, target_s, max_count, title)
    # 2) normalisation (bornes, phrases, gap, fusion)
    moments = clamp_moments(moments, subs, duration, min_s, max_s, target_s, max_count)
    # 3) complément algorithmique si le LLM a été avare
    if 0 < len(moments) < max_count:
        extra = algo_selection(subs, duration, target_s, max_count, moments)
        if extra:
            log.info("Complément algorithmique : %d fenêtre(s)", len(extra))
            moments = clamp_moments(moments + extra, subs, duration, min_s, max_s, target_s, max_count)
    if not moments:
        moments = clamp_moments(
            algo_selection(subs, duration, target_s, max_count, []),
            subs, duration, min_s, max_s, target_s, max_count,
        )
    if not moments:
        log.error("Vidéo trop courte (%.1fs) pour produire un short", duration)
        mark_step("cute", "error")
        sys.exit(1)

    # 4) découpe parallèle
    moments = cut_video(moments, crf)
    kept = [m for m in moments if m.get("cut")]

    if not kept:
        log.error("Aucune découpe n'a abouti")
        mark_step("cute", "error")
        sys.exit(1)

    with open(run_rel("moments.json"), "w", encoding="utf-8") as f:
        json.dump(kept, f, indent=2, ensure_ascii=False)

    # 5) SRT par cut
    write_cut_srts(kept, subs)

    mark_step("cute", "ok", {"shorts": len(kept)})
    log.info("Terminé : %d shorts prêts dans cuts/", len(kept))
    for m in kept:
        log.info("  cut_%s : %6.1fs -> %6.1fs (%5.1fs) — %s",
                 m["id"], m["start"], m["end"], m["end"] - m["start"], m["title"] or "?")


if __name__ == "__main__":
    main()
