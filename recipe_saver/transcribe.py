"""Local speech-to-text with whisper.cpp's CLI (F5). English-only model by default."""

import os
import re
import subprocess
from pathlib import Path

DEFAULT_BIN = "whisper-cli"
DEFAULT_MODEL = "models/ggml-base.en.bin"


class TranscribeError(Exception):
    pass


class Transcriber:
    def __init__(self, binary: str | None = None, model: str | None = None,
                 threads: int | None = None, timeout: float = 300):
        self.binary = binary or os.environ.get("WHISPER_BIN", DEFAULT_BIN)
        self.model = model or os.environ.get("WHISPER_MODEL", DEFAULT_MODEL)
        self.threads = threads or int(os.environ.get("WHISPER_THREADS", os.cpu_count() or 4))
        self.timeout = timeout

    def transcribe(self, wav: Path) -> str:
        if not Path(self.model).is_file():
            raise TranscribeError(f"whisper model not found at {self.model} (set WHISPER_MODEL)")
        cmd = [self.binary, "-m", self.model, "-f", str(wav), "-l", "en",
               "-t", str(self.threads), "-nt", "-np"]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout)
        except FileNotFoundError as e:
            raise TranscribeError(f"{self.binary} not found (set WHISPER_BIN)") from e
        except subprocess.TimeoutExpired as e:
            raise TranscribeError(f"transcription took over {self.timeout:.0f}s") from e
        if proc.returncode != 0:
            raise TranscribeError(f"whisper failed: {proc.stderr.strip()[:300]}")
        return _tidy(proc.stdout)


def _tidy(text: str) -> str:
    # Drop what whisper marks as non-speech: [Music], (singing in foreign language) and
    # song lyrics between ♪ marks. Then join its segment lines.
    text = re.sub(r"\[[^\]]*\]|\([^)]*\)|♪[^♪]*♪?", " ", text)
    return re.sub(r"\s+", " ", text).strip()
