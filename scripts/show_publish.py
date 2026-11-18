"""Affiche le récapitulatif publish.json d'un run (utilisé par prog.sh).

Usage : python show_publish.py <run_dir>/publish.json
"""
import json
import sys


def main():
    if len(sys.argv) < 2:
        print("Usage: show_publish.py <publish.json>")
        sys.exit(1)
    path = sys.argv[1]
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    for p in data:
        pub = p.get("publication", {})
        titre = pub.get("titre") or p.get("titre") or p["fichier"]
        tags = " ".join(pub.get("hashtags", []))
        print(f"  - {p['fichier']}  ({p.get('debut', 0):.0f}s -> {p.get('fin', 0):.0f}s, score {p.get('score')})")
        if titre:
            print(f"      Titre    : {titre}")
        kws = pub.get("keywords") or []
        if kws:
            print(f"      SEO      : {', '.join(kws)}")
        if tags:
            print(f"      Hashtags : {tags}")


if __name__ == "__main__":
    main()
