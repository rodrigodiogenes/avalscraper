"""Garimpador de dados imobiliários.

Coleta ofertas de imóveis em portais (ZAP, VivaReal, OLX, Chaves na Mão) e em
sites de imobiliárias locais, normaliza os campos e exporta para CSV/JSON.
"""

from garimpador.config import SearchParams
from garimpador.models import Imovel

__all__ = ["SearchParams", "Imovel"]
__version__ = "0.1.0"
