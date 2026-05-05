import logging
import os
from dataclasses import dataclass, field
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import unquote, urlparse

from dotenv import load_dotenv

load_dotenv()


def _require(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise EnvironmentError(f"Variável de ambiente obrigatória não definida: {key}")
    return value


def _optional(key: str, default: str) -> str:
    return os.getenv(key, default)


def _parse_sharepoint_url(url: str) -> tuple[str, str]:
    """Extrai (site_url, server_relative_path) de uma URL completa do SharePoint."""
    decoded = unquote(url)
    parsed = urlparse(decoded)
    site_url = f"{parsed.scheme}://{parsed.netloc}"
    server_relative_path = parsed.path
    return site_url, server_relative_path


@dataclass
class Settings:
    # Anthropic Claude
    anthropic_api_key: str
    claude_model: str        # Sonnet — raciocínio e escrita
    claude_haiku_model: str  # Haiku  — classificação e roteamento

    # Arquivo local (alternativa ao SharePoint — útil com MFA/OneDrive)
    excel_local_path: Path | None   # None se EXCEL_LOCAL_PATH não definido

    # SharePoint (mantido para compatibilidade)
    excel_file_path: str           # URL original do .env
    sharepoint_site_url: str       # extraído automaticamente
    sharepoint_file_path: str      # caminho relativo ao servidor, extraído automaticamente
    sharepoint_username: str
    sharepoint_password: str

    # Metabase
    metabase_url: str
    metabase_user: str
    metabase_password: str
    metabase_db_id: int
    metabase_table_vendas: int
    metabase_table_produtores: int

    # Cache
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

    # Arquivo rotativo: 5 MB, 3 backups
    fh = RotatingFileHandler(
        log_dir / "tmb_churn.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    # Console (INFO+)
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    return logger


def _load_settings() -> Settings:
    excel_url = _optional("EXCEL_FILE_PATH", "")
    site_url, file_path = _parse_sharepoint_url(excel_url) if excel_url else ("", "")

    local_path_str = _optional("EXCEL_LOCAL_PATH", "")
    excel_local_path = Path(local_path_str).resolve() if local_path_str else None

    return Settings(
        anthropic_api_key=_require("ANTHROPIC_API_KEY"),
        claude_model=_optional("CLAUDE_MODEL", "claude-sonnet-4-6"),
        claude_haiku_model=_optional("CLAUDE_HAIKU_MODEL", "claude-haiku-4-5-20251001"),
        excel_local_path=excel_local_path,
        excel_file_path=excel_url,
        sharepoint_site_url=site_url,
        sharepoint_file_path=file_path,
        sharepoint_username=_optional("SHAREPOINT_USERNAME", ""),
        sharepoint_password=_optional("SHAREPOINT_PASSWORD", ""),
        metabase_url=_optional("METABASE_URL", ""),
        metabase_user=_optional("METABASE_USER", ""),
        metabase_password=_optional("METABASE_PASSWORD", ""),
        metabase_db_id=int(_optional("METABASE_DB_ID", "3")),
        metabase_table_vendas=int(_optional("METABASE_TABLE_VENDAS", "645")),
        metabase_table_produtores=int(_optional("METABASE_TABLE_PRODUTORES", "626")),
        cache_dir=Path(_optional("CACHE_DIR", "./cache")).resolve(),
        cache_max_age_hours=int(_optional("CACHE_MAX_AGE_HOURS", "4")),
        log_dir=Path(_optional("LOG_DIR", "./logs")).resolve(),
        log_level=_optional("LOG_LEVEL", "INFO"),
    )


settings = _load_settings()
