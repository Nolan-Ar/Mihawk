"""Fonctions communes à tous les modules Python de Mihawk (v2.1).

Centralise : chemins du run courant, appel du LLM (z-ai chat) avec parsing
JSON tolérant, écriture SRT, utilitaires de temps, normalisation des
sous-titres, nettoyage TTS, état/resume d'un run, et mapping des langues
pour edge-tts (avec fallback).
"""
import json
import logging
import os
import re
import subprocess
import sys
import unicodedata

# ------------------------------------------------------------------
# Racine du projet et run courant
# ------------------------------------------------------------------
MIHAWK_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK_ROOT = os.environ.get("WORK_ROOT", os.path.join(MIHAWK_ROOT, "work"))

CURRENT_RUN = os.environ.get("MIHAWK_RUN", "")
RUN_DIR = os.path.join(WORK_ROOT, CURRENT_RUN) if CURRENT_RUN else WORK_ROOT


def run_rel(*parts):
    """Chemin à l'intérieur du dossier du run courant."""
    return os.path.join(RUN_DIR, *parts)


def setup_logging(name):
    """Logging commun : console + fichier logs/<name>.log."""
    os.makedirs(os.path.join(MIHAWK_ROOT, "logs"), exist_ok=True)
    level = os.environ.get("MIHAWK_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(os.path.join(MIHAWK_ROOT, "logs", f"{name}.log"), encoding="utf-8"),
        ],
        force=True,
    )
    return logging.getLogger(name)


# ------------------------------------------------------------------
# État d'un run (reprise / skipped steps)
# ------------------------------------------------------------------
def state_path():
    return run_rel("state.json")


def load_state():
    """Charge l'état du run (dict vide si absent)."""
    p = state_path()
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {"steps": {}}


def mark_step(step, status="ok", data=None):
    """Enregistre l'état d'une étape du pipeline (pour reprise)."""
    state = load_state()
    state.setdefault("steps", {})[step] = {"status": status, "data": data or {}}
    with open(state_path(), "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def step_status(step):
    return load_state().get("steps", {}).get(step, {}).get("status")


# ------------------------------------------------------------------
# Appel du LLM via le CLI z-ai
# ------------------------------------------------------------------
def llm_chat(prompt, system=None, max_retries=3, timeout=300, temperature=None):
    """Appelle le LLM (z-ai chat) et retourne le texte de la réponse.

    Retourne None en cas d'échec répété.
    """
    cmd = ["z-ai", "chat", "--prompt", prompt]
    if system:
        cmd += ["--system", system]
    out_file = run_rel(".llm_last.json")
    cmd += ["--output", out_file]

    for attempt in range(1, max_retries + 1):
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout
            )
            if proc.returncode == 0 and os.path.exists(out_file):
                with open(out_file, encoding="utf-8") as f:
                    data = json.load(f)
                content = data["choices"][0]["message"]["content"]
                return content.strip() if isinstance(content, str) else content
            logging.getLogger("common").warning(
                "llm_chat: tentative %d/%d échouée (rc=%s)", attempt, max_retries, proc.returncode
            )
        except subprocess.TimeoutExpired:
            logging.getLogger("common").warning("llm_chat: timeout tentative %d/%d", attempt, max_retries)
        except Exception as exc:  # noqa: BLE001
            logging.getLogger("common").warning("llm_chat: erreur %s", exc)
    return None


def _strip_code_fence(text):
    """Retire un éventuel bloc ```json ... ``` autour d'une réponse."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    return text.strip()


def _repair_json(text):
    """Tente de réparer un JSON tronqué (crochets/accents non fermés,
    virgule finale) — renvoie None si impossible.

    Stratégie : on scanne caractère par caractère en suivant les
    chaînes et la pile de conteneurs, puis on ferme ce qui reste
    ouvert (chaîne, virgule suspendue, conteneurs).
    """
    # 1) virgules finales immédiatement avant ] ou } déjà fermés
    text = re.sub(r",\s*([\]}])", r"\1", text)

    # 2) scan : pile de conteneurs + chaîne éventuellement ouverte
    stack = []
    in_str = False
    esc = False
    for ch in text:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "[{":
            stack.append("]" if ch == "[" else "}")
        elif ch in "]}":
            if stack:
                stack.pop()

    if not stack and not in_str:
        # rien à réparer de ce côté : peut-être une virgule pendante en fin
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            candidate = text.rstrip().rstrip(",").strip()
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                return None

    # 3) fermer proprement : chaîne ouverte -> guillemet ;
    #    virgule/deux-points pendants -> retirés ; conteneurs fermés
    #    dans l'ordre INVERSE de leur ouverture (pile renversée).
    fixed = text
    if in_str:
        fixed += '"'
    # retirer une virgule (ou deux-points) finale pendante AVANT de fermer
    fixed = re.sub(r'[,:]\s*$', "", fixed)
    fixed += "".join(reversed(stack))
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        # dernier recours : découper au dernier élément complet
        return _repair_json_truncate(text)


def _repair_json_truncate(text):
    """Coupe le texte au dernier '}' ou ']' précédé d'un élément complet
    et referme la pile restante."""
    last_complete = -1
    stack = []
    depth_at_close = None
    in_str = False
    esc = False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "[{":
            stack.append("]" if ch == "[" else "}")
        elif ch in "]}":
            if stack:
                stack.pop()
            if not stack and not in_str:
                last_complete = i
                depth_at_close = list(stack)
    if last_complete >= 0:
        candidate = text[: last_complete + 1]
        # si le conteneur racine est fermé ici, c'est un JSON complet
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            return None
    return None


def llm_json(prompt, system=None, max_retries=3, expect_type=None):
    """Appelle le LLM et parse la réponse comme JSON (tolérant).

    Gère : blocs ```json, virgules finales, JSON tronqué, et relance
    explicite si le LLM a mal répondu. expect_type ('list'|'dict')
    permet de valider le type attendu.
    """
    raw = llm_chat(prompt, system=system, max_retries=max_retries)
    if raw is None:
        return None
    text = _strip_code_fence(raw)

    candidates = []
    try:
        candidates.append(json.loads(text))
    except json.JSONDecodeError:
        repaired = _repair_json(text)
        if repaired is not None:
            candidates.append(repaired)
        # extraire le premier objet/tableau JSON du texte (LLM bavard)
        m = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
        if m:
            try:
                candidates.append(json.loads(m.group(1)))
            except json.JSONDecodeError:
                repaired2 = _repair_json(m.group(1))
                if repaired2 is not None:
                    candidates.append(repaired2)

    for cand in candidates:
        if cand is None:
            continue
        if expect_type == "list" and isinstance(cand, list):
            return cand
        if expect_type == "dict" and isinstance(cand, dict):
            return cand
        if expect_type is None and isinstance(cand, (list, dict)):
            return cand

    # dernier recours : redemander proprement
    retry = llm_chat(
        "Le texte suivant devait être du JSON valide mais il est mal formé. "
        "Retourne UNIQUEMENT le JSON corrigé, sans autre texte:\n\n" + text[:4000],
        system="Tu réponds uniquement en JSON valide.",
        max_retries=max_retries,
    )
    if retry:
        clean = _strip_code_fence(retry)
        try:
            data = json.loads(clean)
        except json.JSONDecodeError:
            data = _repair_json(clean)
        if data is not None:
            if expect_type == "list" and isinstance(data, list):
                return data
            if expect_type == "dict" and isinstance(data, dict):
                return data
            if expect_type is None and isinstance(data, (list, dict)):
                return data
    return None


# ------------------------------------------------------------------
# Temps / SRT
# ------------------------------------------------------------------
def seconds_to_srt_time(seconds):
    """Secondes -> 'HH:MM:SS,mmm'."""
    seconds = max(0.0, float(seconds))
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds - int(seconds)) * 1000))
    if ms == 1000:  # arrondi limite
        ms = 0
        s += 1
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def srt_time_to_seconds(ts):
    """'HH:MM:SS,mmm' -> secondes."""
    m = re.match(r"(\d+):(\d+):(\d+)[,.](\d+)", ts.strip())
    if not m:
        raise ValueError(f"Horodatage SRT invalide: {ts!r}")
    h, mn, s, ms = map(int, m.groups())
    return h * 3600 + mn * 60 + s + ms / 1000.0


def parse_any_timestamp(value):
    """Parse un temps renvoyé par un LLM : secondes (float/str),
    'MM:SS', 'HH:MM:SS', 'MM:SS.mmm' ou 'HH:MM:SS,mmm'. -> secondes."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    # secondes simples
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return float(text)
    # formats horloge
    m = re.fullmatch(r"(?:(\d+):)?(\d+):(\d+)(?:[.,](\d{1,3}))?", text)
    if m:
        h = int(m.group(1) or 0)
        mn = int(m.group(2))
        s = int(m.group(3))
        ms = int((m.group(4) or "0").ljust(3, "0"))
        return h * 3600 + mn * 60 + s + ms / 1000.0
    return None


def parse_srt(path):
    """Lit un fichier SRT -> liste de dicts {index, start, end, text}."""
    with open(path, encoding="utf-8-sig") as f:
        content = f.read()
    blocks = re.split(r"\n\s*\n", content.strip())
    subs = []
    for block in blocks:
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if len(lines) < 2:
            continue
        time_line = None
        text_lines = []
        for ln in lines:
            if "-->" in ln:
                time_line = ln
            elif time_line is not None:
                text_lines.append(ln)
        if time_line is None:
            continue
        start_s, end_s = [t.strip() for t in time_line.split("-->")]
        subs.append(
            {
                "start": srt_time_to_seconds(start_s),
                "end": srt_time_to_seconds(end_s.split()[0]),
                "text": " ".join(t.strip() for t in text_lines),
            }
        )
    for i, sub in enumerate(subs, 1):
        sub["index"] = i
    return subs


def normalize_subs(subs, max_dur=7.0, max_chars=84, min_gap=0.05):
    """Normalise une liste de sous-titres pour l'affichage et le doublage.

    1. ordre chronologique + indexation propre ;
    2. end > start garanti, gap minimum entre sous-titres ;
    3. découpage des sous-titres trop LONGS (> max_dur s ou > max_chars) :
       réparti en 2 sous-sous-titres équilibrés (sans couper un mot) ;
    4. fusion des micro-segments consécutifs (< 0.3 s) ;
    5. nettoyage du texte (espaces, ponctuation multiple).
    """
    subs = sorted(subs, key=lambda s: s["start"])
    cleaned = []
    for sub in subs:
        text = clean_text_for_srt(sub.get("text", ""))
        if not text:
            continue
        start = float(sub["start"])
        end = max(float(sub.get("end", start + 1.0)), start + 0.4)
        if cleaned and start - cleaned[-1]["end"] < min_gap:
            # pas de chevauchement : on pousse le début
            start = cleaned[-1]["end"] + min_gap
            if end <= start + 0.3:
                end = start + 0.4
        cleaned.append({"start": start, "end": end, "text": text})

    # fusion micro-segments
    merged = []
    for sub in cleaned:
        if (
            merged
            and sub["end"] - sub["start"] < 0.3
            and sub["start"] - merged[-1]["end"] < 0.4
            and len(merged[-1]["text"]) + len(sub["text"]) <= max_chars
        ):
            merged[-1]["end"] = sub["end"]
            merged[-1]["text"] = (merged[-1]["text"] + " " + sub["text"]).strip()
        else:
            merged.append(sub)

    # découpage des sous-titres trop longs
    result = []
    for sub in merged:
        dur = sub["end"] - sub["start"]
        n_chars = len(sub["text"])
        if dur <= max_dur and n_chars <= max_chars:
            result.append(sub)
            continue
        # nombre de parts nécessaires (par durée et par longueur)
        parts = max(2, math_ceil(dur / max_dur), math_ceil(n_chars / max_chars))
        words = sub["text"].split()
        # découpe équilibrée des mots en `parts` groupes
        groups = _split_words(words, parts)
        if len(groups) < 2:
            result.append(sub)
            continue
        chunk = dur / len(groups)
        t = sub["start"]
        for g in groups:
            text = " ".join(g)
            if text:
                result.append(
                    {"start": t, "end": t + chunk, "text": text}
                )
                t += chunk
        # recaler la fin exacte
        result[-1]["end"] = sub["end"]

    for i, sub in enumerate(result, 1):
        sub["index"] = i
    return result


def _split_words(words, parts):
    """Découpe une liste de mots en `parts` groupes quasi égaux."""
    if parts <= 1 or len(words) < parts:
        return [words]
    n = len(words)
    base = n // parts
    extra = n % parts
    groups = []
    i = 0
    for p in range(parts):
        take = base + (1 if p < extra else 0)
        groups.append(words[i:i + take])
        i += take
    return [g for g in groups if g]


def math_ceil(x):
    import math
    return math.ceil(x)


def clean_text_for_srt(text):
    """Nettoie un texte de sous-titre : espaces multiples, ponctuation,
    caractères de sous-titrage ([Musique], notes entre crochets conservées
    mais nettoyées)."""
    text = str(text).strip()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r" {2,}", " ", text)
    # enlever les balises HTML éventuelles du transcription
    text = re.sub(r"<[^>]+>", "", text)
    # normaliser les tirets d'ouverture
    text = re.sub(r"^-\s*", "", text)
    return text.strip()


def write_srt(path, subs, bilingual=None):
    """Écrit une liste de dicts {start, end, text} en SRT.

    bilingual : liste alignée de textes secondaires -> affichés en
    seconde ligne (VO en gris approximatif via le style par défaut).
    """
    with open(path, "w", encoding="utf-8") as f:
        for i, sub in enumerate(subs, 1):
            f.write(f"{i}\n")
            f.write(
                f"{seconds_to_srt_time(sub['start'])} --> "
                f"{seconds_to_srt_time(sub['end'])}\n"
            )
            f.write(sub["text"].strip() + "\n")
            if bilingual and i - 1 < len(bilingual) and bilingual[i - 1]:
                f.write(bilingual[i - 1].strip() + "\n")
            f.write("\n")


def write_plain_text(path, subs):
    """Écrit le texte brut des sous-titres (une phrase par ligne)."""
    with open(path, "w", encoding="utf-8") as f:
        for sub in subs:
            line = sub["text"].strip()
            if line:
                f.write(line + "\n")


# ------------------------------------------------------------------
# Nettoyage pour TTS
# ------------------------------------------------------------------
TTS_STRIP_PATTERNS = [
    (re.compile(r"\[[^\]]*\]"), ""),          # [Musique], [Applaudissements]
    (re.compile(r"\([^)]{0,40}\)"), ""),       # indications courantes
    (re.compile(r"[«»“”\"„]"), "" ),           # guillemets
    (re.compile(r"[_#*~`]"), ""),             # markdown résiduel
    (re.compile(r"\s*[-–—]\s*$"), ""),         # tiret de fin
    (re.compile(r"(\d)\s*%\s*"), r"\1 pour cent "),  # pourcent lisible
]

def clean_text_for_tts(text, lang="fr"):
    """Nettoie un texte pour la synthèse vocale.

    - retire les annotations [Musique], les guillemets, markdown ;
    - développe les symboles courants (%, €, &, @) en mots ;
    - retire emojis.
    """
    text = str(text)
    for pat, repl in TTS_STRIP_PATTERNS:
        text = pat.sub(repl, text)
    # emojis -> rien
    text = "".join(ch for ch in text if not (0x1F300 <= ord(ch) <= 0x1FAFF or ord(ch) in (0x2600, 0x2700)))
    # symboles courants
    text = text.replace("€", " euros ").replace("$", " dollars ").replace("&", " et ")
    if lang in ("en",):
        text = text.replace("@", " at ")
    else:
        text = text.replace("@", " at ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def simplify_for_ascii_check(text):
    return unicodedata.normalize("NFKD", text)


# ------------------------------------------------------------------
# Langues
# ------------------------------------------------------------------
# Mapping code ISO -> (voix edge-tts masculine, voix féminine)
TTS_VOICE_MAP = {
    "fr": ("fr-FR-HenriNeural", "fr-FR-DeniseNeural"),
    "en": ("en-US-ChristopherNeural", "en-US-JennyNeural"),
    "es": ("es-ES-AlvaroNeural", "es-ES-ElviraNeural"),
    "de": ("de-DE-ConradNeural", "de-DE-KatjaNeural"),
    "it": ("it-IT-DiegoNeural", "it-IT-ElsaNeural"),
    "pt": ("pt-BR-AntonioNeural", "pt-BR-FranciscaNeural"),
    "nl": ("nl-NL-MaartenNeural", "nl-NL-FennaNeural"),
    "ru": ("ru-RU-DmitryNeural", "ru-RU-SvetlanaNeural"),
    "ja": ("ja-JP-KeitaNeural", "ja-JP-NanamiNeural"),
    "ko": ("ko-KR-InJoonNeural", "ko-KR-SunHiNeural"),
    "zh": ("zh-CN-YunxiNeural", "zh-CN-XiaoxiaoNeural"),
    "ar": ("ar-EG-ShakirNeural", "ar-EG-SalmaNeural"),
    "hi": ("hi-IN-MadhurNeural", "hi-IN-SwaraNeural"),
    "pl": ("pl-PL-MarekNeural", "pl-PL-ZofiaNeural"),
    "tr": ("tr-TR-AhmetNeural", "tr-TR-EmelNeural"),
    "sv": ("sv-SE-ErikNeural", "sv-SE-SofiaNeural"),
    "da": ("da-DK-JeppeNeural", "da-DK-SaraNeural"),
    "no": ("nb-NO-IsakNeural", "nb-NO-IselinNeural"),
    "fi": ("fi-FI-HarriNeural", "fi-FI-NooraNeural"),
    "cs": ("cs-CZ-AntoninNeural", "cs-CZ-VlastaNeural"),
    "el": ("el-GR-TheodorosNeural", "el-GR-AthinaNeural"),
    "he": ("he-IL-AvriNeural", "he-IL-HilaNeural"),
    "hu": ("hu-HU-TamasNeural", "hu-HU-NoemiNeural"),
    "id": ("id-ID-ArdiNeural", "id-ID-GadisNeural"),
    "ro": ("ro-RO-AndreiNeural", "ro-RO-AlinaNeural"),
    "sk": ("sk-SK-LukasNeural", "sk-SK-ViktoriaNeural"),
    "th": ("th-TH-NiwatNeural", "th-TH-PremwadeeNeural"),
    "uk": ("uk-UA-OstapNeural", "uk-UA-PolinaNeural"),
    "vi": ("vi-VN-NamMinhNeural", "vi-VN-HoaiMyNeural"),
}

# Rythme de lecture (caractères/seconde) par langue — pour l'accord
# de durée. Le chinois/japonais se lisent différemment (idéogrammes
# denses) : moins de caractères pour le même temps.
READING_SPEED = {
    "fr": 14.0, "en": 14.0, "es": 14.5, "de": 12.5, "it": 14.5,
    "pt": 14.0, "nl": 13.5, "ru": 12.5, "ja": 5.0, "ko": 6.0,
    "zh": 4.5, "ar": 11.0, "hi": 11.0, "pl": 12.0, "tr": 12.5,
    "default": 14.0,
}


def tts_voice_for(lang, gender="male"):
    """Retourne la voix edge-tts pour une langue (fallback anglais)."""
    if lang in TTS_VOICE_MAP:
        pair = TTS_VOICE_MAP[lang]
        return pair[0] if gender == "male" else pair[1]
    return ("en-US-ChristopherNeural", "en-US-JennyNeural")[0 if gender == "male" else 1]


def reading_speed(lang):
    """Caractères par seconde pour une langue."""
    return READING_SPEED.get(lang, READING_SPEED["default"])


def language_name(code):
    """Nom FR d'une langue à partir de son code ISO."""
    names = {
        "fr": "français", "en": "anglais", "es": "espagnol", "de": "allemand",
        "it": "italien", "pt": "portugais", "nl": "néerlandais", "ru": "russe",
        "ja": "japonais", "ko": "coréen", "zh": "chinois", "ar": "arabe",
        "hi": "hindi", "pl": "polonais", "tr": "turc", "sv": "suédois",
        "da": "danois", "no": "norvégien", "fi": "finnois", "cs": "tchèque",
        "el": "grec", "he": "hébreu", "hu": "hongrois", "id": "indonésien",
        "ro": "roumain", "sk": "slovaque", "th": "thaï", "uk": "ukrainien",
        "vi": "vietnamien",
    }
    return names.get(code, code)


# ------------------------------------------------------------------
# Utilitaires processus
# ------------------------------------------------------------------
def run_cmd(cmd, timeout=600, check=False, capture=True):
    """Exécute une commande externe et loggue l'essentiel."""
    log = logging.getLogger("common")
    log.debug("run_cmd: %s", cmd)
    try:
        proc = subprocess.run(
            cmd, capture_output=capture, text=True, timeout=timeout
        )
        if check and proc.returncode != 0:
            log.error("commande échouée (%d): %s", proc.returncode, (proc.stderr or "")[-300:])
        return proc
    except subprocess.TimeoutExpired:
        log.error("timeout (%ds) sur %s", timeout, " ".join(cmd[:3]))
        return None


def ffprobe_duration(path):
    """Durée d'un média via ffprobe (0.0 si échec)."""
    proc = run_cmd(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path], timeout=30
    )
    if proc and proc.returncode == 0:
        try:
            return float(proc.stdout.strip())
        except ValueError:
            pass
    return 0.0


def ffprobe_video_size(path):
    """(largeur, hauteur) d'une vidéo via ffprobe."""
    proc = run_cmd(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", path],
        timeout=30,
    )
    if proc and proc.returncode == 0 and "x" in proc.stdout:
        try:
            w, h = proc.stdout.strip().split("x")
            return int(w), int(h)
        except ValueError:
            pass
    return 0, 0
