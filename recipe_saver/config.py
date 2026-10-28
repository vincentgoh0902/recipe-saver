"""Settings from environment variables (see .env.example)."""

import os
from pathlib import Path

PORT = int(os.environ.get("PORT", "8000"))
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
DB_PATH = DATA_DIR / "recipes.db"
THUMBS_DIR = DATA_DIR / "thumbs"
