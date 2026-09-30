import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.config import Settings


def configure_logging(settings: Settings) -> Path:
    log_dir = Path(settings.log_dir).expanduser()
    log_dir.mkdir(parents=True, exist_ok=True)

    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(formatter)

    application_log = RotatingFileHandler(
        log_dir / "bot.log",
        maxBytes=settings.log_max_bytes,
        backupCount=settings.log_backup_count,
        encoding="utf-8",
    )
    application_log.setLevel(level)
    application_log.setFormatter(formatter)

    error_log = RotatingFileHandler(
        log_dir / "error.log",
        maxBytes=settings.log_max_bytes,
        backupCount=settings.log_backup_count,
        encoding="utf-8",
    )
    error_log.setLevel(logging.ERROR)
    error_log.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(min(level, logging.ERROR))
    root.handlers.clear()
    root.addHandler(console)
    root.addHandler(application_log)
    root.addHandler(error_log)

    return log_dir.resolve()
