# CHANGELOG — Mihawk

## v2.1 — Robustesse, qualité, productivité

### Robustesse
- **Réparateur JSON** (`common`) : les réponses LLM tronquées (crochets non
  fermés, chaînes ouvertes, virgules pendantes, blocs ```json) sont réparées
  au lieu d'être rejetées — un lot de traduction ne échoue plus pour une
  accolade manquante.
- **Anti-hallucination Whisper** (`treatment`) : filtre des segments
  répétitifs ("merci d'avoir regardé" bouclé, tokens répétés sur musique,
  segments sans lettres) ; `condition_on_previous_text=False` pour éviter la
  contamination du contexte ; `initial_prompt` = titre de la vidéo.
- **Timestamps tolérants** (`cute`) : le LLM peut répondre `"01:30"`,
  `90.5`, `"2:05.5"` ou `3665.25` — tout est parse et validé, moments
  illisibles ignorés avec warning.
- **Confirmation de langue** (`translate`) : si Whisper hésite (<70 %),
  le LLM confirme la langue sur un échantillon avant de traduire.
- **Fallback partout** : découpage algorithmique si le LLM ne propose rien
  ; complément des zones libres si le LLM en propose trop peu ;
  métadonnées minimales si le LLM échoue ; VO conservée sur un segment non
  traduit ; bascule z-ai tts si edge-tts tombe.
- **state.json + resume** : chaque étape marque son avancement ; `prog.sh
  resume` reprend un run interrompu sans refaire ce qui est déjà fait
  (chaque module saute ses sorties existantes, `--force` les refait).

### Qualité
- **Trim des silences TTS** (`voice`) : suppression du silence de
  tête/queue des MP3 edge-tts avant placement — la synchro voix/image est
  maintenant exacte (c'était la cause du décalage progressif).
- **Ducking sidechaincompress** (`voice`) : la VO s'atténue dynamiquement
  quand la voix doublée parle, au lieu d'être écrasée à un volume fixe.
- **Loudnorm EBU R128** (`voice`) : niveau sonore homogène entre tous les
  shorts (-16 LUFS, TP -1.5 dB).
- **Accélération plafonnée 1.35x** (`voice`) : atempo calculé sur la durée
  réelle après trim — un débordement résiduel passe dans le gap au lieu
  d'une voix artificiellement rapide.
- **Dimensions paires + yuv420p** (`render`) : crop/scale toujours pairs
  (H.264 4:2:0), plus de fichiers illisibles sur certains lecteurs.
- **Sous-titres proportionnels** (`render`) : taille calculée sur la
  hauteur réelle du rendu (gros texte en portrait), remontés pour laisser
  la place aux UI TikTok/Shorts.
- **Miniatures** (`render`) : `thumbnail_XX.jpg` (frame au tiers du short)
  pour chaque vidéo.
- **Normalisation des sous-titres** (`common`) : découpe des sous-titres
  trop longs (>7 s / >84 chars) en sous-sous-titres équilibrés sans couper
  un mot, fusion des micro-segments, zéro chevauchement.
- **Nettoyage TTS** (`common`) : [Musique], emojis, guillemets, % →
  "pour cent", $ → "dollars" — la voix ne lit plus les artefacts.
- **Durées dans le prompt de traduction** (`translate`) : le LLM voit le
  temps de parole disponible par segment et condense réellement.
- **Glossaire des noms propres** (`translate`) : extrait au premier lot,
  réinjecté dans les suivants — cohérence des noms sur toute la vidéo.
- **Rythme de lecture par langue** (`common`/`translate`) : l'accord
  timeline utilise ~14 car/s pour les langues latines, ~5 pour le japonais
  (idéogrammes denses).
- **Accélération de découpe** (`cute`) : `-ss` avant `-i` (seek rapide) +
  découpes ffmpeg en ThreadPool (3 simultanées).

### Productivité
- **CLI complet** (`prog.sh`) : `run`, `resume`, `batch urls.txt`,
  `report`, `doctor` (vérif installation + test LLM réel), `voices`
  (30 langues mappées).
- **Flags** : `--quick` (modèle tiny + 3 shorts), `--hd` (upscale 1080 +
  CRF 21), `--female` (voix féminine), `--bilingual` (SRT VO+traduction).
- **Parallélisme** : TTS d'un court entier en un event-loop asyncio
  (`TTS_CONCURRENCY`), appels LLM description en ThreadPool
  (`LLM_CONCURRENCY`), découpes ffmpeg en ThreadPool.
- **Mots-clés SEO** (`description`) : expressions de recherche générées et
  injectées dans la description ; `post.md` par short, prêt à
  copier-coller (titre + description + hashtags).
- **30 langues TTS** (`common`) : voix masculine et féminine pour fr, en,
  es, de, it, pt, nl, ru, ja, ko, zh, ar, hi, pl, tr, sv, da, no, fi, cs,
  el, he, hu, id, ro, sk, th, uk, vi (+ fallback anglais).
- **Selftest** (`scripts/selftest.py`) : 11 tests unitaires sans réseau —
  réparateur JSON, timestamps, normalisation, anti-hallucination, accord
  timeline, clamp moments, fallback métadonnées, style sous-titres.
- **Compatibilité v2** : `./prog.sh <URL> <langue>` continue de
  fonctionner comme avant.

## v2 — Réécriture multilingue complète

- faster-whisper (multilingue, détection auto) au lieu de
  `whisper base.en` (anglais uniquement).
- Traduction LLM via API (Mistral-7B local était hors de portée sur CPU) ;
  lots JSON indexés ; réalignement de la timeline par segment.
- Découpage piloté par LLM (moments pertinents + score) au lieu de blocs
  équidistants de 91 s ; bornes alignées sur les phrases.
- Doublage : synthèse par phrase + placement `adelay`+`amix` (la v1
  concaténait les MP3 et se désynchronisait progressivement).
- Description : LLM de chat sur le dialogue réel (la v1 lisait un
  dialogue.txt jamais créé avec distilgpt2).
- Render : sous-titres incrustés + 9:16 + publish.json (la v1 avait un
  dossier render vide).
- prog.sh orchestre les 7 modules (la v1 s'arrêtait après cute).
- Chemins relatifs + config.sh central + runs isolés work/run_XXX (la v1
  avait des chemins /home/nono en dur).

## v1 — Pipeline initial

- start (yt-dlp) → treatment (whisper base.en) → translate (Mistral-7B
  local) → cute (blocs de 91 s) ; voice/description/render cassés ou
  inexistants ; chemins en dur ; anglais uniquement.
