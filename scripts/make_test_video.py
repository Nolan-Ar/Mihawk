"""Fabrique une vidéo de test multilingue pour le pipeline Mihawk.

1. Génère des phrases FR et EN avec edge-tts (voix différentes) ;
2. Les place sur une timeline (français 0-28 s, anglais 28-58 s) ;
3. Crée une image animée (zoom lent) pour la piste vidéo ;
4. Assemble le tout en mp4 + wav 16 kHz mono.

Résultat : /home/z/my-project/mihawk/Mihawk2/tests/test_multilingue.mp4
"""
import asyncio
import os
import subprocess
import sys

OUT_DIR = "/home/z/my-project/mihawk/Mihawk2/tests"

PHRASES = [
    # (langue, voix, texte)
    ("fr", "fr-FR-HenriNeural",
     "Bonjour et bienvenue dans cette vidéo consacrée à l'intelligence artificielle."),
    ("fr", "fr-FR-HenriNeural",
     "Aujourd'hui, nous allons voir comment un modèle de langage fonctionne réellement."),
    ("fr", "fr-FR-HenriNeural",
     "L'intelligence artificielle a changé radicalement la manière dont on crée du contenu vidéo."),
    ("fr", "fr-FR-HenriNeural",
     "Et c'est là que ça devient intéressant pour les créateurs."),
    ("en", "en-US-ChristopherNeural",
     "Hello and welcome to this video about artificial intelligence."),
    ("en", "en-US-ChristopherNeural",
     "Today we will see how a large language model actually works under the hood."),
    ("en", "en-US-ChristopherNeural",
     "Machine translation has dramatically changed video production workflows."),
    ("en", "en-US-ChristopherNeural",
     "And that is where things get really interesting for content creators."),
]


async def tts(text, voice, out):
    import edge_tts
    await edge_tts.Communicate(text, voice).save(out)
    print(f"  + {os.path.basename(out)} ({voice})")


def dur(path):
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True)
    return float(p.stdout.strip())


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    tmp = os.path.join(OUT_DIR, "tmp_tts")
    os.makedirs(tmp, exist_ok=True)

    print("1) Synthèse des phrases...")
    files = []
    for i, (lang, voice, text) in enumerate(PHRASES):
        out = os.path.join(tmp, f"phrase_{i:02d}.mp3")
        if not os.path.exists(out) or os.path.getsize(out) < 100:
            asyncio.run(tts(text, voice, out))
        files.append((out, lang))

    # 2) placement sur la timeline avec 0.6 s de pause entre phrases
    print("2) Placement sur la timeline...")
    parts = []
    t = 0.0
    placements = []  # (fichier, début)
    for f, _lang in files:
        placements.append((f, t))
        t += dur(f) + 0.6
    total = t

    # adelay par fichier puis amix
    n = len(placements)
    filters = []
    for idx, (f, start) in enumerate(placements):
        ms = int(start * 1000)
        filters.append(f"[{idx}:a]adelay={ms}:all=1[a{idx}]")
    inputs = []
    for f, _ in placements:
        inputs += ["-i", f]
    mix_in = "".join(f"[a{i}]" for i in range(n))
    fc = ";".join(filters) + f";{mix_in}amix=inputs={n}:duration=longest:normalize=0[mix]"

    full_audio = os.path.join(tmp, "audio_full.mp3")
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"] + inputs +
        ["-filter_complex", fc, "-map", "[mix]", full_audio], check=True)
    print(f"   audio total: {dur(full_audio):.1f}s")

    # 3) vidéo : fond dégradé animé via lavfi (zoom lent sur image générée)
    print("3) Génération de la piste vidéo (fond animé)...")
    w, h = 1280, 720
    video_out = os.path.join(OUT_DIR, "test_multilingue.mp4")
    # fond colorimétrique animé simple : dégradé + zoompan sur lavi
    src_graph = (
        f"color=c=0x1a2b4c:s={w}x{h}:d={total + 1}:r=25,"
        f"drawtext=text='MIHAWK TEST VIDEO':fontcolor=white:fontsize=64:"
        f"x=(w-text_w)/2:y=(h-text_h)/2"
    )
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", src_graph,
         "-i", full_audio,
         "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "160k", "-shortest", video_out], check=True)

    # 4) wav 16k mono pour le pipeline
    print("4) Extraction WAV 16 kHz...")
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-i", video_out, "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
         os.path.join(OUT_DIR, "test_audio.wav")], check=True)

    print(f"OK : {video_out} ({dur(video_out):.1f}s)")


if __name__ == "__main__":
    main()
