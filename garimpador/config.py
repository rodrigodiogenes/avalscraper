"""Parâmetros de busca e mapeamentos de vocabulário por portal."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from garimpador.parsing import normalizar_comparacao, slugify

TIPOS_VALIDOS = ("casa", "apartamento", "terreno", "casa de condominio", "sala", "loja", "galpao")
TRANSACOES_VALIDAS = ("venda", "aluguel")

ESTADOS_NOME: dict[str, str] = {
    "AC": "Acre", "AL": "Alagoas", "AP": "Amapá", "AM": "Amazonas", "BA": "Bahia",
    "CE": "Ceará", "DF": "Distrito Federal", "ES": "Espírito Santo", "GO": "Goiás",
    "MA": "Maranhão", "MT": "Mato Grosso", "MS": "Mato Grosso do Sul", "MG": "Minas Gerais",
    "PA": "Pará", "PB": "Paraíba", "PR": "Paraná", "PE": "Pernambuco", "PI": "Piauí",
    "RJ": "Rio de Janeiro", "RN": "Rio Grande do Norte", "RS": "Rio Grande do Sul",
    "RO": "Rondônia", "RR": "Roraima", "SC": "Santa Catarina", "SP": "São Paulo",
    "SE": "Sergipe", "TO": "Tocantins",
}


@dataclass
class SearchParams:
    """Critérios de busca recebidos pelo garimpador."""

    tipo_imovel: str = "Casa"
    transacao: str = "Venda"
    cidade: str = "Fortaleza"
    estado: str = "CE"
    bairro: str = "Aldeota"
    area_minima_m2: Optional[float] = None
    area_maxima_m2: Optional[float] = None
    preco_minimo: Optional[float] = None
    preco_maximo: Optional[float] = None
    # Controle de execução
    max_paginas: int = 5
    revelar_telefone: bool = True
    fontes: list[str] = field(default_factory=list)  # vazio = todas

    def __post_init__(self) -> None:
        self.estado = self.estado.strip().upper()
        if self.estado not in ESTADOS_NOME:
            raise ValueError(f"UF inválida: {self.estado!r}")
        if normalizar_comparacao(self.tipo_imovel) not in TIPOS_VALIDOS:
            raise ValueError(f"tipo_imovel deve ser um de {TIPOS_VALIDOS}, recebido {self.tipo_imovel!r}")
        if normalizar_comparacao(self.transacao) not in TRANSACOES_VALIDAS:
            raise ValueError(f"transacao deve ser Venda ou Aluguel, recebido {self.transacao!r}")
        for nome_min, nome_max in (("area_minima_m2", "area_maxima_m2"), ("preco_minimo", "preco_maximo")):
            vmin, vmax = getattr(self, nome_min), getattr(self, nome_max)
            if vmin is not None and vmax is not None and vmin > vmax:
                raise ValueError(f"{nome_min} ({vmin}) maior que {nome_max} ({vmax})")
        if self.max_paginas < 1:
            raise ValueError("max_paginas deve ser >= 1")

    # Formas normalizadas usadas pelos adaptadores ------------------------- #

    @property
    def tipo_norm(self) -> str:
        return normalizar_comparacao(self.tipo_imovel)

    @property
    def transacao_norm(self) -> str:
        return normalizar_comparacao(self.transacao)

    @property
    def is_venda(self) -> bool:
        return self.transacao_norm == "venda"

    @property
    def cidade_slug(self) -> str:
        return slugify(self.cidade)

    @property
    def bairro_slug(self) -> str:
        return slugify(self.bairro) if self.bairro else ""

    @property
    def estado_slug(self) -> str:
        return self.estado.lower()

    @property
    def estado_nome(self) -> str:
        return ESTADOS_NOME[self.estado]

    def resumo(self) -> str:
        area = f"{self.area_minima_m2 or 0:g}-{self.area_maxima_m2 or '∞'} m²"
        preco = f"R$ {self.preco_minimo or 0:,.0f}-{self.preco_maximo or '∞'}"
        return (
            f"{self.tipo_imovel} para {self.transacao} | {self.bairro or '(todos os bairros)'}, "
            f"{self.cidade}-{self.estado} | {area} | {preco}"
        )
