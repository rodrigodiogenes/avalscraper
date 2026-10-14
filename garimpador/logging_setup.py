"""Configuração de logging do garimpador."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

FORMATO = "%(asctime)s | %(levelname)-7s | %(name)-34s | %(message)s"


def configurar_logging(nivel: str = "INFO", arquivo: Optional[Path] = None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if arquivo:
        handlers.append(logging.FileHandler(arquivo, encoding="utf-8"))
    logging.basicConfig(level=nivel.upper(), format=FORMATO, datefmt="%H:%M:%S",
                        handlers=handlers, force=True)
    # Bibliotecas ruidosas
    for nome in ("asyncio", "urllib3", "playwright"):
        logging.getLogger(nome).setLevel(logging.WARNING)
