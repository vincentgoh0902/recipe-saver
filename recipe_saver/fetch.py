"""Fetch a post's caption and metadata (F3), and its audio for transcription (F5)."""

import subprocess
from dataclasses import dataclass
from pathlib import Path

import yt_dlp


class FetchError(Exception):
    pass


@dataclass
class Post:
    id: str
    url: str
    title: str | None
    author: str | None
    thumbnail: str | None
    duration: float | None
    caption: str


def _ydl(**opts) -> yt_dlp.YoutubeDL:
    return yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "noprogress": True} | opts)


def fetch_post(url: str) -> Post:
    try:
        with _ydl(skip_download=True) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as e:
        raise FetchError(f"could not fetch post: {e}") from e
    return Post(
        id=str(info.get("id")),
        url=info.get("webpage_url") or url,
        title=info.get("title"),
        author=info.get("uploader") or info.get("creator"),
        thumbnail=info.get("thumbnail"),
        duration=info.get("duration"),
        caption=info.get("description") or "",
    )


def download_audio(url: str, dest_dir: Path) -> Path:
    """Download the video's own audio and convert it to the 16 kHz mono WAV whisper.cpp
    wants. The caller owns dest_dir and deletes it afterwards (we keep text, not media).

    TikTok's audio-only format is the post's "sound" (often just the background music
    clip), not the video's mix, so take the smallest video that has audio and drop the
    picture with ffmpeg."""
    template = str(dest_dir / "source.%(ext)s")
    try:
        with _ydl(format="worst[vcodec!=none][acodec!=none]/best", outtmpl=template) as ydl:
            info = ydl.extract_info(url, download=True)
            source = Path(ydl.prepare_filename(info))
    except yt_dlp.utils.DownloadError as e:
        raise FetchError(f"could not download audio: {e}") from e

    wav = dest_dir / "audio.wav"
    proc = subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(source),
         "-vn", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(wav)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise FetchError(f"ffmpeg failed: {proc.stderr.strip()[:300]}")
    source.unlink(missing_ok=True)
    return wav
