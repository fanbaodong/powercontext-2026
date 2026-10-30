import os
from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    db_host: str = "127.0.0.1"
    db_port: int = 2896
    db_user: str = "root"
    db_password: str = "placeholder_password"
    db_name: str = "qoder_research"

    app_host: str = "127.0.0.1"
    app_port: int = 8036

    max_upload_size_bytes: int = 10 * 1024 * 1024  # 10 MB
    log_level: str = "INFO"

    allowed_extensions: set[str] = {".txt", ".md"}

    chunk_max_chars: int = 2000

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()

STATIC_DIR = Path(__file__).resolve().parent / "static"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
