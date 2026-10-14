"""Modelo de dados de um anúncio normalizado."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

# Ordem das colunas obrigatórias na exportação.
CAMPOS_OBRIGATORIOS: tuple[str, ...] = (
    "link",
    "endereco",
    "area_m2",
    "valor_oferta",
    "fonte",
    "contato",
)

CAMPOS_EXTRAS: tuple[str, ...] = (
    "titulo",
    "tipo_imovel",
    "bairro",
    "cidade",
    "estado",
    "quartos",
    "id_externo",
    "coletado_em",
)


def _agora_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class Imovel:
    """Um anúncio já normalizado.

    Os seis primeiros campos são os obrigatórios do contrato de saída; os demais
    ajudam na análise e na deduplicação.
    """

    link: str
    endereco: str = ""
    area_m2: Optional[float] = None
    valor_oferta: Optional[float] = None
    fonte: str = ""
    contato: str = ""

    titulo: str = ""
    tipo_imovel: str = ""
    bairro: str = ""
    cidade: str = ""
    estado: str = ""
    quartos: Optional[int] = None
    id_externo: str = ""
    coletado_em: str = field(default_factory=_agora_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
