"""Reconhecimento de um site: salva HTML, JSONs interceptados e um diagnóstico.

Uso: python -m garimpador recon https://www.site.com.br/imoveis/venda/casa/fortaleza

Gera em ./recon/<host>/ o HTML renderizado, as respostas JSON que a página
buscou (úteis para descobrir APIs internas) e `diagnostico.txt` com os links
que parecem anúncios e os blocos JSON-LD encontrados. É o insumo para ajustar
`data/imobiliarias.yaml`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import Counter
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from garimpador.browser import BrowserConfig, BrowserPool
from garimpador.html_utils import extrair_jsonld, extrair_next_data, soup_de, telefones_de_links

log = logging.getLogger("garimpador.recon")


async def reconhecer(url: str, pasta: Path = Path("recon"), cfg: BrowserConfig | None = None) -> Path:
    cfg = cfg or BrowserConfig()
    host = urlsplit(url).netloc.replace(":", "_")
    destino = pasta / host
    destino.mkdir(parents=True, exist_ok=True)
    jsons: list[tuple[str, object]] = []

    async with BrowserPool(cfg) as pool, pool.contexto() as ctx:
        page = await ctx.new_page()

        async def ao_responder(resp):  # type: ignore[no-untyped-def]
            tipo = resp.headers.get("content-type", "")
            if "json" in tipo and resp.status == 200:
                try:
                    jsons.append((resp.url, await resp.json()))
                except Exception:  # noqa: BLE001
                    pass

        page.on("response", ao_responder)
        resp = await page.goto(url, wait_until="domcontentloaded")
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(1)
        html = await page.content()
        status = resp.status if resp else 0
        titulo = await page.title()

    (destino / "pagina.html").write_text(html, encoding="utf-8")
    for i, (u, dado) in enumerate(jsons):
        (destino / f"resposta_{i:02d}.json").write_text(
            json.dumps({"url": u, "dados": dado}, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    soup = soup_de(html)
    links = [urljoin(url, a["href"]) for a in soup.find_all("a", href=True)]
    mesmos = [l for l in links if urlsplit(l).netloc == urlsplit(url).netloc]
    padroes = Counter(re.sub(r"\d+", "{n}", urlsplit(l).path) for l in mesmos)
    com_numero = [l for l in mesmos if re.search(r"\d{3,}", l)]

    linhas = [
        f"URL: {url}",
        f"HTTP: {status} | título: {titulo}",
        f"HTML: {len(html)} bytes | links no mesmo host: {len(mesmos)}",
        f"__NEXT_DATA__: {'sim' if extrair_next_data(soup) else 'não'}",
        f"JSON-LD: {len(extrair_jsonld(soup))} blocos",
        f"Respostas JSON interceptadas: {len(jsons)}",
        *(f"   - {u}" for u, _ in jsons[:30]),
        f"Telefones em links: {telefones_de_links(soup)}",
        "",
        "Padrões de caminho mais frequentes (candidatos a padrao_link):",
        *(f"  {n:4d}  {p}" for p, n in padroes.most_common(25)),
        "",
        "Exemplos de links com número (prováveis anúncios):",
        *(f"  {l}" for l in list(dict.fromkeys(com_numero))[:25]),
    ]
    (destino / "diagnostico.txt").write_text("\n".join(linhas), encoding="utf-8")
    log.info("Recon salvo em %s", destino.resolve())
    print("\n".join(linhas))
    return destino
