"""Milestone 3 spike: fetch a post's audio, transcribe it, save the transcript.

    uv run spike/transcribe.py URL [URL ...]
    uv run spike/transcribe.py --file spike/links.txt

Transcripts go to data/samples/<id>.transcript.txt next to the caption samples.
"""

import argparse
import sys
import tempfile
import time
from pathlib import Path

from recipe_saver.fetch import download_audio, fetch_post
from recipe_saver.transcribe import Transcriber

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "data" / "samples"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("urls", nargs="*")
    ap.add_argument("--file", type=Path)
    args = ap.parse_args()
    urls = list(args.urls)
    if args.file:
        urls += [l.strip() for l in args.file.read_text().splitlines() if l.strip()]

    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    transcriber = Transcriber()
    for url in urls:
        post = fetch_post(url)
        with tempfile.TemporaryDirectory() as tmp:
            start = time.monotonic()
            wav = download_audio(url, Path(tmp))
            fetched = time.monotonic() - start
            text = transcriber.transcribe(wav)
        total = time.monotonic() - start
        (SAMPLES_DIR / f"{post.id}.transcript.txt").write_text(text + "\n")
        print(f"=== {post.id} {post.author} ({post.duration}s audio; "
              f"download {fetched:.1f}s, total {total:.1f}s)\n{text}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
