"""Module description — métadonnées de publication pour chaque short (v2.1).

Améliorations v2.1 :
  - MOTS-CLÉS SEO : le LLM produit aussi des mots-clés de recherche
    (intégrés à la description) — visibilité YouTube/TikTok ;
  - fichier POST.md par short : titre + description + hashtags
    formatés prêt à copier-coller au moment de publier ;
  - appels LLM PARALLÉLISÉS entre shorts (les shorts sont
    indépendants) -> 3-4x plus rapide sur une grosse vidéo ;
  - validation stricte de la réponse (fallback propre si le LLM
    délire : on garde un jeu minimal exploitable plutôt que rien) ;
  - reprise : cut_XX_meta.json existant sauf --force.

Sorties :
  - cuts/cut_XX_meta.json|.txt : {titre, description, hashtags, keywords}
  - cuts/cut_XX_post.md : prêt à copier-coller
  - video_meta.json|.txt + video_post.md : pour la vidéo complète
"""
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "common"))
from mihawk_common import llm_json, mark_step, parse_srt, run_rel, setup_logging, language_name  # noqa: E402

log = setup_logging("description")


def read_dialogue_for_cut(cut_id, moment):
    """Dialogue du short = texte des sous-titres du cut + titre du moment."""
    srt_path = run_rel("cuts", f"cut_{cut_id}.srt")
    text = ""
    if os.path.exists(srt_path):
        subs = parse_srt(srt_path)
        text = " ".join(s["text"] for s in subs)
    if len(text) > 6000:
        text = text[:3000] + " [...] " + text[-3000:]
    return text, moment.get("title", "")


def read_video_context():
    """Contexte global : titre de la vidéo + dialogue traduit complet."""
    title = ""
    title_path = run_rel("title.txt")
    if os.path.exists(title_path):
        with open(title_path, encoding="utf-8") as f:
            title = f.read().strip()

    dialogue = ""
    for name in ("translated.txt", "original.txt"):
        path = run_rel(name)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                dialogue = f.read()
            break
    if len(dialogue) > 8000:
        dialogue = dialogue[:4000] + "\n[...]\n" + dialogue[-4000:]

    target = "fr"
    timeline_path = run_rel("timeline.json")
    if os.path.exists(timeline_path):
        with open(timeline_path, encoding="utf-8") as f:
            target = json.load(f).get("target_lang", "fr")
    return title, dialogue, target


def generate_meta(dialogue, context_title, target_lang, is_video=False):
    """Un appel LLM -> {titre, description, hashtags, keywords}."""
    kind = "vidéo longue" if is_video else "short vertical"
    prompt = f"""Tu es un expert en marketing de contenu pour réseaux sociaux.
Rédige les métadonnées de publication d'un {kind}, en {language_name(target_lang)}.

Titre/contexte général : {context_title}
Dialogue de l'extrait :
{dialogue}

Réponds UNIQUEMENT en JSON valide :
{{
  "titre": "titre accrocheur, max 90 caractères, avec une émotion ou une promesse claire",
  "description": "description de 2 à 4 phrases, tutoiement, incitant au visionnage et à l'abonnement",
  "hashtags": ["#motclé1", "#motclé2", "#motclé3", "#motclé4", "#motclé5"],
  "keywords": ["mot-clé de recherche 1", "mot-clé 2", "mot-clé 3"]
}}

Règles : hashtags en {language_name(target_lang)} + anglais (mots-clés génériques type #shorts
acceptés), 5 à 8 hashtags, sans doublons, en minuscules, pertinents au contenu réel de l'extrait.
Les keywords sont des expressions de RECHERCHE (2-3 mots) que taperait quelqu'un intéressé,
sans #."""

    data = llm_json(prompt, system="Tu réponds uniquement en JSON valide, sans autre texte.",
                    expect_type="dict")
    if not isinstance(data, dict) or not data.get("titre"):
        log.warning("Réponse LLM invalide pour '%s'", context_title[:40])
        return None

    # Nettoyage hashtags
    tags = data.get("hashtags", [])
    if isinstance(tags, str):
        tags = [t for t in re.split(r"[\s,]+", tags) if t]
    clean_tags = []
    for t in tags:
        t = t.strip().lstrip("#").replace(" ", "")
        if t and f"#{t}" not in clean_tags:
            clean_tags.append(f"#{t}")

    # Nettoyage keywords
    kws = data.get("keywords", [])
    if isinstance(kws, str):
        kws = [k for k in re.split(r"[\n,]+", kws) if k.strip()]
    clean_kws = [str(k).strip().lower() for k in kws if str(k).strip()][:6]

    titre = str(data["titre"]).strip()[:120]
    description = str(data.get("description", "")).strip()
    # injecter les mots-clés SEO dans la description (zone tags YouTube)
    if clean_kws:
        description = description.rstrip() + "\n\n" + ", ".join(clean_kws)

    return {
        "titre": titre,
        "description": description,
        "hashtags": clean_tags[:8],
        "keywords": clean_kws,
    }


def fallback_meta(dialogue, context_title):
    """Jeu minimal exploitable si le LLM échoue (jamais vide)."""
    first_words = " ".join(dialogue.split()[:12])
    return {
        "titre": (context_title or first_words or "Extrait vidéo")[:90],
        "description": (dialogue[:200] + "...").strip(),
        "hashtags": ["#shorts", "#video"],
        "keywords": [],
    }


def write_outputs(base, meta):
    """Écrit le couple JSON + TXT pour un jeu de métadonnées."""
    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
    with open(base + ".txt", "w", encoding="utf-8") as f:
        f.write("TITRE\n" + meta["titre"] + "\n\n")
        f.write("DESCRIPTION\n" + meta["description"] + "\n\n")
        f.write("HASHTAGS\n" + " ".join(meta["hashtags"]) + "\n")
        if meta.get("keywords"):
            f.write("\nMOTS-CLES SEO\n" + ", ".join(meta["keywords"]) + "\n")
    # post.md : prêt à copier-coller dans la plateforme
    with open(base + "_post.md", "w", encoding="utf-8") as f:
        f.write(f"# {meta['titre']}\n\n")
        f.write(meta["description"] + "\n\n")
        f.write(" ".join(meta["hashtags"]) + "\n")


def process_one(m, title, target, force=False):
    """Génère les métadonnées d'un short (appelé en parallèle)."""
    cut_id = m.get("id")
    if not cut_id:
        return False
    base = run_rel("cuts", f"cut_{cut_id}_meta")
    if not force and os.path.exists(base + ".json"):
        log.info("cut_%s : métadonnées existantes — sauté", cut_id)
        return True
    dialogue, moment_title = read_dialogue_for_cut(cut_id, m)
    if not dialogue:
        log.warning("cut_%s : pas de dialogue — ignoré", cut_id)
        return False
    meta = generate_meta(dialogue, f"{title} — {moment_title}", target)
    if meta is None:
        meta = fallback_meta(dialogue, f"{title} — {moment_title}")
        log.info("cut_%s : fallback (LLM indisponible)", cut_id)
    write_outputs(base, meta)
    log.info("cut_%s : %s", cut_id, meta["titre"])
    return True


def main():
    force = "--force" in sys.argv
    title, dialogue, target = read_video_context()
    if not dialogue:
        log.error("Aucun dialogue trouvé (translated.txt / original.txt)")
        mark_step("description", "error")
        sys.exit(1)

    moments = []
    moments_path = run_rel("moments.json")
    if os.path.exists(moments_path):
        with open(moments_path, encoding="utf-8") as f:
            moments = json.load(f)

    # 1) métadonnées par short — en parallèle (LLM indépendants)
    ok = 0
    with ThreadPoolExecutor(max_workers=int(os.environ.get("LLM_CONCURRENCY", "3"))) as pool:
        results = list(
            pool.map(lambda m: process_one(m, title, target, force), moments)
        )
    ok = sum(1 for r in results if r)

    # 2) métadonnées pour la vidéo complète
    base = run_rel("video_meta")
    if force or not os.path.exists(base + ".json"):
        video_meta = generate_meta(dialogue, title, target, is_video=True)
        if video_meta is None:
            video_meta = fallback_meta(dialogue, title)
        write_outputs(base, video_meta)
        log.info("Métadonnées vidéo : %s", video_meta["titre"])

    mark_step("description", "ok", {"shorts": ok})
    log.info("Module description terminé (%d shorts + vidéo)", ok)


if __name__ == "__main__":
    main()
