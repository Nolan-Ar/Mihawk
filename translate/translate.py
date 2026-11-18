"""Module translate — traduction du SRT vers la langue cible + accord timeline (v2.1).

Améliorations v2.1 :
  - le prompt contient la DURÉE de chaque segment : le LLM adapte la
    longueur de sa traduction au temps de parole disponible (~rythme
    de lecture par langue) au lieu d'un simple "reste concis" ;
  - confirmation de langue par LLM quand la détection Whisper est
    peu sûre (flag language_uncertain) ;
  - glossaire des noms propres : extrait au premier lot et réinjecté
    dans les lots suivants (cohérence des noms sur toute la vidéo) ;
  - retry ciblé des seuls segments manquants d'un lot (plus de
    re-soumission complète) ;
  - SRT bilingue optionnel (TRANSLATE_BILINGUAL=1) : VO en seconde
    ligne — utile pour vérifier la traduction ;
  - accord timeline avec le rythme réel de la langue cible
    (READING_SPEED par langue, ex. le japonais se lit plus dense) ;
  - reprise : translated.srt existant + pas de --force -> saut.

Sorties : translated.srt (+.bilingual.srt), translated.txt,
timeline.json, glossaire.json.
"""
import json
import math
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "common"))
from mihawk_common import (  # noqa: E402
    language_name,
    llm_json,
    mark_step,
    parse_srt,
    reading_speed,
    run_rel,
    setup_logging,
    write_srt,
    write_plain_text,
)

log = setup_logging("translate")

MAX_STRETCH_FACTOR = 1.6   # on n'étire jamais un créneau au-delà de 1.6x
BATCH_SIZE = 25            # segments traduits par appel LLM


# ------------------------------------------------------------------
# Source & langue
# ------------------------------------------------------------------
def read_source():
    """Charge le SRT source + langue + titre de la vidéo."""
    srt_path = run_rel("output.srt")
    if not os.path.exists(srt_path):
        log.error("Fichier %s introuvable", srt_path)
        sys.exit(1)
    subs = parse_srt(srt_path)

    lang = "en"
    with open(run_rel("language.txt"), encoding="utf-8") as f:
        lang = f.read().strip() or "en"

    title = ""
    title_path = run_rel("title.txt")
    if os.path.exists(title_path):
        with open(title_path, encoding="utf-8") as f:
            title = f.read().strip()

    return subs, lang, title


def confirm_language(subs, lang, title):
    """Si la détection Whisper est peu sûre, demande au LLM de
    confirmer la langue sur un échantillon de segments."""
    flag = run_rel("language_uncertain.flag")
    if not os.path.exists(flag):
        return lang
    sample = " \n".join(sub["text"] for sub in subs[:12])
    data = llm_json(
        "Identifie la langue principale de ce texte de transcription vidéo "
        f"(titre: {title}). Réponds UNIQUEMENT en JSON: {{\"langue\": \"<code iso-639-1>\"}}\n\n"
        f"Texte:\n{sample}",
        system="Tu réponds uniquement en JSON valide.",
    )
    if isinstance(data, dict) and data.get("langue"):
        confirmed = str(data["langue"]).strip().lower()[:2]
        if re.match(r"[a-z]{2}", confirmed):
            log.info("Langue confirmée par LLM : %s (Whisper avait dit %s)", confirmed, lang)
            with open(run_rel("language.txt"), "w", encoding="utf-8") as f:
                f.write(confirmed)
            return confirmed
    log.warning("Confirmation LLM de la langue échouée — on garde %s", lang)
    return lang


# ------------------------------------------------------------------
# Glossaire des noms propres
# ------------------------------------------------------------------
def extract_glossary(subs, source_lang, target_lang, title):
    """Demande au LLM la liste des noms propres/noms de marques du
    document pour garantir leur cohérence dans toute la traduction."""
    sample = " \n".join(sub["text"] for sub in subs[:60])[:4000]
    data = llm_json(
        f"Liste les noms propres, marques et termes techniques présents dans ce texte "
        f"({language_name(source_lang)}), avec leur traduction correcte en "
        f"{language_name(target_lang)} s'ils doivent être traduits (vide sinon). "
        f"Titre de la vidéo : {title}\n\nTexte :\n{sample}\n\n"
        'Réponds UNIQUEMENT en JSON: {"glossaire": [{"src": "nom original", "dst": "traduction ou vide"}]} '
        "(max 15 entrées, seulement les termes réellement présents).",
        system="Tu réponds uniquement en JSON valide.",
    )
    entries = []
    if isinstance(data, dict) and isinstance(data.get("glossaire"), list):
        for item in data["glossaire"]:
            if isinstance(item, dict) and item.get("src"):
                entries.append(
                    {"src": str(item["src"]).strip(), "dst": str(item.get("dst", "") or "").strip()}
                )
    if entries:
        with open(run_rel("glossaire.json"), "w", encoding="utf-8") as f:
            json.dump(entries, f, indent=2, ensure_ascii=False)
        log.info("Glossaire : %d terme(s)", len(entries))
    return entries


def glossary_block(glossary):
    """Bloc de texte à injecter dans les prompts de traduction."""
    if not glossary:
        return "(aucun)"
    return "; ".join(
        f"{g['src']} -> {g['dst']}" if g["dst"] else g["src"]
        for g in glossary
    )


# ------------------------------------------------------------------
# Traduction par lots
# ------------------------------------------------------------------
def translate_batch(batch, source_lang, target_lang, title, glossary, cps):
    """Traduit un lot -> dict {index: traduction}."""
    lines = "\n".join(
        f'{sub["index"]}|{sub["end"] - sub["start"]:.1f}s|{sub["text"]}'
        for sub in batch
    )
    gloss = glossary_block(glossary)
    prompt = f"""Tu es un traducteur professionnel de sous-titres vidéo.
Traduis chaque segment de {language_name(source_lang)} vers le {language_name(target_lang)}.

Titre de la vidéo : {title}
Glossaire (termes à respecter) : {gloss}

SEGMENTS (numéro|durée disponible|texte source) :
{lines}

RÈGLES STRICTES :
1. Réponds UNIQUEMENT en JSON : {{"<numéro>": "<traduction>"}}
2. Un numéro pour chaque segment, aucun omis.
3. CRITIQUE — durée : la colonne 'durée' donne le temps de parole disponible.
   Ne dépasse JAMAIS environ {int(cps)} caractères par seconde : pour un segment
   de D secondes, ta traduction doit faire au plus {int(cps)}×D caractères environ.
   Si le texte source est trop dense, condense (garde le sens, coupe les répétitions).
4. Traduction naturelle et idiomatique, adaptée au doublage vidéo.
5. Ne traduis pas les noms propres ni les marques (utilise le glossaire).
6. Conserve les chiffres et unités.

Glossaire source->cible : {gloss}"""

    data = llm_json(
        prompt, system="Tu réponds uniquement en JSON valide, sans autre texte.",
    )
    translations = {}
    if isinstance(data, dict):
        for key, val in data.items():
            try:
                translations[int(key)] = str(val).strip()
            except (ValueError, TypeError):
                log.warning("Clé JSON invalide ignorée : %r", key)
    return translations


def retry_missing(batch, translations, source_lang, target_lang, glossary, cps):
    """Redemande au LLM UNIQUEMENT les segments manquants d'un lot."""
    missing = [sub for sub in batch if not translations.get(sub["index"])]
    if not missing:
        return translations
    if len(missing) == len(batch):
        return translations  # lot entièrement raté : la VO suffira
    log.info("Retry de %d segment(s) manquant(s)", len(missing))
    retried = translate_batch(missing, source_lang, target_lang, "", glossary, cps)
    translations.update(retried)
    return translations


def translate_all(subs, source_lang, target_lang, title, glossary):
    """Traduit tous les segments par lots, avec retries ciblés."""
    cps = reading_speed(target_lang)
    log.info("Traduction %s -> %s (%d segments, cible ~%d car/s)",
             source_lang, target_lang, len(subs), int(cps))
    translations = {}
    n_batches = math.ceil(len(subs) / BATCH_SIZE)
    for b in range(n_batches):
        batch = subs[b * BATCH_SIZE:(b + 1) * BATCH_SIZE]
        log.info("Lot %d/%d (segments %d-%d)...",
                 b + 1, n_batches, batch[0]["index"], batch[-1]["index"])
        part = translate_batch(batch, source_lang, target_lang, title, glossary, cps)
        part = retry_missing(batch, part, source_lang, target_lang, glossary, cps)
        for sub in batch:
            translations[sub["index"]] = part.get(sub["index"]) or sub["text"]
    return translations


# ------------------------------------------------------------------
# Accord timeline
# ------------------------------------------------------------------
def realign_timeline(subs, translations, video_duration, target_lang):
    """Met d'accord les créneaux des sous-titres avec la traduction.

    Durée nécessaire = longueur de la traduction / rythme de lecture
    de la langue cible (bornée à MAX_STRETCH_FACTOR x l'original),
    en respectant : début inchangé, fin <= début du segment suivant,
    gap minimum 0.05 s.
    """
    cps = reading_speed(target_lang)
    aligned = []
    for i, sub in enumerate(subs):
        text = translations.get(sub["index"], sub["text"])
        needed = len(text) / cps
        orig = sub["end"] - sub["start"]
        # si la VO a un créneau généreux et la traduction est courte,
        # on garde le créneau d'origine (sous-titre lisible plus longtemps)
        target_end = sub["start"] + max(orig, min(needed, orig * MAX_STRETCH_FACTOR))
        if i + 1 < len(subs):
            next_start = subs[i + 1]["start"]
            end = min(target_end, next_start - 0.05, sub["end"] + 2.0)
            end = max(end, sub["start"] + 0.4)
        else:
            end = min(max(target_end, sub["end"]), video_duration)
        aligned.append({"start": sub["start"], "end": max(end, sub["start"] + 0.4), "text": text})
    return aligned


def main():
    target_lang = sys.argv[1] if len(sys.argv) > 1 else "fr"
    bilingual = os.environ.get("TRANSLATE_BILINGUAL", "0") == "1"

    if os.path.exists(run_rel("translated.srt")) and "--force" not in sys.argv:
        log.info("translated.srt existe déjà — traduction sautée (--force pour refaire)")
        mark_step("translate", "skipped")
        return

    subs, source_lang, title = read_source()
    source_lang = confirm_language(subs, source_lang, title)

    if source_lang == target_lang:
        log.info("La vidéo est déjà en %s — traduction inutile", target_lang)
        translations = {sub["index"]: sub["text"] for sub in subs}
        glossary = []
    else:
        glossary = extract_glossary(subs, source_lang, target_lang, title)
        translations = translate_all(subs, source_lang, target_lang, title, glossary)

    video_duration = subs[-1]["end"] + 2.0
    meta_path = run_rel("meta.json")
    if os.path.exists(meta_path):
        with open(meta_path, encoding="utf-8") as f:
            video_duration = json.load(f).get("duration", video_duration)

    aligned = realign_timeline(subs, translations, video_duration, target_lang)

    # Sorties
    write_srt(run_rel("translated.srt"), aligned)
    if bilingual:
        write_srt(
            run_rel("translated.bilingual.srt"), aligned,
            bilingual=[subs[i]["text"] for i in range(len(aligned))],
        )
    write_plain_text(run_rel("translated.txt"), aligned)
    with open(run_rel("timeline.json"), "w", encoding="utf-8") as f:
        json.dump(
            {
                "source_lang": source_lang,
                "target_lang": target_lang,
                "video_duration": video_duration,
                "segments": aligned,
                "glossary": glossary,
            },
            f,
            indent=2,
            ensure_ascii=False,
        )
    mark_step("translate", "ok", {"segments": len(aligned), "target": target_lang})
    total_chars = sum(len(s["text"]) for s in aligned)
    log.info("Traduction terminée : %d sous-titres, %d caractères", len(aligned), total_chars)


if __name__ == "__main__":
    main()
