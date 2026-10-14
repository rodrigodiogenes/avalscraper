from pathlib import Path

import pytest

from garimpador.browser import BrowserConfig
from garimpador.config import SearchParams

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def params() -> SearchParams:
    return SearchParams(tipo_imovel="Casa", transacao="Venda", cidade="Fortaleza", estado="CE",
                        bairro="Aldeota", area_minima_m2=180, area_maxima_m2=280)


@pytest.fixture
def cfg() -> BrowserConfig:
    return BrowserConfig(delay_min=0, delay_max=0)


def ler(nome: str) -> str:
    return (FIXTURES / nome).read_text(encoding="utf-8")
