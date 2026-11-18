"""Module treatment — transcription multilingue de l'audio (v2.1).

Améliorations v2.1 :
  - anti-hallucination : filtre des segments répétitifs (boucles de
    Whisper sur musique/silence) et des segments sans lettres ;
  - `condition_on_previous_text=False` : empêche l'effet boule de
    neige où une erreur de contexte se propage à toute la suite ;
  - `initial_prompt` avec le titre de la vidéo : oriente la
    reconnaissance (vocabulaire du sujet) sans forcer ;
  - normalisation des sous-titres (normalize_subs) : découpe des
    sous-titres trop longs, fusion des micro-segments, zéro
    chevauchement -> base propre pour traduire et doubler ;
  - reprise : si output.srt existe déjà et que --force n'est pas
    passé, on ne retranscrit pas.

Sorties dans le dossier du run :
  - output.srt / original.txt : transcription normalisée
  - language.txt : code ISO de la langue détectée (+ .flag si la
    détection est peu sûre, le module translate tranchera via LLM)
  - meta.json : durée, segments, modèle, confiance, hallucinations
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "common"))
from mihawk_common import (  # noqa: E402
    mark_step,
    normalize_subs,
    run_rel,
    setup_logging,
    write_plain_text,
    write_srt,
    language_name,
)

log = setup_logging("treatment")

# Motifs classiques d'hallucination Whisper (répétitions / artefacts)
HALLUCINATION_PATTERNS = [
    re.compile(r"^(so|thank you)[\s.,]*$", re.IGNORECASE),
    re.compile(r"^subscri", re.IGNORECASE),
    re.compile(r"^(merci d'avoir regardé|thanks for watching)[\s.!]*$", re.IGNORECASE),
    re.compile(r"^\W*$"),  # ponctuation seule
]


def is_hallucination(text, previous=None):
    """Détecte les segments hallucinés : motifs connus, répétition
    extrême du même token, segment identique au précédent."""
    text = text.strip()
    if not re.search(r"[a-zA-Zà-öø-ÿÀ-ÖØ-ÿ\u0400-\u04ff\u0600-\u06ff\u4e00-\u9fff]", text):
        return True  # aucune lettre -> artefact
    for pat in HALLUCINATION_PATTERNS:
        if pat.match(text):
            return True
    # répétition d'un même token (ex: "et et et et et")
    words = re.findall(r"\w+", text.lower())
    if len(words) >= 6:
        if len(set(words)) <= 2:  # 1-2 mots uniques sur 6+ -> boucle
            return True
    # identique au précédent (hors phrases très courtes légitimes)
    if previous and text.lower() == previous.lower() and len(words) > 3:
        return True
    return False


def transcribe(audio_path, model_name, device, compute, title):
    """Transcrit et retourne (subs normalisés, infos)."""
    from faster_whisper import WhisperModel

    log.info("Chargement du modèle faster-whisper '%s' (%s/%s)...", model_name, device, compute)
    model = WhisperModel(model_name, device=device, compute_type=compute)

    # initial_prompt : le titre oriente le vocabulaire (noms propres,
    # thème) ; on le garde court pour ne pas biaiser la langue.
    initial_prompt = (title[:200] + ".") if title else None

    log.info("Transcription multilingue de %s ...", audio_path)
    segments_iter, info = model.transcribe(
        audio_path,
        beam_size=5,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
        condition_on_previous_text=False,  # anti-propagation d'erreurs
        initial_prompt=initial_prompt,
    )

    detected, prob = info.language, info.language_probability
    log.info("Langue détectée : %s (confiance %.1f%%)", language_name(detected), prob * 100)

    raw = []
    prev_text = None
    hallucinated = 0
    for seg in segments_iter:
        text = seg.text.strip()
        if not text:
            continue
        if is_hallucination(text, prev_text):
            hallucinated += 1
            log.debug("hallucination ignorée: %r", text[:60])
            continue
        prev_text = text
        raw.append({"start": seg.start, "end": seg.end, "text": text})

    if hallucinated:
        log.info("%d segment(s) halluciné(s) filtré(s)", hallucinated)

    subs = normalize_subs(raw)
    return subs, {"language": detected, "prob": prob, "duration": info.duration,
                  "raw_segments": len(raw), "hallucinated": hallucinated}


def main():
    audio_file = run_rel("audio.wav")
    if not os.path.exists(audio_file):
        log.error("Fichier audio introuvable : %s", audio_file)
        sys.exit(1)

    force = "--force" in sys.argv
    existing = run_rel("output.srt")
    if os.path.exists(existing) and not force:
        log.info("output.srt existe déjà — transcription sautée (--force pour refaire)")
        mark_step("treatment", "skipped")
        return

    model_name = os.environ.get("WHISPER_MODEL", "small")
    device = os.environ.get("WHISPER_DEVICE", "cpu")
    compute = os.environ.get("WHISPER_COMPUTE", "int8")

    title = ""
    title_path = run_rel("title.txt")
    if os.path.exists(title_path):
        with open(title_path, encoding="utf-8") as f:
            title = f.read().strip()

    subs, info = transcribe(audio_file, model_name, device, compute, title)

    if not subs:
        log.error("Aucun segment transcrit — audio illisible ?")
        mark_step("treatment", "error")
        sys.exit(1)

    write_srt(run_rel("output.srt"), subs)
    write_plain_text(run_rel("original.txt"), subs)
    with open(run_rel("language.txt"), "w", encoding="utf-8") as f:
        f.write(info["language"] or "en")
    # détection peu sûre (< 70 %) : flag pour que translate tranche
    if info["prob"] < 0.70:
        with open(run_rel("language_uncertain.flag"), "w", encoding="utf-8") as f:
            f.write(f"confiance={info['prob']:.2f}")
    with open(run_rel("meta.json"), "w", encoding="utf-8") as f:
        json.dump(
            {
                "language": info["language"],
                "language_probability": info["prob"],
                "duration": info["duration"],
                "segments": len(subs),
                "raw_segments": info["raw_segments"],
                "hallucinations_filtered": info["hallucinated"],
                "model": model_name,
            },
            f,
            indent=2,
        )
    mark_step("treatment", "ok", {"segments": len(subs), "language": info["language"]})
    log.info("Transcription terminée : %d segments (hors %d hallucinations) -> output.srt",
             len(subs), info["hallucinated"])


if __name__ == "__main__":
    main()
