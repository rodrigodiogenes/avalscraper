"""Integração com Chromium real, sem internet: os sites são simulados via page routing.

Exercita o caminho que os testes de parsing não cobrem: navegação, interceptação
do JSON da glue-api, paginação, visita ao detalhe e clique em "Ver telefone".
Pulado se não houver Chromium disponível.
"""

import os
import shutil
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from garimpador.browser import BrowserConfig, BrowserPool
from garimpador.scrapers.generico import SiteGenericoScraper, carregar_sites
from garimpador.scrapers.grupozap import ZapScraper
from tests.conftest import FIXTURES, ler


def _chromium() -> str | None:
    for c in (os.environ.get("GARIMPADOR_CHROMIUM"), "/opt/pw-browsers/chromium",
              shutil.which("chromium"), shutil.which("chromium-browser")):
        if c and Path(c).exists():
            return c
    return None


CHROMIUM = _chromium()
pytestmark = pytest.mark.skipif(CHROMIUM is None, reason="Chromium não disponível")

ZAP_BUSCA = """<html><head><title>Casas à venda</title></head><body><div id="r">carregando</div>
<script>fetch('https://glue-api.zapimoveis.com.br/v2/listings?page=1').then(r => r.json())
  .then(d => document.getElementById('r').textContent = d.search.totalCount);</script></body></html>"""

DETALHE_COM_BOTAO = """<html><body><h1>Casa em Aldeota</h1>
<p>Área útil: 230 m²</p><p>Valor de venda: R$ 1.380.000</p>
<div class="corretor"><span class="nome">João Corretor</span>
<button onclick="document.getElementById('tel').innerHTML='<a href=&quot;tel:+5585991112222&quot;>(85) 99111-2222</a>'">Ver telefone</button>
<div id="tel"></div></div></body></html>"""


class PoolSimulado(BrowserPool):
    """BrowserPool cujos contextos respondem com conteúdo local."""

    def __init__(self, cfg, paginas: dict[str, tuple[str, str]]):
        super().__init__(cfg)
        self.paginas = paginas
        self.visitas: list[str] = []

    @asynccontextmanager
    async def contexto(self):
        async with super().contexto() as ctx:
            async def responder(route):
                url = route.request.url
                self.visitas.append(url)
                for chave, (tipo, corpo) in self.paginas.items():
                    # chave terminada em "*" casa por prefixo; senão, URL exata
                    if url == chave or (chave.endswith("*") and url.startswith(chave[:-1])):
                        return await route.fulfill(status=200, content_type=tipo, body=corpo,
                                                   headers={"Access-Control-Allow-Origin": "*"})
                await route.fulfill(status=404, body="nao encontrado")
            await ctx.route("**/*", responder)
            yield ctx


@pytest.fixture
def cfg_browser():
    return BrowserConfig(executable_path=CHROMIUM, delay_min=0, delay_max=0, timeout_ms=15_000)


async def test_zap_intercepta_glue_api(params, cfg_browser):
    params.max_paginas = 2
    params.revelar_telefone = False
    paginas = {
        "https://www.zapimoveis.com.br/venda/casas/ce+fortaleza++aldeota/?areaMinima=180&areaMaxima=280":
            ("text/html; charset=utf-8", ZAP_BUSCA),
        "https://glue-api.zapimoveis.com.br/v2/listings*":
            ("application/json; charset=utf-8", (FIXTURES / "glue_api_listings.json").read_text(encoding="utf-8")),
    }
    async with PoolSimulado(cfg_browser, paginas) as pool:
        s = ZapScraper(params, pool, cfg_browser)
        s.max_tentativas = 1
        itens = await s.run()
    assert [i.id_externo for i in itens] == ["2712345001"]
    assert itens[0].contato.startswith("Imobiliária Exemplo | (85) 99999-8888")
    # página 2 tentou a URL com bairro e o fallback de cidade antes de encerrar
    assert any("pagina=2" in v and "++aldeota" in v for v in pool.visitas)
    assert any("pagina=2" in v and "ce+fortaleza/?" in v for v in pool.visitas)


async def test_generico_clica_ver_telefone(params, cfg_browser):
    params.max_paginas = 1
    site = next(s for s in carregar_sites() if s.chave == "magnomuniz")
    site.seletores_detalhe = {"anunciante": ".corretor .nome"}
    site.seletor_contato = ".corretor"
    busca = ler("imobiliaria_busca.html").replace("<footer>Fale conosco: (85) 3224-5566</footer>", "")
    paginas = {
        "https://www.magnomuniz.com.br/imoveis/venda/casa/fortaleza/aldeota*": ("text/html; charset=utf-8", busca),
        "https://www.magnomuniz.com.br/imovel/casa-aldeota-codigo-4521": ("text/html; charset=utf-8", DETALHE_COM_BOTAO),
    }
    async with PoolSimulado(cfg_browser, paginas) as pool:
        s = SiteGenericoScraper(site, params, pool, cfg_browser)
        s.max_tentativas = 1
        itens = await s.run()
    assert len(itens) == 1
    i = itens[0]
    assert (i.area_m2, i.valor_oferta) == (230.0, 1380000.0)
    assert i.contato == "João Corretor | (85) 99111-2222"
    assert i.fonte == "Magno Muniz Imóveis"
    # o card de 120 m² foi descartado sem visitar o detalhe
    assert not any("codigo-4522" in v for v in pool.visitas)
