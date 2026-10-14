import pytest

from garimpador.parsing import (
    br_para_float,
    canonical_url,
    dentro_da_faixa,
    extrair_telefones,
    montar_contato,
    montar_endereco,
    normalizar_telefone,
    parse_area,
    parse_preco,
    slugify,
)


@pytest.mark.parametrize("texto,esperado", [
    ("1.250.000,50", 1250000.5),
    ("180,5", 180.5),
    ("1.200", 1200.0),
    ("180.5", 180.5),
    ("1,250,000", 1250000.0),
    ("", None),
])
def test_br_para_float(texto, esperado):
    assert br_para_float(texto) == esperado


@pytest.mark.parametrize("texto,esperado", [
    ("R$ 1.250.000", 1250000.0),
    ("R$ 1.250.000,00", 1250000.0),
    ("R$1.090.000", 1090000.0),
    ("R$ 1,25 mi", 1250000.0),
    ("R$ 1,2 milhão", 1200000.0),
    ("2 milhões", 2000000.0),
    ("850 mil", 850000.0),
    ("R$ 900.000 Cond. R$ 800", 900000.0),
    ("Sob consulta", None),
    ("Consulte", None),
    (1500000, 1500000.0),
    ("1500000", 1500000.0),
    (None, None),
    (0, None),
])
def test_parse_preco(texto, esperado):
    assert parse_preco(texto) == esperado


@pytest.mark.parametrize("texto,esperado", [
    ("180 m²", 180.0),
    ("180m²", 180.0),
    ("180,5 m2", 180.5),
    ("1.200 m²", 1200.0),
    ("180 - 220 m²", 180.0),
    ("Área útil: 250 metros quadrados", 250.0),
    ("Casa 3 quartos 200 m²", 200.0),
    ("R$ 900.000 180 m²", 180.0),
    ("200", 200.0),
    ([210], 210.0),
    ([230, 210], 210.0),
    ("3 quartos", None),
    (None, None),
])
def test_parse_area(texto, esperado):
    assert parse_area(texto) == esperado


def test_dentro_da_faixa_limites_inclusivos():
    assert dentro_da_faixa(180, 180, 280)
    assert dentro_da_faixa(280, 180, 280)
    assert not dentro_da_faixa(179.9, 180, 280)
    assert not dentro_da_faixa(280.1, 180, 280)
    assert not dentro_da_faixa(None, 180, 280)
    assert dentro_da_faixa(None, None, None)
    assert dentro_da_faixa(500, 180, None)


@pytest.mark.parametrize("bruto,esperado", [
    ("85999998888", "(85) 99999-8888"),
    ("+55 (85) 3224-1234", "(85) 3224-1234"),
    ("5585999998888", "(85) 99999-8888"),
    ("085 99999 8888", "(85) 99999-8888"),
    ("12345", None),
])
def test_normalizar_telefone(bruto, esperado):
    assert normalizar_telefone(bruto) == esperado


def test_extrair_telefones_texto_livre():
    texto = "Ligue (85) 99999-8888 ou 85 3224.1234, whats +55 85 99999-8888"
    assert extrair_telefones(texto) == ["(85) 99999-8888", "(85) 3224-1234"]


def test_montar_contato_e_endereco():
    assert montar_contato("Imob X", ["85999998888", "(85) 99999-8888"]) == "Imob X | (85) 99999-8888"
    assert montar_contato("", []) == ""
    assert montar_endereco("Rua Silva Paulet", "1000", "Aldeota", "Fortaleza", "ce") == \
        "Rua Silva Paulet, 1000 - Aldeota, Fortaleza - CE"
    assert montar_endereco("", "", "Aldeota", "Fortaleza", "CE") == "Aldeota, Fortaleza - CE"


def test_slugify_e_canonical_url():
    assert slugify("São José do Rio Preto") == "sao-jose-do-rio-preto"
    assert canonical_url("https://WWW.Zap.com.br/imovel/abc-123/?utm_source=x#topo") == \
        "https://www.zap.com.br/imovel/abc-123"
    # parâmetros de identificação são preservados
    assert canonical_url("https://www.sj.com.br/SiteSys/site.aspx?codigo=55&utm_medium=y") == \
        "https://www.sj.com.br/SiteSys/site.aspx?codigo=55"
