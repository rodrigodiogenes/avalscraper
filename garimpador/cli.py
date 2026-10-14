"""Interface de linha de comando.

Exemplos:
    python -m garimpador                                   # cenário padrão (Aldeota)
    python -m garimpador --tipo Apartamento --bairro Meireles --area-min 90 --area-max 150
    python -m garimpador --fontes zap vivareal --max-paginas 2 --sem-telefone
    python -m garimpador listar-fontes
    python -m garimpador recon https://www.magnomuniz.com.br/imoveis/venda
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Optional, Sequence

from garimpador.browser import BrowserConfig
from garimpador.config import SearchParams
from garimpador.logging_setup import configurar_logging


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="garimpador",
        description="Garimpador de dados imobiliários (ZAP, VivaReal, OLX, Chaves na Mão e imobiliárias).",
    )
    p.add_argument("--tipo", dest="tipo_imovel", default="Casa",
                   help="Casa, Apartamento, Terreno, 'Casa de Condominio', Sala, Loja, Galpao")
    p.add_argument("--transacao", default="Venda", help="Venda ou Aluguel")
    p.add_argument("--cidade", default="Fortaleza")
    p.add_argument("--estado", default="CE")
    p.add_argument("--bairro", default="Aldeota", help="Use '' para a cidade inteira")
    p.add_argument("--area-min", dest="area_minima_m2", type=float, default=180)
    p.add_argument("--area-max", dest="area_maxima_m2", type=float, default=280)
    p.add_argument("--preco-min", dest="preco_minimo", type=float, default=None)
    p.add_argument("--preco-max", dest="preco_maximo", type=float, default=None)
    p.add_argument("--fontes", nargs="*", default=[],
                   help="Chaves das fontes (veja 'listar-fontes'). Padrão: todas.")
    p.add_argument("--max-paginas", type=int, default=5)
    p.add_argument("--sem-telefone", action="store_true",
                   help="Não clica em 'ver telefone' (mais rápido e discreto).")
    p.add_argument("--headful", action="store_true", help="Abre o navegador visível (depuração).")
    p.add_argument("--proxy", default=None, help="http://user:senha@host:porta")
    p.add_argument("--chromium", default=None,
                   help="Caminho de um Chromium já instalado (alternativa ao playwright install).")
    p.add_argument("--delay-min", type=float, default=1.0)
    p.add_argument("--delay-max", type=float, default=3.0)
    p.add_argument("--concorrencia", type=int, default=3, help="Fontes rodando ao mesmo tempo.")
    p.add_argument("--saida", type=Path, default=Path("."), help="Pasta dos arquivos gerados.")
    p.add_argument("--log", default="INFO", help="DEBUG, INFO, WARNING")
    p.add_argument("--log-arquivo", type=Path, default=None)
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if argv[:1] == ["listar-fontes"]:
        from garimpador.scrapers import registro

        for chave in registro():
            print(chave)
        return 0

    if argv[:1] == ["recon"]:
        if len(argv) < 2:
            print("uso: python -m garimpador recon <url> [--headful]", file=sys.stderr)
            return 2
        from garimpador.recon import reconhecer

        configurar_logging("INFO")
        asyncio.run(reconhecer(argv[1], cfg=BrowserConfig(headless="--headful" not in argv)))
        return 0

    args = _parser().parse_args(argv)
    configurar_logging(args.log, args.log_arquivo)
    try:
        params = SearchParams(
            tipo_imovel=args.tipo_imovel,
            transacao=args.transacao,
            cidade=args.cidade,
            estado=args.estado,
            bairro=args.bairro,
            area_minima_m2=args.area_minima_m2,
            area_maxima_m2=args.area_maxima_m2,
            preco_minimo=args.preco_minimo,
            preco_maximo=args.preco_maximo,
            max_paginas=args.max_paginas,
            revelar_telefone=not args.sem_telefone,
            fontes=args.fontes,
        )
    except ValueError as exc:
        print(f"Parâmetro inválido: {exc}", file=sys.stderr)
        return 2

    cfg = BrowserConfig(
        headless=not args.headful,
        proxy=args.proxy,
        delay_min=args.delay_min,
        delay_max=args.delay_max,
    )
    if args.chromium:
        cfg.executable_path = args.chromium
    from garimpador.pipeline import garimpar

    try:
        df = asyncio.run(garimpar(params, cfg, args.saida, args.concorrencia))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1
    if not df.empty:
        with __import__("pandas").option_context("display.max_colwidth", 60, "display.width", 200):
            print(df[["fonte", "area_m2", "valor_oferta", "endereco", "contato"]].head(20).to_string())
    return 0
