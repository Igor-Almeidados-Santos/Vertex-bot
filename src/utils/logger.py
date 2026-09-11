"""
Módulo de Logging Estruturado do Vertex-bot.
Suporta saída formatada para console e saída JSON estruturada para arquivo.
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Optional


class JSONFormatter(logging.Formatter):
    """Formatador para saída de logs em linhas JSON estruturadas."""

    def format(self, record: logging.LogRecord) -> str:
        log_data: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "module": record.name,
            "message": record.getMessage(),
        }

        # Anexar dados contextuais extras se presentes
        if hasattr(record, "event"):
            log_data["event"] = record.event
        if hasattr(record, "token_address"):
            log_data["token_address"] = record.token_address
        if hasattr(record, "extra_context"):
            log_data["context"] = record.extra_context

        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_data, default=str)


def setup_logger(
    name: str = "vertex",
    log_level: str = "INFO",
    log_file: Optional[str] = "logs/vertex.log",
) -> logging.Logger:
    """Configura e retorna uma instância configurada do logger do Vertex-bot."""
    logger = logging.getLogger(name)
    level = getattr(logging, log_level.upper(), logging.INFO)
    logger.setLevel(level)

    # Evitar adicionar múltiplos handlers se já configurado
    if logger.handlers:
        return logger

    # 1. Console Handler (formato legível e limpo)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_format = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console_handler.setFormatter(console_format)
    logger.addHandler(console_handler)

    # 2. File Handler (formato JSON estruturado)
    if log_file:
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(level)
        file_handler.setFormatter(JSONFormatter())
        logger.addHandler(file_handler)

    return logger


def mask_sensitive(data: str) -> str:
    """Mascara chaves privadas ou tokens sensíveis para exibição segura em logs."""
    if not data or len(data) <= 8:
        return "********"
    return f"{data[:4]}...{data[-4:]}"
