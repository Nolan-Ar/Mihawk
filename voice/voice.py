"""Module voice — doublage des shorts avec TTS synchronisé (v2.1).

Améliorations v2.1 :
  - synthèse PARALLÈLE : toutes les phrases d'un short sont générées
    en un seul event-loop asyncio (siemens fois plus rapide) ;
  - TRIM DU SILENCE : edge-tts ajoute ~0.6 s de silence en fin de
    chaque MP3 -> supprimé via silenceremove, le placement
    temporel devient précis (c'était la source du décalage) ;
  - accélération PROPORTIONNELLE : atempo calculé sur la durée
    effective APRÈS trim, avec lissage (jamais > 1.35x pour rester
    naturel, débordement résiduel accepté dans le gap) ;
  - DUCKING SIDECHAIN : la VO s'atténue automatiquement QUAND on
    parle (volume dynamique) au lieu d'un volume fixe ;
  - LOUDNORM : normalisation broadcast EBU R128 du mix final
    (cohérence sonore entre les shorts) ;
  - nettoyage TTS du texte (clean_text_for_tts) : [Musique],
    emojis, symboles -> la voix ne lit plus les artefacts ;
  - durée de sortie bornée à la vidéo (-shortest) : plus de queue
    audio après la fin de l'image ;
  - reprise : cuts/cut_XX_dub.mp4 existants sautés sauf --force.

Sorties : cuts/cut_XX_dub.mp4
"""
import asyncio
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "common"))
from mihawk_common import (  # noqa: E402
    clean_text_for_tts,
    ffprobe_duration,
    mark_step,
    parse_srt,
    run_rel,
    setup_logging,
    tts_voice_for,
)

log = setup_logging("voice")

MAX_TEMPO = 1.35        # accélération max de la voix (au-delà : débit artificiel)
DUCK_LEVEL = 0.25       # VO à 25% pendant la parole (sidechain)


# ------------------------------------------------------------------
# Synthèse TTS parallèle
# ------------------------------------------------------------------
async def synth_one(text, out_mp3, voice, semaphore):
    """Synthétise une phrase (avec throttling via semaphore)."""
    import edge_tts

    async with semaphore:
        for attempt in range(3):
            try:
                await edge_tts.Communicate(text, voice).save(out_mp3)
                if os.path.exists(out_mp3) and os.path.getsize(out_mp3) > 300:
                    return True
            except Exception as exc:  # noqa: BLE001
                log.warning("edge-tts échec %d/3 : %s", attempt + 1, exc)
            await asyncio.sleep(1.5 * (attempt + 1))
    return False


async def synth_all_async(jobs, voice, max_concurrent=4):
    """Synthétise toutes les phrases en parallèle -> liste de bool."""
    import edge_tts  # noqa: F401  (présence vérifiée)

    semaphore = asyncio.Semaphore(max_concurrent)
    tasks = [synth_one(text, out, voice, semaphore) for text, out in jobs]
    return await asyncio.gather(*tasks)


def synth_all(jobs, voice, max_concurrent=4):
    """Wrapper synchrone : [(texte, sortie)] -> {sortie: ok_bool}."""
    results = asyncio.run(synth_all_async(jobs, voice, max_concurrent))
    return {out: ok for (text, out), ok in zip(jobs, results)}


def synth_zai(text, out_wav, voice, retries=2):
    """Synthèse via z-ai tts (fallback si edge-tts indisponible)."""
    for attempt in range(1, retries + 1):
        try:
            proc = subprocess.run(
                ["z-ai", "tts", "--input", text, "--output", out_wav, "--voice", voice],
                capture_output=True, text=True, timeout=180,
            )
            if proc.returncode == 0 and os.path.exists(out_wav) and os.path.getsize(out_wav) > 1000:
                return True
        except Exception as exc:  # noqa: BLE001
            log.warning("z-ai tts échec %d/%d : %s", attempt, retries, exc)
    return False


# ------------------------------------------------------------------
# Post-traitement d'une phrase
# ------------------------------------------------------------------
def trim_silences(path_in, path_out):
    """Supprime silence de tête/queue (0.3 s @ -38 dB) d'un MP3 -> WAV.

    Retourne la durée utile, ou None si échec."""
    proc = subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", path_in, "-af",
         "silenceremove=start_periods=1:start_threshold=-38dB:start_silence=0.15,"
         "areverse,"
         "silenceremove=start_periods=1:start_threshold=-38dB:start_silence=0.30,"
         "areverse",
         "-ar", "44100", path_out],
        capture_output=True, text=True,
    )
    if proc.returncode != 0 or not os.path.exists(path_out):
        return None
    return ffprobe_duration(path_out)


def speed_to_fit(path_in, path_out, slot):
    """Accélère un WAV pour tenir dans `slot` secondes (si besoin).

    Retourne la durée finale."""
    dur = ffprobe_duration(path_in)
    if dur <= 0:
        return None
    if dur <= slot * 1.02:
        # simple copie en format commun
        subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", path_in, path_out],
            capture_output=True, text=True, check=False,
        )
        return ffprobe_duration(path_out)
    factor = min(MAX_TEMPO, dur / slot)
    proc = subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", path_in,
         "-filter:a", f"atempo={factor:.3f}", path_out],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return None
    return ffprobe_duration(path_out)


# ------------------------------------------------------------------
# Doublage d'un short
# ------------------------------------------------------------------
def dub_cut(cut_id, target_lang, engine="edge"):
    """Double un short : TTS horodaté -> mixage -> *_dub.mp4."""
    cut_dir = run_rel("cuts")
    srt_path = os.path.join(cut_dir, f"cut_{cut_id}.srt")
    video_path = os.path.join(cut_dir, f"cut_{cut_id}.mp4")
    if not os.path.exists(video_path):
        log.warning("cut_%s.mp4 absent — ignoré", cut_id)
        return False

    subs = parse_srt(srt_path) if os.path.exists(srt_path) else []
    if not subs:
        log.warning("cut_%s : pas de sous-titres — doublage non fait", cut_id)
        return False

    voice = tts_voice_for(target_lang, os.environ.get("TTS_VOICE_GENDER", "male"))
    cut_dur = ffprobe_duration(video_path) or (subs[-1]["end"] + 1)
    log.info("cut_%s : %d phrases, voix %s, durée %.1fs",
             cut_id, len(subs), voice, cut_dur)

    # 1) préparation des textes (nettoyage TTS) + fichiers
    tmp_dir = os.path.join(cut_dir, f"tts_{cut_id}")
    os.makedirs(tmp_dir, exist_ok=True)
    jobs = []
    metas = []  # (wav_final, start)
    for i, sub in enumerate(subs):
        text = clean_text_for_tts(sub["text"], target_lang)
        if not text:
            continue
        slot = max(0.3, sub["end"] - sub["start"])
        raw_mp3 = os.path.join(tmp_dir, f"raw_{i:03d}.mp3")
        jobs.append((text, raw_mp3))
        metas.append({"i": i, "start": sub["start"], "slot": slot})

    if not jobs:
        log.error("cut_%s : aucune phrase synthétisable", cut_id)
        return False

    # 2) synthèse parallèle
    if engine == "edge":
        results = synth_all(jobs, voice, max_concurrent=int(os.environ.get("TTS_CONCURRENCY", "4")))
    else:
        results = {}
        for text, out in jobs:
            results[out] = synth_zai(text, out, os.environ.get("ZAI_TTS_VOICE", "tongtong"))

    pieces = []  # (wav, start)
    for meta in metas:
        raw = os.path.join(tmp_dir, f'raw_{meta["i"]:03d}.mp3')
        if not results.get(raw):
            log.warning("cut_%s : phrase %d non synthétisée — sautée", cut_id, meta["i"])
            continue
        # 3) trim des silences TTS -> positionnement exact
        trimmed = os.path.join(tmp_dir, f'trim_{meta["i"]:03d}.wav')
        dur = trim_silences(raw, trimmed)
        if dur is None:
            log.warning("cut_%s : trim phrase %d échoué — VO non doublée sur celle-ci", cut_id, meta["i"])
            continue
        # 4) accélération si débordement du créneau
        final_wav = os.path.join(tmp_dir, f'fit_{meta["i"]:03d}.wav')
        fit_dur = speed_to_fit(trimmed, final_wav, meta["slot"])
        if fit_dur is None:
            continue
        pieces.append((final_wav, meta["start"]))

    if not pieces:
        log.error("cut_%s : aucune phrase prête", cut_id)
        return False

    # 5) mixage : placement adelay + ducking sidechain de la VO
    filter_parts = []
    mix_inputs = []
    for idx, (wav, start) in enumerate(pieces):
        delay_ms = int(max(0.0, start) * 1000)
        filter_parts.append(f"[{idx + 1}:a]adelay={delay_ms}:all=1[voice{idx}]")
        mix_inputs.append(f"[voice{idx}]")

    n_voices = len(pieces)
    # VO (flux 0) atténuée dynamiquement quand une voix parle (sidechain).
    # On somme d'abord les voix (vocmix), on en dérive le signal de
    # commande (ducksig) et l'audio voix (vocsig), puis :
    #   VO x ducksig -> sidechainfilter (ducking) -> duckvo
    #   vocsig + duckvo -> amix + loudnorm -> outmix
    voices_join = "".join(f"[voice{i}]" for i in range(n_voices))
    filter_parts.append(
        f"{voices_join}amix=inputs={n_voices}:duration=longest:normalize=0[vocmix]"
    )
    filter_parts.append(f"[vocmix]asplit=2[ducksig][vocsig]")
    # ducking : la VO (flux 0) est compressée par le signal de la voix,
    # puis prolongée d'un silence (apad) pour couvrir toute la vidéo
    filter_parts.append(
        "[0:a]apad[voext]"
    )
    filter_parts.append(
        "[voext][ducksig]sidechaincompress=threshold=0.02:ratio=8:attack=50:release=300[duckvo]"
    )
    filter_parts.append(
        "[vocsig][duckvo]amix=inputs=2:duration=longest:normalize=0,"
        "loudnorm=I=-16:TP=-1.5:LRA=11[outmix]"
    )

    filter_complex = ";".join(filter_parts)

    out_video = os.path.join(cut_dir, f"cut_{cut_id}_dub.mp4")
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", video_path]
    for wav, _ in pieces:
        cmd += ["-i", wav]
    cmd += [
        "-filter_complex", filter_complex,
        "-map", "0:v", "-map", "[outmix]",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        # -t borne la sortie à la durée de la vidéo (apad produit un flux
        # infini ; -shortest se comporte mal avec les flux infinis)
        "-t", f"{cut_dur:.3f}",
        out_video,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not os.path.exists(out_video):
        log.error("cut_%s : échec mixage ffmpeg — %s", cut_id, (proc.stderr or "")[-400:])
        return False

    # 6) nettoyage des fichiers temporaires
    try:
        for f in os.listdir(tmp_dir):
            os.remove(os.path.join(tmp_dir, f))
        os.rmdir(tmp_dir)
    except OSError:
        pass

    log.info("cut_%s : doublage OK -> %s", cut_id, os.path.basename(out_video))
    return True


def main():
    force = "--force" in sys.argv
    with open(run_rel("timeline.json"), encoding="utf-8") as f:
        timeline = json.load(f)
    target_lang = timeline.get("target_lang", "fr")
    engine = os.environ.get("TTS_ENGINE", "edge")

    with open(run_rel("moments.json"), encoding="utf-8") as f:
        moments = json.load(f)

    if engine == "edge":
        try:
            import edge_tts  # noqa: F401
        except ImportError:
            log.warning("edge-tts absent — bascule sur z-ai tts")
            engine = "zai"

    done, skipped = 0, 0
    for m in moments:
        cut_id = m.get("id")
        if not cut_id:
            continue
        if not force and os.path.exists(run_rel("cuts", f"cut_{cut_id}_dub.mp4")):
            log.info("cut_%s déjà doublé — sauté (--force pour refaire)", cut_id)
            skipped += 1
            done += 1
            continue
        if dub_cut(cut_id, target_lang, engine=engine):
            done += 1

    log.info("Doublage terminé : %d/%d shorts", done, len(moments))
    mark_step("voice", "ok" if done else "error", {"dubbed": done})
    if done == 0:
        log.error("Aucun short doublé")
        sys.exit(1)


if __name__ == "__main__":
    main()
