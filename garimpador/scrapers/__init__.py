"""Registro de adaptadores disponíveis."""

from __future__ import annotations

from typing import Any, Callable

from garimpador.scrapers.base import BaseScraper
from garimpador.scrapers.generico import (
    ChavesNaMaoScraper,
    SiteConfig,
    SiteGenericoScraper,
    carregar_sites,
)
from garimpador.scrapers.grupozap import VivaRealScraper, ZapScraper
from garimpador.scrapers.olx import OLXScraper

Fabrica = Callable[..., BaseScraper]

PORTAIS: dict[str, Fabrica] = {
    "zap": ZapScraper,
    "vivareal": VivaRealScraper,
    "olx": OLXScraper,
    "chavesnamao": ChavesNaMaoScraper,
}


def registro() -> dict[str, Fabrica]:
    """Portais fixos + uma entrada por imobiliária do YAML (chave = `chave` do site)."""
    fabricas: dict[str, Fabrica] = dict(PORTAIS)
    for site in carregar_sites():
        def fabrica(*args: Any, _site: SiteConfig = site, **kwargs: Any) -> BaseScraper:
            return SiteGenericoScraper(_site, *args, **kwargs)
        fabricas[site.chave] = fabrica
    return fabricas


__all__ = [
    "BaseScraper",
    "ChavesNaMaoScraper",
    "OLXScraper",
    "PORTAIS",
    "SiteGenericoScraper",
    "VivaRealScraper",
    "ZapScraper",
    "registro",
]
