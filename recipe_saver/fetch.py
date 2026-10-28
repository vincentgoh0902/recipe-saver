"""Fetch a post's caption and metadata (F3), and its audio for transcription (F5)."""

import wave
from dataclasses import dataclass
from pathlib import Path

import av
import yt_dlp


class FetchError(Exception):
    pass


@dataclass
class Post:
    id: str
    url: str
    platform: str
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
        # Short links (vt.tiktok.com) come through a different extractor; same platform.
        platform="tiktok" if "tiktok" in (info.get("extractor_key") or "").lower()
        else (info.get("extractor_key") or "web").lower(),
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
    clip), not the video's mix, so take the smallest video that has audio and decode just
    its sound (PyAV bundles ffmpeg's libraries, so no ffmpeg binary is needed)."""
    template = str(dest_dir / "source.%(ext)s")
    try:
        with _ydl(format="worst[vcodec!=none][acodec!=none]/best", outtmpl=template) as ydl:
            info = ydl.extract_info(url, download=True)
            source = Path(ydl.prepare_filename(info))
    except yt_dlp.utils.DownloadError as e:
        raise FetchError(f"could not download audio: {e}") from e

    wav = dest_dir / "audio.wav"
    try:
        _to_wav(source, wav)
    except (av.error.FFmpegError, IndexError) as e:
        raise FetchError(f"could not decode audio: {e}") from e
    source.unlink(missing_ok=True)
    return wav


def _to_wav(source: Path, wav: Path) -> None:
    resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
    with av.open(str(source)) as container, wave.open(str(wav), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16000)
        stream = container.streams.audio[0]
        for frame in container.decode(stream):
            for chunk in resampler.resample(frame):
                out.writeframes(bytes(chunk.planes[0])[: chunk.samples * 2])
        for chunk in resampler.resample(None):
            out.writeframes(bytes(chunk.planes[0])[: chunk.samples * 2])
