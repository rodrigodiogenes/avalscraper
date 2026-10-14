"""OLX Brasil.

A busca é uma página Next.js: os anúncios vêm completos em `__NEXT_DATA__`
(props.pageProps.ads), o que dispensa parsear o HTML dos cards. A página do
anúncio expõe o JSON em `script#initial-data[data-json]`.

O telefone na OLX só aparece para usuário logado; não automatizamos login, então
o contato traz o nome do anunciante e, quando o anúncio publica, o telefone que
estiver na descrição.
"""

from __future__ import annotations

import json
from typing import Any, Optional
from urllib.parse import urlencode, urljoin

from garimpador.html_utils import (
    area_por_rotulo,
    extrair_next_data,
    iterar_dicts,
    preco_por_rotulo,
    soup_de,
    telefones_de_links,
    texto_visivel,
)
from garimpador.models import Imovel
from garimpador.parsing import (
    extrair_telefones,
    limpar_texto,
    montar_contato,
    montar_endereco,
    parse_area,
    parse_preco,
)
from garimpador.scrapers.base import BaseScraper

# Região OLX das capitais; demais cidades seguem o padrão "<capital>-e-regiao"
# do estado e precisam ser ajustadas aqui se a busca vier vazia.
REGIAO_POR_UF = {
    "CE": "fortaleza-e-regiao",
    "SP": "sao-paulo-e-regiao",
    "RJ": "rio-de-janeiro-e-regiao",
    "MG": "belo-horizonte-e-regiao",
    "PE": "grande-recife",
    "BA": "grande-salvador",
    "DF": "distrito-federal-e-regiao",
    "PR": "regiao-de-curitiba-e-paranagua",
    "RS": "regioes-de-porto-alegre-torres-e-santa-cruz-do-sul",
    "RN": "natal",
    "PB": "paraiba",
}


class OLXScraper(BaseScraper):
    nome = "OLX"
    base_url = "https://www.olx.com.br/"
    suporta_revelar_telefone = False  # exige login

    TIPOS = {
        "casa": "casas",
        "apartamento": "apartamentos",
        "terreno": "terrenos",
        "casa de condominio": "casas",
        "sala": "comercio-e-industria",
        "loja": "comercio-e-industria",
        "galpao": "comercio-e-industria",
    }

    def urls_busca(self, pagina: int) -> list[str]:
        p = self.params
        trans = "venda" if p.is_venda else "aluguel"
        regiao = REGIAO_POR_UF.get(p.estado, f"{p.cidade_slug}-e-regiao")
        base = f"/imoveis/{trans}/{self.TIPOS[p.tipo_norm]}/estado-{p.estado_slug}/{regiao}/{p.cidade_slug}"
        q: dict[str, Any] = {}
        # Preço vai na URL (ps/pe em reais). Área é filtrada localmente porque os
        # parâmetros de área da OLX são faixas codificadas, não m².
        if p.preco_minimo:
            q["ps"] = int(p.preco_minimo)
        if p.preco_maximo:
            q["pe"] = int(p.preco_maximo)
        if pagina > 1:
            q["o"] = pagina
        sufixo = f"?{urlencode(q)}" if q else ""
        urls = []
        if p.bairro_slug:
            urls.append(urljoin(self.base_url, f"{base}/{p.bairro_slug}{sufixo}"))
        urls.append(urljoin(self.base_url, f"{base}{sufixo}"))
        return urls

    def parse_search_html(self, html: str, url: str) -> list[Imovel]:
        soup = soup_de(html)
        dados = extrair_next_data(soup)
        anuncios: list[dict[str, Any]] = []
        if dados:
            ads = (((dados.get("props") or {}).get("pageProps") or {}).get("ads")) or []
            anuncios = [a for a in ads if isinstance(a, dict) and a.get("url")]
            if not anuncios:
                anuncios = [d for d in iterar_dicts(dados) if d.get("listId") and d.get("url")]
        return [self._imovel_de_ad(a) for a in anuncios]

    def _imovel_de_ad(self, ad: dict[str, Any]) -> Imovel:
        loc = ad.get("locationDetails") or {}
        props = {str(p.get("name", "")).lower(): p.get("value") for p in ad.get("properties") or []
                 if isinstance(p, dict)}
        area = parse_area(props.get("size")) or parse_area(props.get("real_estate_size"))
        bairro = loc.get("neighbourhood") or ""
        if not bairro and ad.get("location"):
            # "Fortaleza, Aldeota" ou "Aldeota, Fortaleza - CE"
            partes = [limpar_texto(x) for x in str(ad["location"]).split(",")]
            bairro = partes[-1] if len(partes) > 1 else ""
        quartos = props.get("rooms")
        return Imovel(
            link=ad["url"],
            endereco=montar_endereco("", "", bairro, loc.get("municipality", ""), loc.get("uf", "")),
            area_m2=area,
            valor_oferta=parse_preco(ad.get("price") or ad.get("priceValue")),
            fonte=self.nome,
            contato="",
            titulo=limpar_texto(ad.get("subject") or ad.get("title") or ""),
            bairro=bairro,
            cidade=loc.get("municipality", ""),
            estado=loc.get("uf", ""),
            quartos=_int(quartos),
            id_externo=str(ad.get("listId", "")),
        )

    def parse_detail_html(self, html: str, imovel: Imovel) -> Imovel:
        soup = soup_de(html)
        dado = _initial_data(soup) or extrair_next_data(soup) or {}
        anuncio: dict[str, Any] = {}
        for d in iterar_dicts(dado):
            if "listId" in d and ("user" in d or "location" in d):
                anuncio = d
                break

        nome = ""
        if anuncio:
            usuario = anuncio.get("user") or {}
            nome = usuario.get("name") or anuncio.get("sellerName") or ""
            loc = anuncio.get("location") or {}
            if isinstance(loc, dict):
                imovel.endereco = montar_endereco(
                    loc.get("address", ""), "", loc.get("neighbourhood", imovel.bairro),
                    loc.get("municipality", imovel.cidade), loc.get("uf", imovel.estado),
                ) or imovel.endereco
                imovel.bairro = loc.get("neighbourhood") or imovel.bairro
            for p in anuncio.get("properties") or []:
                if isinstance(p, dict) and str(p.get("name", "")).lower() in ("size", "real_estate_size"):
                    imovel.area_m2 = imovel.area_m2 or parse_area(p.get("value"))
            imovel.valor_oferta = imovel.valor_oferta or parse_preco(anuncio.get("priceValue") or anuncio.get("price"))
            descricao = str(anuncio.get("body") or anuncio.get("description") or "")
        else:
            descricao = ""

        texto = texto_visivel(soup_de(html))
        if imovel.area_m2 is None:
            imovel.area_m2 = area_por_rotulo(texto)
        if imovel.valor_oferta is None:
            imovel.valor_oferta = preco_por_rotulo(texto, self.preco_minimo_plausivel)

        telefones = telefones_de_links(soup) + extrair_telefones(descricao)
        imovel.contato = montar_contato(nome, telefones) or imovel.contato
        return imovel


def _initial_data(soup: Any) -> Optional[dict[str, Any]]:
    tag = soup.find("script", id="initial-data")
    if not tag or not tag.get("data-json"):
        return None
    try:
        return json.loads(tag["data-json"])
    except (json.JSONDecodeError, ValueError):
        return None


def _int(valor: Any) -> Optional[int]:
    try:
        return int(str(valor).strip().rstrip("+"))
    except (TypeError, ValueError):
        return None
