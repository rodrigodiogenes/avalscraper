import json
from contextlib import asynccontextmanager

import pandas as pd
import pytest

from garimpador.cli import main
from garimpador.config import SearchParams
from garimpador.exporters import exportar, para_dataframe
from garimpador.models import CAMPOS_OBRIGATORIOS, Imovel
from garimpador.scrapers.base import BaseScraper, _mesclar_contato


class _FakeCtx:
    async def new_page(self):
        return object()


class _FakePool:
    @asynccontextmanager
    async def contexto(self):
        yield _FakeCtx()


class _FakeScraper(BaseScraper):
    nome = "Fake"
    PAGINAS = {
        1: [
            Imovel(link="https://x.com/1", area_m2=200, valor_oferta=1e6, bairro="Aldeota",
                   endereco="Aldeota, Fortaleza - CE", contato="Imob | (85) 99999-0000"),
            Imovel(link="https://x.com/2", area_m2=100, bairro="Aldeota"),       # fora da faixa
            Imovel(link="https://x.com/boom", bairro="Aldeota"),                 # erro no detalhe
        ],
        2: [Imovel(link="https://x.com/3", bairro="Aldeota")],                   # completa no detalhe
        3: [Imovel(link="https://x.com/3", bairro="Aldeota")],                   # repetida -> fim
    }

    def urls_busca(self, pagina):
        return []

    def parse_search_html(self, html, url):
        return []

    async def coletar_pagina(self, page, pagina):
        return [Imovel(**i.to_dict()) for i in self.PAGINAS.get(pagina, [])]

    async def enriquecer(self, page, imovel):
        if "boom" in imovel.link:
            raise TimeoutError("rede caiu")
        imovel.area_m2, imovel.valor_oferta = 250, 1.2e6
        imovel.endereco, imovel.contato = "Rua A - Aldeota, Fortaleza - CE", "Corretor"
        return imovel


@pytest.mark.asyncio
async def test_run_isola_erro_por_item_e_para_paginacao(params, cfg, caplog):
    params.max_paginas = 5
    params.revelar_telefone = False
    s = _FakeScraper(params, _FakePool(), cfg)
    with caplog.at_level("INFO"):
        itens = await s.run()
    assert [i.link for i in itens] == ["https://x.com/1", "https://x.com/3"]
    assert all(i.fonte == "Fake" for i in itens)
    assert s.descartes == {"area_fora_da_faixa": 1, "erro_no_item": 1}
    assert "Página 1/5 processada - 1 imóveis válidos encontrados" in caplog.text
    assert "Página 3/5 sem anúncios novos" in caplog.text


def test_dedup_mantem_versao_mais_completa(tmp_path):
    imoveis = [
        Imovel(link="https://a.com/imovel/1?utm_source=x", area_m2=200, valor_oferta=1e6, fonte="ZAP Imóveis"),
        Imovel(link="https://a.com/imovel/1/", area_m2=200, valor_oferta=1e6, fonte="ZAP Imóveis",
               endereco="Rua X", contato="Imob | (85) 99999-0000"),
        Imovel(link="https://a.com/imovel/2", area_m2=210, valor_oferta=9e5, fonte="VivaReal"),
    ]
    df = para_dataframe(imoveis)
    assert len(df) == 2
    linha = df[df["link"] == "https://a.com/imovel/1"].iloc[0]
    assert linha["contato"] == "Imob | (85) 99999-0000"
    assert list(df.columns[:6]) == list(CAMPOS_OBRIGATORIOS)

    csv_path, json_path = exportar(df, tmp_path)
    assert csv_path.name == "imoveis_garimpados.csv" and json_path.name == "imoveis_garimpados.json"
    lido = pd.read_csv(csv_path, encoding="utf-8-sig")
    assert len(lido) == 2 and lido["valor_oferta"].dtype.kind == "f"
    registros = json.loads(json_path.read_text(encoding="utf-8"))
    assert {"link", "endereco", "area_m2", "valor_oferta", "fonte", "contato"} <= set(registros[0])


def test_exportar_vazio_gera_cabecalho(tmp_path):
    csv_path, json_path = exportar(para_dataframe([]), tmp_path)
    assert csv_path.read_text(encoding="utf-8-sig").startswith("link,endereco,area_m2,valor_oferta,fonte,contato")
    assert json.loads(json_path.read_text(encoding="utf-8")) == []


def test_mesclar_contato():
    assert _mesclar_contato("Imob X", ["85999990000"]) == "Imob X | (85) 99999-0000"
    assert _mesclar_contato("Imob X | (85) 99999-0000", ["(85) 99999-0000", "8532240000"]) == \
        "Imob X | (85) 99999-0000 / (85) 3224-0000"
    assert _mesclar_contato("(85) 99999-0000", []) == "(85) 99999-0000"


@pytest.mark.parametrize("kwargs", [
    {"estado": "XX"},
    {"tipo_imovel": "Castelo"},
    {"transacao": "Permuta"},
    {"area_minima_m2": 300, "area_maxima_m2": 200},
    {"preco_minimo": 2e6, "preco_maximo": 1e6},
])
def test_search_params_valida(kwargs):
    with pytest.raises(ValueError):
        SearchParams(**kwargs)


def test_cli_listar_fontes(capsys):
    assert main(["listar-fontes"]) == 0
    saida = capsys.readouterr().out.split()
    assert {"zap", "vivareal", "olx", "chavesnamao", "magnomuniz", "sj"} <= set(saida)


def test_cli_parametro_invalido():
    assert main(["--estado", "ZZ"]) == 2
