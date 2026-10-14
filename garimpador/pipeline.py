"""Orquestração: roda os adaptadores em paralelo (um contexto por fonte) e consolida."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional

import pandas as pd

from garimpador.browser import BrowserConfig, BrowserPool
from garimpador.config import SearchParams
from garimpador.exporters import exportar, para_dataframe
from garimpador.models import Imovel
from garimpador.scrapers import registro

log = logging.getLogger("garimpador")


def fontes_selecionadas(params: SearchParams) -> list[str]:
    disponiveis = registro()
    if not params.fontes:
        return list(disponiveis)
    desconhecidas = [f for f in params.fontes if f not in disponiveis]
    if desconhecidas:
        raise ValueError(
            f"Fontes desconhecidas: {desconhecidas}. Disponíveis: {sorted(disponiveis)}"
        )
    return list(params.fontes)


async def garimpar(
    params: SearchParams,
    cfg: Optional[BrowserConfig] = None,
    pasta_saida: Path | str = ".",
    concorrencia: int = 3,
) -> pd.DataFrame:
    """Executa a busca em todas as fontes e grava CSV/JSON. Devolve o DataFrame final."""
    cfg = cfg or BrowserConfig()
    fabricas = registro()
    fontes = fontes_selecionadas(params)
    log.info("Busca: %s", params.resumo())
    log.info("Fontes (%d): %s", len(fontes), ", ".join(fontes))

    semaforo = asyncio.Semaphore(concorrencia)
    resultados: list[Imovel] = []
    resumo: dict[str, str] = {}

    async with BrowserPool(cfg) as pool:
        async def rodar(chave: str) -> None:
            async with semaforo:
                scraper = fabricas[chave](params, pool, cfg)
                try:
                    itens = await scraper.run()
                    resultados.extend(itens)
                    resumo[scraper.nome] = f"{len(itens)} válidos"
                except Exception as exc:  # noqa: BLE001 - uma fonte não derruba as outras
                    log.error("Fonte %s falhou: %s", chave, str(exc).splitlines()[0] if str(exc) else exc)
                    log.debug("Detalhe da falha em %s", chave, exc_info=True)
                    resumo[chave] = f"falhou ({type(exc).__name__})"

        await asyncio.gather(*(rodar(f) for f in fontes))

    df = para_dataframe(resultados)
    exportar(df, pasta_saida)
    for fonte, status in sorted(resumo.items()):
        log.info("  %-28s %s", fonte, status)
    log.info("Total consolidado: %d imóveis", len(df))
    return df
