"""Adaptador genérico dirigido por configuração (imobiliárias locais e Chaves na Mão).

Cada site é descrito por um bloco no `data/imobiliarias.yaml`:

    - nome: Magno Muniz
      base_url: https://www.magnomuniz.com.br/
      urls_busca:                      # tentadas em ordem; placeholders abaixo
        - /imoveis/{transacao}/{tipo}/{cidade}/{bairro}?pagina={pagina}
      padrao_link: /imovel/            # regex que identifica link de anúncio
      seletor_card: null               # opcional; sem ele usamos heurística
      seletores_detalhe:               # opcionais
        area: ".area-util"
        preco: ".valor"
        endereco: ".endereco"
        anunciante: ".corretor-nome"
      seletor_contato: ".contato"      # área onde procurar telefones
      contato_padrao: "Magno Muniz Imóveis | (85) 0000-0000"
      paginacao: true
      validated: false

Placeholders disponíveis: {pagina} {transacao} {tipo} {tipo_plural} {cidade}
{bairro} {uf} {area_min} {area_max} {preco_min} {preco_max}.

Sem seletores, a extração cai nas camadas genéricas: JSON-LD, links tel:/wa.me
e rótulos textuais ("Área útil 200 m²", "Valor R$ 1.200.000").
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urljoin, urlsplit

import yaml

from garimpador.config import SearchParams
from garimpador.html_utils import (
    area_por_rotulo,
    cards_por_links,
    extrair_jsonld,
    preco_por_rotulo,
    soup_de,
    telefones_da_pagina,
    texto_visivel,
)
from garimpador.models import Imovel
from garimpador.parsing import (
    limpar_texto,
    montar_contato,
    montar_endereco,
    parse_area,
    parse_preco,
    slugify,
)
from garimpador.scrapers.base import BaseScraper, _mesclar_contato

ARQUIVO_PADRAO = Path(__file__).resolve().parent.parent / "data" / "imobiliarias.yaml"

TIPO_PLURAL = {
    "casa": "casas",
    "apartamento": "apartamentos",
    "terreno": "terrenos",
    "casa de condominio": "casas-de-condominio",
    "sala": "salas",
    "loja": "lojas",
    "galpao": "galpoes",
}


@dataclass
class SiteConfig:
    nome: str
    base_url: str
    urls_busca: list[str]
    padrao_link: str
    seletor_card: Optional[str] = None
    seletores_detalhe: dict[str, str] = field(default_factory=dict)
    seletor_contato: Optional[str] = None
    contato_padrao: str = ""
    paginacao: bool = True
    validated: bool = False
    chave: str = ""

    @classmethod
    def de_dict(cls, d: dict[str, Any]) -> "SiteConfig":
        campos = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        cfg = cls(**campos)
        cfg.chave = cfg.chave or slugify(cfg.nome)
        cfg.seletores_detalhe = cfg.seletores_detalhe or {}
        return cfg


def carregar_sites(caminho: Path = ARQUIVO_PADRAO) -> list[SiteConfig]:
    with open(caminho, encoding="utf-8") as fh:
        bruto = yaml.safe_load(fh) or []
    return [SiteConfig.de_dict(d) for d in bruto]


def renderizar_url(modelo: str, params: SearchParams, pagina: int) -> str:
    """Preenche os placeholders do modelo de URL com os parâmetros de busca."""
    valores = {
        "pagina": pagina,
        "transacao": "venda" if params.is_venda else "aluguel",
        "tipo": slugify(params.tipo_imovel),
        "tipo_plural": TIPO_PLURAL.get(params.tipo_norm, slugify(params.tipo_imovel)),
        "cidade": params.cidade_slug,
        "bairro": params.bairro_slug,
        "uf": params.estado_slug,
        "area_min": int(params.area_minima_m2 or 0),
        "area_max": int(params.area_maxima_m2 or 0) or "",
        "preco_min": int(params.preco_minimo or 0),
        "preco_max": int(params.preco_maximo or 0) or "",
    }
    return modelo.format(**valores)


class SiteGenericoScraper(BaseScraper):
    """Scraper de um site descrito por `SiteConfig`."""

    def __init__(self, site: SiteConfig, *args: Any, **kwargs: Any) -> None:
        self.site = site
        self.nome = site.nome
        self.seletor_contato = site.seletor_contato
        super().__init__(*args, **kwargs)
        if not site.validated:
            self.log.warning(
                "Seletores/URLs de %s ainda não validados em execução real; "
                "rode `python -m garimpador recon` e ajuste data/imobiliarias.yaml.",
                site.nome,
            )

    def urls_busca(self, pagina: int) -> list[str]:
        if pagina > 1 and not self.site.paginacao:
            return []
        return [urljoin(self.site.base_url, renderizar_url(m, self.params, pagina))
                for m in self.site.urls_busca]

    # --- Resultados --------------------------------------------------- #

    def parse_search_html(self, html: str, url: str) -> list[Imovel]:
        soup = soup_de(html)
        mesmo_host = urlsplit(self.site.base_url).netloc.replace("www.", "")
        pares: list[tuple[str, str]] = []
        if self.site.seletor_card:
            for card in soup.select(self.site.seletor_card):
                a = card.find("a", href=re.compile(self.site.padrao_link, re.I)) or card.find("a", href=True)
                if a:
                    pares.append((urljoin(url, a["href"]), limpar_texto(card.get_text(" "))))
        if not pares:
            pares = cards_por_links(soup, url, self.site.padrao_link)

        saida: list[Imovel] = []
        for link, texto in pares:
            if mesmo_host not in urlsplit(link).netloc:
                continue
            saida.append(Imovel(
                link=link,
                area_m2=area_por_rotulo(texto) if texto else None,
                valor_oferta=preco_por_rotulo(texto, self.preco_minimo_plausivel) if texto else None,
                fonte=self.nome,
                titulo=texto[:160],
            ))
        return saida

    # --- Detalhe ------------------------------------------------------ #

    def parse_detail_html(self, html: str, imovel: Imovel) -> Imovel:
        soup = soup_de(html)
        sel = self.site.seletores_detalhe

        def por_seletor(chave: str) -> str:
            css = sel.get(chave)
            el = soup.select_one(css) if css else None
            return limpar_texto(el.get_text(" ")) if el else ""

        # 1) JSON-LD (schema.org: Offer, Residence, Product, RealEstateListing...)
        for obj in extrair_jsonld(soup):
            _aplicar_jsonld(obj, imovel)

        # 2) Seletores configurados
        if sel.get("area"):
            imovel.area_m2 = parse_area(por_seletor("area")) or imovel.area_m2
        if sel.get("preco"):
            imovel.valor_oferta = parse_preco(por_seletor("preco")) or imovel.valor_oferta
        if sel.get("endereco"):
            imovel.endereco = por_seletor("endereco") or imovel.endereco
        nome = por_seletor("anunciante") if sel.get("anunciante") else ""

        h1 = soup.find("h1")
        if h1 and not imovel.titulo:
            imovel.titulo = limpar_texto(h1.get_text(" "))[:160]

        tels = telefones_da_pagina(soup, self.seletor_contato)

        # 3) Heurística textual
        texto = texto_visivel(soup_de(html))
        if imovel.area_m2 is None:
            imovel.area_m2 = area_por_rotulo(texto)
        if imovel.valor_oferta is None:
            imovel.valor_oferta = preco_por_rotulo(texto, self.preco_minimo_plausivel)
        if not imovel.bairro and self.params.bairro:
            alvo = self.params.bairro
            if re.search(rf"\b{re.escape(alvo)}\b", f"{imovel.titulo} {texto[:4000]}", re.IGNORECASE):
                imovel.bairro = alvo
        if not imovel.endereco:
            imovel.endereco = montar_endereco(
                "", "", imovel.bairro, self.params.cidade, self.params.estado
            )

        base_contato = nome or imovel.contato or self.site.contato_padrao or self.site.nome
        imovel.contato = _mesclar_contato(base_contato, tels)
        return imovel


def _aplicar_jsonld(obj: dict[str, Any], imovel: Imovel) -> None:
    tipo = obj.get("@type")
    tipos = {t.lower() for t in (tipo if isinstance(tipo, list) else [tipo]) if isinstance(t, str)}

    oferta = obj.get("offers")
    if isinstance(oferta, list):
        oferta = oferta[0] if oferta else None
    if isinstance(oferta, dict) and imovel.valor_oferta is None:
        imovel.valor_oferta = parse_preco(oferta.get("price"))
    if "offer" in tipos and imovel.valor_oferta is None:
        imovel.valor_oferta = parse_preco(obj.get("price"))

    area = obj.get("floorSize")
    if isinstance(area, dict):
        area = area.get("value")
    if area and imovel.area_m2 is None:
        imovel.area_m2 = parse_area(area)

    end = obj.get("address")
    if isinstance(end, dict) and not imovel.endereco:
        bairro = end.get("addressNeighborhood") or ""
        imovel.endereco = montar_endereco(
            end.get("streetAddress", ""), "", bairro,
            end.get("addressLocality", ""), end.get("addressRegion", ""),
        )
        imovel.bairro = imovel.bairro or bairro
        imovel.cidade = imovel.cidade or end.get("addressLocality", "")

    if tipos & {"realestateagent", "organization", "person"} and obj.get("name"):
        tel = obj.get("telephone") or ""
        if not imovel.contato:
            imovel.contato = montar_contato(obj["name"], [tel] if tel else [])


class ChavesNaMaoScraper(SiteGenericoScraper):
    """Chaves na Mão com URLs fixas do portal e extração genérica."""

    SITE = SiteConfig(
        nome="Chaves na Mão",
        base_url="https://www.chavesnamao.com.br/",
        urls_busca=[
            "/{tipo_plural}-a-{transacao}/{uf}-{cidade}/{bairro}/?pg={pagina}",
            "/{tipo_plural}-a-{transacao}/{uf}-{cidade}/?pg={pagina}",
        ],
        padrao_link=r"/imovel/",
        seletor_contato='[class*="contact"], [class*="anunciante"], [class*="advertiser"]',
        validated=False,
        chave="chavesnamao",
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(self.SITE, *args, **kwargs)

    def urls_busca(self, pagina: int) -> list[str]:
        urls = super().urls_busca(pagina)
        if not self.params.bairro_slug:
            urls = [u for u in urls if "//" not in urlsplit(u).path]
        return urls
