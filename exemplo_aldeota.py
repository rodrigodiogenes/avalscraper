"""Exemplo programático: Casa à venda na Aldeota (Fortaleza-CE), 180 a 280 m².

Execute:  python exemplo_aldeota.py
Equivale a: python -m garimpador --tipo Casa --transacao Venda --cidade Fortaleza \
            --estado CE --bairro Aldeota --area-min 180 --area-max 280
"""

import asyncio

from garimpador.browser import BrowserConfig
from garimpador.config import SearchParams
from garimpador.logging_setup import configurar_logging
from garimpador.pipeline import garimpar

if __name__ == "__main__":
    configurar_logging("INFO")
    params = SearchParams(
        tipo_imovel="Casa",
        transacao="Venda",
        cidade="Fortaleza",
        estado="CE",
        bairro="Aldeota",
        area_minima_m2=180,
        area_maxima_m2=280,
        preco_minimo=None,
        preco_maximo=None,
        max_paginas=5,
        revelar_telefone=True,
        fontes=[],  # vazio = todas; ex.: ["zap", "vivareal"]
    )
    df = asyncio.run(garimpar(params, BrowserConfig(headless=True), pasta_saida="."))
    print(df.head(20).to_string())
