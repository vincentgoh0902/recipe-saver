"""uv run --env-file .env python -m recipe_saver"""

import logging

import uvicorn

from recipe_saver import config
from recipe_saver.web import create_app

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
uvicorn.run(create_app(), host="0.0.0.0", port=config.PORT, proxy_headers=True)
