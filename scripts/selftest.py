"""Tests unitaires du projet Mihawk v2.1 (sans réseau ni LLM).

Exécution :  python scripts/selftest.py
Sortie : OK/ÉCHEC par test + code retour 0 si tout passe.

Couvre : réparateur JSON, parsing de timestamps, normalisation de
sous-titres, nettoyage TTS, anti-hallucination, accord timeline,
clamp des moments, génération de métadonnées (fallback).
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("common", "treatment", "translate", "cute", "voice", "render", "description"):
    sys.path.insert(0, os.path.join(ROOT, sub))

os.environ.setdefault("MIHAWK_RUN", "selftest")

PASS, FAIL = 0, 0


def check(name, fn):
    global PASS, FAIL
    try:
        fn()
        print(f"  ✔ {name}")
        PASS += 1
    except AssertionError as exc:
        print(f"  ✘ {name} — {exc}")
        FAIL += 1
    except Exception as exc:  # noqa: BLE001
        print(f"  ✘ {name} — {type(exc).__name__}: {exc}")
        FAIL += 1


# ------------------------------------------------------------------
def t_json_repair():
    from mihawk_common import _repair_json
    assert _repair_json('[{"a": 1},') == [{"a": 1}]
    assert _repair_json('{"a": "x"') == {"a": "x"}
    assert _repair_json('[{"a": "va') == [{"a": "va"}]
    assert _repair_json('{"a": 1,}') == {"a": 1}
    assert _repair_json('[{"start": 1.0, "title": "Test') == [{"start": 1.0, "title": "Test"}]
    assert _repair_json('[{"s":1},{"s":2},') == [{"s": 1}, {"s": 2}]


def t_parse_timestamp():
    from mihawk_common import parse_any_timestamp
    assert parse_any_timestamp("12.5") == 12.5
    assert parse_any_timestamp("01:30") == 90.0
    assert parse_any_timestamp("01:01:05,250") == 3665.25
    assert parse_any_timestamp("2:05.5") == 125.5
    assert parse_any_timestamp(7) == 7.0
    assert parse_any_timestamp(None) is None
    assert parse_any_timestamp("abc") is None


def t_normalize_subs():
    from mihawk_common import normalize_subs
    subs = [
        {"start": 0.0, "end": 12.0, "text": "une phrase tres longue qui depasse largement les limites de caracteres et de duree pour un seul sous-titre"},
        {"start": 0.5, "end": 1.0, "text": "micro"},
        {"start": 1.0, "end": 4.0, "text": "bonjour le monde"},
    ]
    out = normalize_subs(subs)
    assert all(s["end"] > s["start"] for s in out)
    assert all(len(s["text"]) <= 90 for s in out)
    assert out[0]["start"] == 0.0
    # indexation propre
    assert [s["index"] for s in out] == list(range(1, len(out) + 1))


def t_clean_tts():
    from mihawk_common import clean_text_for_tts
    t = clean_text_for_tts("Bonjour [Musique] le monde 🎉 — «c'est» 50% du total_$")
    assert "[Musique]" not in t and "🎉" not in t and "«" not in t
    assert "pour cent" in t


def t_reading_speed():
    from mihawk_common import reading_speed, tts_voice_for
    assert reading_speed("ja") < reading_speed("fr")  # japonais plus dense
    assert tts_voice_for("fr", "male") == "fr-FR-HenriNeural"
    assert tts_voice_for("xx", "male") == "en-US-ChristopherNeural"  # fallback


def t_hallucination():
    from treatment import is_hallucination
    assert is_hallucination("...")
    assert is_hallucination("Merci d'avoir regardé")
    assert is_hallucination("et et et et et et")
    assert not is_hallucination("Une phrase normale avec des mots variés")
    assert not is_hallucination("OK", previous="OK")


def t_realign():
    sys.path.insert(0, os.path.join(ROOT, "translate"))
    from translate import realign_timeline
    subs = [
        {"index": 1, "start": 0.0, "end": 2.0, "text": "Hello"},
        {"index": 2, "start": 2.0, "end": 5.0, "text": "How are you"},
        {"index": 3, "start": 5.0, "end": 8.0, "text": "Bye"},
    ]
    trans = {1: "Bonjour", 2: "Comment allez-vous aujourd'hui mes amis", 3: "Au revoir"}
    out = realign_timeline(subs, trans, 10.0, "fr")
    assert out[0]["start"] == 0.0
    for i in range(len(out) - 1):
        assert out[i]["end"] < out[i + 1]["start"] + 0.001  # jamais de chevauchement


def t_clamp_moments():
    from cute import clamp_moments
    subs = [{"start": s, "end": s + 3.0, "text": "x"} for s in range(0, 60, 4)]
    moments = [
        {"start": 5.0, "end": 30.0, "title": "A", "score": 9},
        {"start": 20.0, "end": 55.0, "title": "B", "score": 8},
        {"start": 45.0, "end": 52.0, "title": "C", "score": 7},
    ]
    out = clamp_moments(moments, subs, duration=60, min_s=5, max_s=30, target_s=15, max_count=4)
    assert all(m["end"] > m["start"] for m in out)
    assert all(m["end"] - m["start"] <= 30.01 for m in out)
    for i in range(len(out) - 1):
        assert out[i + 1]["start"] - out[i]["end"] >= 2.9  # gap minimum


def t_algo_selection():
    from cute import algo_selection
    subs = [{"start": s, "end": s + 3.0, "text": "x"} for s in range(0, 60, 4)]
    existing = [{"start": 4, "end": 28, "title": "", "score": 9}]
    extra = algo_selection(subs, 60, 15, 4, existing)
    for e in extra:
        for m in existing:
            assert not (e["start"] < m["end"] and e["end"] > m["start"])  # pas de chevauchement


def t_description_fallback():
    from description import fallback_meta, write_outputs
    meta = fallback_meta("Ceci est le dialogue du short pour tester le fallback.", "Titre")
    assert meta["titre"] and meta["hashtags"] and meta["description"]
    base = os.path.join("/tmp", "mihawk_selftest_meta")
    write_outputs(base, meta)
    assert os.path.exists(base + ".json")
    assert os.path.exists(base + ".txt")
    assert os.path.exists(base + "_post.md")
    content = open(base + "_post.md", encoding="utf-8").read()
    assert content.startswith("# ") and "#shorts" in content


def t_render_filters():
    from render import subtitle_style
    style = subtitle_style(1920)
    assert "Fontsize=" in style
    assert int(style.split("Fontsize=")[1].split(",")[0]) > 100  # gros texte en 1080p


def main():
    print("Mihawk v2.1 — selftest")
    tests = [
        ("réparateur JSON (LLM tronqué)", t_json_repair),
        ("parsing timestamps LLM (MM:SS / secondes)", t_parse_timestamp),
        ("normalisation sous-titres (découpe/fusion/gap)", t_normalize_subs),
        ("nettoyage texte pour TTS", t_clean_tts),
        ("vitesses de lecture + voix par langue", t_reading_speed),
        ("anti-hallucination Whisper", t_hallucination),
        ("accord timeline (aucun chevauchement)", t_realign),
        ("clamp moments (bornes/gap/fusion)", t_clamp_moments),
        ("complément algorithmique (zones libres)", t_algo_selection),
        ("métadonnées fallback + post.md", t_description_fallback),
        ("style sous-titres proportionnel", t_render_filters),
    ]
    for name, fn in check and tests:
        check(name, fn)
    print()
    print(f"Résultat : {PASS} OK / {FAIL} échec(s) sur {len(tests)}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
