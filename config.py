"""
config.py — settings + logger.

Carrega variáveis do .env e expõe o objeto `settings` consumido pelo
restante do código. Apenas chaves efetivamente usadas estão aqui: o
projeto deixou de usar SharePoint/Excel e os IDs dos cards Metabase
(189, 194) são hardcoded em `importers/metabase.py`.
"""

import logging
import os
from dataclasses import dataclass, field
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _require(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise EnvironmentError(f"Variável de ambiente obrigatória não definida: {key}")
    return value


def _optional(key: str, default: str) -> str:
    return os.getenv(key, default)


@dataclass
class Settings:
    # Anthropic Claude
    anthropic_api_key: str
    claude_model: str        # Sonnet — raciocínio e escrita
    claude_haiku_model: str  # Haiku  — classificação e roteamento

    # Metabase
    metabase_url: str
    metabase_user: str
    metabase_password: str

    # Cache (parquets do Metabase em data/metabase/)
    cache_dir: Path
    cache_max_age_hours: int

    # Logs
    log_dir: Path
    log_level: str

    # Derivado: logger configurado
    logger: logging.Logger = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.logger = _build_logger(self.log_dir, self.log_level)


def _build_logger(log_dir: Path, log_level: str) -> logging.Logger:
    logger = logging.getLogger("tmb_churn")
    if logger.handlers:
        return logger

    level = getattr(logging, log_level.upper(), logging.INFO)
    logger.setLevel(level)

    fmt = logging.Formatter("%(message)s")

    fh = RotatingFileHandler(
        log_dir / "tmb_churn.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    return logger


def _load_settings() -> Settings:
    return Settings(
        anthropic_api_key=_require("ANTHROPIC_API_KEY"),
        claude_model=_optional("CLAUDE_MODEL", "claude-sonnet-4-6"),
        claude_haiku_model=_optional("CLAUDE_HAIKU_MODEL", "claude-haiku-4-5-20251001"),
        metabase_url=_optional("METABASE_URL", ""),
        metabase_user=_optional("METABASE_USER", ""),
        metabase_password=_optional("METABASE_PASSWORD", ""),
        cache_dir=Path(_optional("CACHE_DIR", "./data/metabase")).resolve(),
        cache_max_age_hours=int(_optional("CACHE_MAX_AGE_HOURS", "4")),
        log_dir=Path(_optional("LOG_DIR", "./logs")).resolve(),
        log_level=_optional("LOG_LEVEL", "INFO"),
    )


settings = _load_settings()
