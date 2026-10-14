import json

from garimpador.scrapers.generico import (
    ChavesNaMaoScraper,
    SiteGenericoScraper,
    carregar_sites,
    renderizar_url,
)
from garimpador.scrapers.grupozap import VivaRealScraper, ZapScraper
from garimpador.scrapers.olx import OLXScraper
from garimpador.models import Imovel
from tests.conftest import FIXTURES, ler

CAMPOS = ("link", "endereco", "area_m2", "valor_oferta", "fonte", "contato")


def _scraper(cls, params, cfg, *extra):
    return cls(*extra, params, None, cfg)


# --------------------------------------------------------------------------- #
# Grupo ZAP
# --------------------------------------------------------------------------- #

def test_zap_urls(params, cfg):
    urls = _scraper(ZapScraper, params, cfg).urls_busca(2)
    assert urls[0] == ("https://www.zapimoveis.com.br/venda/casas/ce+fortaleza++aldeota/"
                       "?areaMinima=180&areaMaxima=280&pagina=2")
    assert urls[1].startswith("https://www.zapimoveis.com.br/venda/casas/ce+fortaleza/?")


def test_vivareal_urls(params, cfg):
    urls = _scraper(VivaRealScraper, params, cfg).urls_busca(1)
    assert urls[0] == ("https://www.vivareal.com.br/venda/ceara/fortaleza/bairros/aldeota/"
                       "casa_residencial/?areaMinima=180&areaMaxima=280")


def test_grupozap_json_extrai_seis_campos(params, cfg):
    s = _scraper(ZapScraper, params, cfg)
    dado = json.loads((FIXTURES / "glue_api_listings.json").read_text(encoding="utf-8"))
    itens = s.imoveis_de_json(dado)
    assert len(itens) == 3
    i = itens[0]
    assert i.link == ("https://www.zapimoveis.com.br/imovel/"
                      "venda-casa-4-quartos-aldeota-fortaleza-ce-220m2-id-2712345001/")
    assert i.endereco == "Rua Silva Paulet, 1500 - Aldeota, Fortaleza - CE"
    assert i.area_m2 == 220.0
    assert i.valor_oferta == 1350000.0
    assert i.fonte == "ZAP Imóveis"
    assert i.contato == "Imobiliária Exemplo | (85) 99999-8888 / (85) 98888-7777"
    assert i.quartos == 4
    # preço de venda escolhido mesmo quando aluguel vem primeiro
    assert itens[2].valor_oferta == 1100000.0


def test_grupozap_filtros(params, cfg):
    s = _scraper(ZapScraper, params, cfg)
    dado = json.loads((FIXTURES / "glue_api_listings.json").read_text(encoding="utf-8"))
    itens = s.imoveis_de_json(dado)
    aprovados = [i for i in itens if s.passa_pre_filtro(i) and s.passa_filtro_final(i)]
    assert [i.id_externo for i in aprovados] == ["2712345001"]
    assert s.descartes == {"area_fora_da_faixa": 1, "outro_bairro": 1}


def test_zap_cards_html(params, cfg):
    s = _scraper(ZapScraper, params, cfg)
    itens = s.parse_search_html(ler("zap_busca_cards.html"), "https://www.zapimoveis.com.br/venda/")
    assert len(itens) == 2
    assert itens[0].area_m2 == 240.0
    assert itens[0].valor_oferta == 1450000.0
    assert itens[0].bairro == "Aldeota"
    assert itens[0].endereco == "Rua Barbosa de Freitas - Aldeota, Fortaleza - CE"
    assert itens[1].link == "https://www.zapimoveis.com.br/imovel/venda-casa-aldeota-id-222/"


# --------------------------------------------------------------------------- #
# OLX
# --------------------------------------------------------------------------- #

def test_olx_urls(params, cfg):
    params.preco_maximo = 2_000_000
    urls = _scraper(OLXScraper, params, cfg).urls_busca(3)
    assert urls[0] == ("https://www.olx.com.br/imoveis/venda/casas/estado-ce/fortaleza-e-regiao/"
                       "fortaleza/aldeota?pe=2000000&o=3")


def test_olx_busca_e_detalhe(params, cfg):
    s = _scraper(OLXScraper, params, cfg)
    itens = s.parse_search_html(ler("olx_busca.html"), "https://www.olx.com.br/")
    assert len(itens) == 2
    i = itens[0]
    assert (i.area_m2, i.valor_oferta, i.bairro, i.quartos) == (250.0, 1200000.0, "Aldeota", 4)
    assert s.passa_pre_filtro(i) and not s.passa_pre_filtro(itens[1])

    i = s.parse_detail_html(ler("olx_detalhe.html"), i)
    assert i.contato == "Maria Corretora | (85) 99876-5432"
    assert i.endereco == "Rua Tibúrcio Cavalcante - Aldeota, Fortaleza - CE"
    assert all(getattr(i, c) for c in CAMPOS)


# --------------------------------------------------------------------------- #
# Genérico / imobiliárias
# --------------------------------------------------------------------------- #

def test_yaml_tem_as_oito_imobiliarias():
    sites = carregar_sites()
    assert {s.chave for s in sites} == {
        "magnomuniz", "fiducial", "megaimoveis", "cesarrego", "torresdemeloneto",
        "ciropaiva", "alessandrobelchior", "sj",
    }
    for s in sites:
        assert s.urls_busca and s.padrao_link and s.base_url.startswith("https://")


def test_renderizar_url(params):
    assert renderizar_url("/imoveis/{transacao}/{tipo}/{cidade}/{bairro}?p={pagina}&a={area_min}",
                          params, 2) == "/imoveis/venda/casa/fortaleza/aldeota?p=2&a=180"


def test_generico_busca_e_detalhe(params, cfg):
    site = next(s for s in carregar_sites() if s.chave == "magnomuniz")
    s = SiteGenericoScraper(site, params, None, cfg)
    itens = s.parse_search_html(ler("imobiliaria_busca.html"), "https://www.magnomuniz.com.br/imoveis/venda")
    # link externo e link de menu sem código são ignorados
    assert [i.link for i in itens] == [
        "https://www.magnomuniz.com.br/imovel/casa-aldeota-codigo-4521",
        "https://www.magnomuniz.com.br/imovel/casa-aldeota-codigo-4522",
    ]
    assert (itens[0].area_m2, itens[0].valor_oferta) == (230.0, 1380000.0)
    assert not s.passa_pre_filtro(itens[1])

    novo = Imovel(link=itens[0].link)
    i = s.parse_detail_html(ler("imobiliaria_detalhe.html"), novo)
    assert i.endereco == "Rua Monsenhor Bruno, 800 - Aldeota, Fortaleza - CE"
    assert i.area_m2 == 230.0
    assert i.valor_oferta == 1380000.0  # não confunde com IPTU
    assert "(85) 99111-2222" in i.contato
    assert s.passa_filtro_final(i)


def test_generico_preco_ignora_iptu_sem_jsonld(params, cfg):
    site = next(s for s in carregar_sites() if s.chave == "magnomuniz")
    s = SiteGenericoScraper(site, params, None, cfg)
    html = "<html><body><h1>Casa Aldeota</h1><p>IPTU R$ 3.200</p><p>R$ 1.100.000</p><p>Área construída: 200 m²</p></body></html>"
    i = s.parse_detail_html(html, Imovel(link="https://www.magnomuniz.com.br/imovel/x-123"))
    assert (i.valor_oferta, i.area_m2, i.bairro) == (1100000.0, 200.0, "Aldeota")
    assert i.contato == "Magno Muniz Imóveis"


def test_chavesnamao_urls(params, cfg):
    urls = _scraper(ChavesNaMaoScraper, params, cfg).urls_busca(1)
    assert urls[0] == "https://www.chavesnamao.com.br/casas-a-venda/ce-fortaleza/aldeota/?pg=1"
    params.bairro = ""
    urls = _scraper(ChavesNaMaoScraper, params, cfg).urls_busca(1)
    assert urls == ["https://www.chavesnamao.com.br/casas-a-venda/ce-fortaleza/?pg=1"]
