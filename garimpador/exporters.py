"""Consolidação em DataFrame, deduplicação e exportação CSV/JSON."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable

import pandas as pd

from garimpador.models import CAMPOS_EXTRAS, CAMPOS_OBRIGATORIOS, Imovel
from garimpador.parsing import canonical_url

log = logging.getLogger("garimpador.export")

NOME_BASE = "imoveis_garimpados"


def para_dataframe(imoveis: Iterable[Imovel]) -> pd.DataFrame:
    """DataFrame com colunas obrigatórias primeiro e duplicatas removidas por `link`.

    Quando o mesmo anúncio aparece duas vezes, mantém a versão mais completa
    (mais campos preenchidos, com preferência para quem tem telefone).
    """
    colunas = [*CAMPOS_OBRIGATORIOS, *CAMPOS_EXTRAS]
    df = pd.DataFrame([i.to_dict() for i in imoveis], columns=colunas)
    if df.empty:
        return df
    df["link"] = df["link"].map(canonical_url)
    df["_completude"] = df[list(CAMPOS_OBRIGATORIOS)].replace("", pd.NA).notna().sum(axis=1)
    df["_completude"] += df["contato"].fillna("").str.contains(r"\(\d{2}\)", regex=True).astype(int)
    antes = len(df)
    df = (
        df.sort_values("_completude", ascending=False)
        .drop_duplicates(subset="link", keep="first")
        .drop(columns="_completude")
        .sort_values(["fonte", "valor_oferta"], na_position="last")
        .reset_index(drop=True)
    )
    if antes != len(df):
        log.info("Duplicatas removidas: %d", antes - len(df))
    df["area_m2"] = pd.to_numeric(df["area_m2"], errors="coerce")
    df["valor_oferta"] = pd.to_numeric(df["valor_oferta"], errors="coerce")
    df["quartos"] = pd.to_numeric(df["quartos"], errors="coerce").astype("Int64")
    return df


def exportar(df: pd.DataFrame, pasta: Path | str = ".", nome_base: str = NOME_BASE) -> tuple[Path, Path]:
    """Grava `<nome_base>.csv` (UTF-8 com BOM, abre certo no Excel) e `<nome_base>.json`."""
    pasta = Path(pasta)
    pasta.mkdir(parents=True, exist_ok=True)
    csv_path = pasta / f"{nome_base}.csv"
    json_path = pasta / f"{nome_base}.json"
    if df.empty:
        df = pd.DataFrame(columns=[*CAMPOS_OBRIGATORIOS, *CAMPOS_EXTRAS])
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    df.to_json(json_path, orient="records", force_ascii=False, indent=2)
    log.info("Exportados %d imóveis para %s e %s", len(df), csv_path, json_path)
    return csv_path, json_path
