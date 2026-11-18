# Mihawk v2.1

**Pipeline automatisé de traduction et production de shorts à partir d'une vidéo YouTube.**

Tu donnes une URL YouTube + une langue cible ; Mihawk télécharge la vidéo, la
transcrit (toutes langues, détection automatique), la traduit avec un LLM,
**choisit intelligemment les meilleurs moments**, découpe, **double la voix
avec un TTS synchronisé au dixième de seconde**, rédige titres /
descriptions / hashtags / mots-clés SEO, et rend des shorts verticaux
sous-titrés avec miniatures — prêts à publier.

```
prog.sh run <URL> <langue>
         │
         ▼
   ┌── start ────────── téléchargement vidéo + audio 16 kHz
   │
   ├── treatment ─────── transcription multilingue + anti-hallucination
   │
   ├── translate ─────── traduction LLM (lots + glossaire) + accord timeline
   │
   ├── cute ──────────── sélection LLM des moments forts + découpe parallèle
   │
   ├── voice ─────────── doublage TTS synchronisé (trim silence, ducking)
   │
   ├── description ───── titres / descriptions / hashtags / SEO (parallèle)
   │
   └── render ────────── sous-titres incrustés + 9:16 + miniatures ──► shorts/
```

---

## 1. Installation

### Prérequis

| Outil | Rôle | Vérification |
|---|---|---|
| **ffmpeg / ffprobe** | découpe, mixage, rendu | `ffmpeg -version` |
| **yt-dlp** | téléchargement YouTube | `yt-dlp --version` |
| **Python 3.10+** | tous les modules | `python3 --version` |
| **CLI `z-ai`** | LLM (traduction, sélection, descriptions) | `z-ai chat --prompt hi` |

```bash
pip install -r requirements.txt
chmod +x prog.sh common/config.sh */*.sh

# vérifier que tout est en place (outils + modules + test LLM réel) :
./prog.sh doctor
```

> **GPU** : par défaut CPU (`WHISPER_DEVICE=cpu`, `WHISPER_COMPUTE=int8`).
> Avec un GPU : `WHISPER_DEVICE=cuda WHISPER_COMPUTE=float16`.

---

## 2. Utilisation

### Commandes

```bash
./prog.sh                                # interactif (URL + langue demandés)
./prog.sh run <URL> [langue]             # pipeline complet (défaut: fr)
./prog.sh run <URL> en --quick           # modèle tiny, 3 shorts (test rapide)
./prog.sh run <URL> es --hd              # upscale 1080x1920, CRF 21
./prog.sh run <URL> de --female          # voix de doublage féminine
./prog.sh run <URL> ja --bilingual       # + SRT bilingue (VO sous traduction)
./prog.sh resume [run_dir]               # reprend un run interrompu
./prog.sh batch urls.txt fr              # traite une liste de vidéos
./prog.sh report [run_dir]               # récapitulatif de publication
./prog.sh doctor                         # vérifie l'installation
./prog.sh voices                         # 30 langues TTS mappées
```

Compatibilité v2 conservée : `./prog.sh <URL> <langue>` fonctionne toujours.

### Étapes à la carte

```bash
MIHAWK_STEPS="start,treatment" ./prog.sh run <URL> en
MIHAWK_STEPS="translate,cute,voice,description,render" ./prog.sh resume work/run_001
```

Chaque module saute automatiquement ce qui est déjà fait (state.json +
sorties existantes) ; `--force` re-force une étape.

### Configuration (common/config.sh, tout est surchargeable)

| Variable | Défaut | Description |
|---|---|---|
| `WHISPER_MODEL` | `small` | `tiny`/`base`/`small`/`medium`/`large-v3` |
| `WHISPER_DEVICE` | `cpu` | `cpu` ou `cuda` |
| `TTS_ENGINE` | `edge` | `edge` (30+ langues) ou `zai` |
| `TTS_VOICE_GENDER` | `male` | `male` ou `female` |
| `TTS_CONCURRENCY` | `4` | phrases TTS synthétisées en parallèle |
| `SHORT_MIN_SEC` | `25` | durée minimum d'un short |
| `SHORT_MAX_SEC` | `90` | durée maximum d'un short |
| `SHORT_TARGET_SEC` | `60` | durée cible d'un short |
| `MAX_SHORTS` | `10` | nombre maximum de shorts |
| `RENDER_MODE` | `vertical` | `vertical` (9:16) ou `original` |
| `RENDER_UPSCALE` | — | `1080` : upscale vertical 1080x1920 |
| `RENDER_FPS` | — | ex `30` : fps standard shorts |
| `TRANSLATE_BILINGUAL` | `0` | `1` : SRT bilingue VO+traduction |
| `LLM_CONCURRENCY` | `3` | appels LLM parallèles (description) |
| `VIDEO_CRF` | `23` | qualité x264 (plus bas = mieux) |

---

## 3. Ce que produit un run

```
work/run_001/
├── video.mp4            # vidéo téléchargée
├── audio.wav            # audio 16 kHz mono
├── title.txt            # titre (contexte LLM)
├── output.srt           # transcription normalisée (anti-hallucination)
├── language.txt          # langue détectée (code ISO)
├── translated.srt        # traduction accordée à la timeline
├── translated.bilingual.srt   # si TRANSLATE_BILINGUAL=1
├── glossaire.json       # noms propres figés (cohérence des lots)
├── timeline.json        # segments + durées (pour le doublage)
├── moments.json         # moments choisis par le LLM
├── cuts/
│   ├── cut_01.mp4       # découpe alignée sur les phrases
│   ├── cut_01.srt       # sous-titres repositionnés à 0
│   ├── cut_01_dub.mp4   # doublé (TTS calé + ducking VO + loudnorm)
│   ├── cut_01_meta.json # titre/description/hashtags/keywords
│   └── cut_01_post.md   # ► PRÊT À COPIER-COLLER à la publication
├── shorts/
│   ├── short_01.mp4     # ► FINAL : 9:16, sous-titré, doublé
│   └── thumbnail_01.jpg # miniature (frame au tiers du short)
├── publish.json         # récapitulatif complet
└── state.json           # avancement (utilisé par resume)
```

---

## 4. Les modules — ce qui a été amélioré en v2.1

### `treatment/` — transcription multilingue
faster-whisper, détection auto de langue (~100 langues). **v2.1** :
filtre d'hallucinations (boucles "merci d'avoir regardé", répétitions
de tokens sur musique), `condition_on_previous_text=False` (une erreur
ne contamine plus la suite), `initial_prompt`=titre (meilleur
vocabulaire), normalisation des sous-titres (découpe des sous-titres
trop longs, fusion des micro-segments, zéro chevauchement).

### `translate/` — traduction + accord timeline
Lots de 25 sous-titres en JSON indexé (aucun décalage possible).
**v2.1** : le prompt donne la **durée disponible de chaque segment** —
le LLM condense vraiment ; **glossaire des noms propres** extrait au
premier lot et réinjecté (cohérence sur toute la vidéo) ; retry
ciblé des seuls segments manquants ; confirmation de langue par LLM
si Whisper hésite ; accord timeline avec le **rythme de lecture réel
de la langue cible** (le japonais se lit plus dense que l'anglais) ;
SRT bilingue optionnel.

### `cute/` — découpage intelligent
Le LLM choisit les moments (score, titre, bornes). **v2.1** : parsing
tolérant des temps (`"01:30"`, `90.5`, `"2:05.5"`...), fusion des
chevauchements au lieu de les jeter, gap minimum entre shorts,
complément algorithmique des zones libres si le LLM est avare,
**découpes ffmpeg parallélisées** (3x plus rapide).

### `voice/` — doublage synchronisé
Chaque phrase synthétisée séparément puis **placée à sa position
exacte** (`adelay`+`amix`). **v2.1** : synthèse **parallèle** d'un
short entier en un seul event-loop ; **trim des silences** de
tête/queue des MP3 edge-tts (c'était la source du décalage
progressif de la v1) ; accélération `atempo` calculée après trim,
plafonnée à 1.35x (débit naturel) ; **ducking sidechaincompress**
(la VO s'atténue dynamiquement quand la voix parle) ;
**loudnorm EBU R128** (cohérence sonore entre shorts) ; nettoyage
TTS du texte ([Musique], emojis, % → "pour cent") ; durée de sortie
bornée exactement à la vidéo.

### `description/` — métadonnées de publication
LLM de chat sur le dialogue réel de chaque cut. **v2.1** :
**mots-clés SEO** (injectés dans la description) ; fichier
`post.md` par short prêt à copier-coller ; appels LLM
**parallélisés** entre shorts ; fallback propre si le LLM échoue
(jamais de vide).

### `render/` — habillage final
Sous-titres stylés proportionnels au portrait + 9:16 centré.
**v2.1** : dimensions **paires garanties** (compatibilité H.264
4:2:0 — évite les fichiers illisibles sur certains lecteurs) ;
`pix_fmt yuv420p` explicite ; **miniature JPEG** par short (frame au
tiers) ; option `RENDER_UPSCALE=1080` (mise à l'échelle propre
1080x1920) ; option `RENDER_FPS=30`.

### `common/` — socle partagé
**v2.1** : **réparateur JSON** (gère les réponses LLM tronquées,
virgules pendantes, blocs ```json) ; parsing de timestamps
multi-formats ; normalisation SRT ; nettoyage TTS ; **state.json**
(reprise) ; helpers ffprobe ; **30 langues TTS** mappées (voix
masculine ET féminine) ; vitesse de lecture par langue.

---

## 5. Langues

- **Source** (détection auto) : ~100 langues Whisper.
- **Cible (traduction)** : toutes les langues courantes du LLM.
- **Cible (voix edge-tts)** : 30 langues mappées avec voix
  masculine/féminine (fr, en, es, de, it, pt, nl, ru, ja, ko, zh, ar,
  hi, pl, tr, sv, da, no, fi, cs, el, he, hu, id, ro, sk, th, uk, vi +
  fallback anglais). Liste : `./prog.sh voices`.
- **Rythme de lecture par langue** : l'accord timeline tient compte
  des langues denses (ja/ko/zh ≈ 4-6 car/s) vs latines (≈ 14 car/s).

---

## 6. Tests intégrés

```bash
python scripts/selftest.py        # 11 tests unitaires (sans réseau)
./prog.sh doctor                  # vérif installation + test LLM réel
```

Couverts : réparateur JSON, parsing timestamps, normalisation
sous-titres, nettoyage TTS, anti-hallucination, accord timeline
(zéro chevauchement), clamp des moments, fallback métadonnées,
style sous-titres.

---

## 7. Dépannage

| Symptôme | Solution |
|---|---|
| `Sign in to confirm you're not a bot` | YouTube bloque l'IP datacenter : cookies (`yt-dlp --cookies-from-browser firefox` dans `start/start.sh`) ou proxy. |
| `TypeError: open() got ... 'metadata_errors'` | conflit av/faster-whisper : `pip install "av<15"`. |
| faster-whisper lent sur CPU | `WHISPER_MODEL=tiny` ou `base`. |
| Un short non doublé | zéro sous-titre dans le cut (silence) — voir `logs/voice.log`. |
| Lot de traduction en VO | échec LLM ponctuel : `MIHAWK_STEPS=translate ./prog.sh resume`. |
| edge-tts en échec | bascule auto sur `z-ai tts`, ou forcez `TTS_ENGINE=zai`. |
| Tout le reste | logs détaillés dans `logs/` (`treatment.log`, ..., `render.log`). |

---

## 8. Historique des versions

| Version | Apports |
|---|---|
| **v1** | pipeline initial (whisper base.en anglais-only, Mistral-7B local inutilisable sur CPU, cut algorithmique 91 s, MP3 concaténés désynchronisés, distilgpt2 ne générant rien) |
| **v2** | réécriture multilingue complète (faster-whisper, LLM API, sélection LLM des moments, placement adelay, orchestrateur 7 étapes) |
| **v2.1** | robustesse (réparateur JSON, anti-hallucination, timestamps tolérants, fallbacks partout), qualité (trim silence TTS, ducking sidechain, loudnorm, dimensions paires, miniatures), productivité (resume, batch, doctor, --quick/--hd/--female/--bilingual, parallélisme TTS/LLM/découpe, post.md, SEO, glossaire, 30 langues TTS, selftest) |

Détail complet : [CHANGELOG.md](CHANGELOG.md).

---

## 9. Licence / provenance

Projet personnel (workflow "Mihawk"). Dépendances libres :
faster-whisper (MIT), edge-tts (GPL), srt, yt-dlp. **Vérifiez les
droits de la vidéo source avant re-publication** : doublage et
sous-titres ne transfèrent pas les droits d'auteur.
