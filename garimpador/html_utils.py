"""Extratores genéricos de HTML reaproveitados por vários adaptadores.

Sites de imobiliária variam muito de layout, então além de seletores
específicos usamos três camadas de fallback que costumam existir em qualquer
página de anúncio: JSON-LD (schema.org), JSON de hidratação (Next.js/Nuxt) e
heurística textual por rótulo ("Área útil: 200 m²", "Valor: R$ ...").
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable, Iterator, Optional
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup, Tag

from garimpador.parsing import (
    extrair_telefones,
    limpar_texto,
    normalizar_telefone,
    parse_area,
    parse_preco,
)


def soup_de(html: str) -> BeautifulSoup:
    return BeautifulSoup(html or "", "lxml")


# --------------------------------------------------------------------------- #
# JSON embutido
# --------------------------------------------------------------------------- #


def extrair_jsonld(soup: BeautifulSoup) -> list[dict[str, Any]]:
    """Todos os objetos JSON-LD da página, achatando @graph e listas."""
    saida: list[dict[str, Any]] = []
    for tag in soup.find_all("script", type="application/ld+json"):
        bruto = tag.string or tag.get_text() or ""
        try:
            dado = json.loads(bruto.strip())
        except (json.JSONDecodeError, ValueError):
            continue
        pilha = dado if isinstance(dado, list) else [dado]
        while pilha:
            item = pilha.pop(0)
            if isinstance(item, dict):
                if "@graph" in item and isinstance(item["@graph"], list):
                    pilha.extend(item["@graph"])
                saida.append(item)
            elif isinstance(item, list):
                pilha.extend(item)
    return saida


def extrair_next_data(soup: BeautifulSoup) -> Optional[dict[str, Any]]:
    tag = soup.find("script", id="__NEXT_DATA__")
    if not tag:
        return None
    try:
        return json.loads(tag.string or tag.get_text())
    except (json.JSONDecodeError, ValueError):
        return None


def iterar_dicts(obj: Any) -> Iterator[dict[str, Any]]:
    """Percorre qualquer estrutura JSON entregando cada dict, em ordem de documento."""
    pilha = [obj]
    while pilha:
        atual = pilha.pop()
        if isinstance(atual, dict):
            yield atual
            pilha.extend(reversed(list(atual.values())))
        elif isinstance(atual, list):
            pilha.extend(reversed(atual))


# --------------------------------------------------------------------------- #
# Contato
# --------------------------------------------------------------------------- #


def telefones_de_links(soup: BeautifulSoup) -> list[str]:
    """Telefones em href="tel:..." e links de WhatsApp (wa.me / api.whatsapp.com)."""
    tels: list[str] = []
    for a in soup.find_all("a", href=True):
        href: str = a["href"]
        candidato = None
        if href.lower().startswith("tel:"):
            candidato = href[4:]
        elif "wa.me/" in href:
            candidato = urlsplit(href).path.strip("/").split("/")[-1]
        elif "whatsapp.com" in href:
            candidato = (parse_qs(urlsplit(href).query).get("phone") or [""])[0]
        tel = normalizar_telefone(candidato or "")
        if tel and tel not in tels:
            tels.append(tel)
    return tels


def telefones_da_pagina(soup: BeautifulSoup, seletor_area: Optional[str] = None) -> list[str]:
    """Links tel:/WhatsApp primeiro; depois regex no texto da área de contato."""
    tels = telefones_de_links(soup)
    escopo: Iterable[Tag] = soup.select(seletor_area) if seletor_area else []
    for bloco in escopo:
        for t in extrair_telefones(bloco.get_text(" ")):
            if t not in tels:
                tels.append(t)
    return tels


# --------------------------------------------------------------------------- #
# Heurística textual por rótulo
# --------------------------------------------------------------------------- #

_RE_AREA_ROTULADA = re.compile(
    r"(?:[áa]rea\s*(?:[úu]til|constru[íi]da|privativa|edificada|total)?|"
    r"constru[íi]da|privativa)\s*[:\-]?\s*(\d[\d.,]*\s*(?:m²|m2|m\b))",
    re.IGNORECASE,
)
_RE_PRECO_ROTULADO = re.compile(
    r"(?:valor\s*(?:de\s*)?(?:venda|loca[çc][ãa]o|aluguel)?|pre[çc]o|venda|aluguel)"
    r"\s*[:\-]?\s*(r\$\s*\d[\d.,]*(?:\s*(?:mil|mi|milh[õo]es|milh[ãa]o))?)",
    re.IGNORECASE,
)
_RE_QUALQUER_PRECO = re.compile(r"r\$\s*\d[\d.,]*(?:\s*(?:mil|mi\b|milh[õo]es|milh[ãa]o))?", re.IGNORECASE)
_RE_QUALQUER_AREA = re.compile(r"\d[\d.,]*\s*(?:m²|m2)", re.IGNORECASE)
_RE_CONDOMINIO_IPTU = re.compile(r"(condom[íi]nio|iptu|taxa)\s*[:\-]?\s*$", re.IGNORECASE)


def area_por_rotulo(texto: str) -> Optional[float]:
    """Prefere área útil/construída/privativa; total de terreno só em último caso."""
    candidatos: list[tuple[int, float]] = []
    for m in _RE_AREA_ROTULADA.finditer(texto):
        area = parse_area(m.group(1))
        if not area:
            continue
        rotulo = m.group(0).lower()
        prioridade = 0 if re.search(r"[úu]til|constru|privativa|edificada", rotulo) else 1
        candidatos.append((prioridade, area))
    if candidatos:
        return sorted(candidatos, key=lambda c: c[0])[0][1]
    m = _RE_QUALQUER_AREA.search(texto)
    return parse_area(m.group(0)) if m else None


def preco_por_rotulo(texto: str, minimo_plausivel: float = 0.0) -> Optional[float]:
    """Preço principal ignorando condomínio/IPTU.

    `minimo_plausivel` evita confundir taxa com preço (ex.: em venda, valores
    abaixo de R$ 10 mil quase sempre são condomínio ou IPTU).
    """
    for m in _RE_PRECO_ROTULADO.finditer(texto):
        valor = parse_preco(m.group(1))
        if valor and valor >= minimo_plausivel:
            return valor
    for m in _RE_QUALQUER_PRECO.finditer(texto):
        anterior = texto[max(0, m.start() - 25):m.start()]
        if _RE_CONDOMINIO_IPTU.search(anterior):
            continue
        valor = parse_preco(m.group(0))
        if valor and valor >= minimo_plausivel:
            return valor
    return None


def texto_visivel(soup: BeautifulSoup | Tag) -> str:
    for t in soup.find_all(["script", "style", "noscript", "svg"]):
        t.decompose()
    return limpar_texto(soup.get_text(" "))


# --------------------------------------------------------------------------- #
# Cards heurísticos (listas de resultados sem seletor conhecido)
# --------------------------------------------------------------------------- #


def cards_por_links(
    soup: BeautifulSoup,
    base_url: str,
    padrao_link: str,
    max_subida: int = 6,
) -> list[tuple[str, str]]:
    """Encontra cards de anúncio a partir dos links que casam `padrao_link`.

    Para cada link, sobe na árvore até achar um contêiner com "R$" ou "m²" e
    devolve (url_absoluta, texto_do_card). Funciona em layouts desconhecidos
    porque quase todo card tem link do anúncio + preço + área no mesmo bloco.
    """
    regex = re.compile(padrao_link, re.IGNORECASE)
    vistos: dict[str, str] = {}
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not regex.search(href):
            continue
        url = urljoin(base_url, href)
        if url in vistos and vistos[url]:
            continue
        no: Optional[Tag] = a
        texto = ""
        for _ in range(max_subida):
            if no is None:
                break
            texto = limpar_texto(no.get_text(" "))
            if "R$" in texto or re.search(r"m²|m2", texto, re.IGNORECASE):
                break
            no = no.parent if isinstance(no.parent, Tag) else None
        vistos[url] = texto if len(texto) < 1500 else texto[:1500]
    return list(vistos.items())
