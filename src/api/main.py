"""Точка входа API-процесса панели: `uvicorn src.api.main:app`.

Отдельный процесс от бота (свой docker-сервис), та же Postgres и та же
бизнес-логика.
"""

from src.api.app import create_app
from src.api.container import build_api_container
from src.config import Settings
from src.infrastructure.logging.json_formatter import setup_logging

settings = Settings()
setup_logging(settings.log_level, settings.log_format, settings.log_file)
container = build_api_container(settings)
app = create_app(container)
