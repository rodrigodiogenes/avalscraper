"""ZAP Imóveis e VivaReal (mesmo grupo, mesma API interna "glue-api").

Estratégia em três camadas, da mais estável para a mais frágil:
1. Interceptar o JSON que a própria página busca na glue-api.
2. Ler o JSON de hidratação (__NEXT_DATA__) se a página vier renderizada no servidor.
3. Ler os cards do HTML pelos atributos `data-cy` (estáveis desde o redesign de 2024).
"""

from __future__ import annotations

import asyncio
import re
from typing import Any
from urllib.parse import urlencode, urljoin

from garimpador.html_utils import (
    area_por_rotulo,
    extrair_next_data,
    iterar_dicts,
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
)
from garimpador.scrapers.base import BaseScraper, _mesclar_contato

RE_GLUE_API = re.compile(r"glue-api\.[a-z.]+/v\d+/listings", re.IGNORECASE)

SELETORES_ANUNCIANTE = (
    '[data-testid="official-store-name"]',
    '[data-cy="ldp-advertiser-name"]',
    '[data-testid="advertiser-info-name"]',
    ".advertiser-info__name",
    '[class*="advertiser"] [class*="name"]',
    '[class*="publisher"] [class*="name"]',
)
SELETORES_ENDERECO_DETALHE = (
    '[data-testid="address-info-value"]',
    '[data-cy="ldp-propertyAddress-txt"]',
    ".address-info-value",
    '[class*="address"]',
)


class GrupoZapScraper(BaseScraper):
    """Base comum; subclasses definem domínio e montagem de URL."""

    base_url: str = ""
    seletor_contato = '[class*="advertiser"], [class*="contact"], [data-testid*="contact"]'

    # --- URLs --------------------------------------------------------- #

    def filtros_query(self, pagina: int) -> dict[str, Any]:
        p = self.params
        q: dict[str, Any] = {}
        if p.area_minima_m2:
            q["areaMinima"] = int(p.area_minima_m2)
        if p.area_maxima_m2:
            q["areaMaxima"] = int(p.area_maxima_m2)
        if p.preco_minimo:
            q["precoMinimo"] = int(p.preco_minimo)
        if p.preco_maximo:
            q["precoMaximo"] = int(p.preco_maximo)
        if pagina > 1:
            q["pagina"] = pagina
        return q

    def _com_query(self, caminho: str, pagina: int) -> str:
        q = self.filtros_query(pagina)
        return urljoin(self.base_url, caminho) + (f"?{urlencode(q)}" if q else "")

    # --- Coleta com interceptação de JSON ----------------------------- #

    async def coletar_pagina(self, page: Any, pagina: int) -> list[Imovel]:
        capturados: list[Any] = []

        async def ao_responder(resp: Any) -> None:
            if RE_GLUE_API.search(resp.url) and resp.status == 200:
                try:
                    capturados.append(await resp.json())
                except Exception:  # noqa: BLE001 - corpo não-JSON ou já consumido
                    pass

        page.on("response", ao_responder)
        erro: Exception | None = None
        try:
            for url in self.urls_busca(pagina):
                capturados.clear()
                try:
                    html = await self.abrir(page, url)
                except Exception as exc:  # noqa: BLE001 - tenta a próxima URL candidata
                    erro = exc
                    continue
                await asyncio.sleep(0.5)  # deixa os handlers de resposta terminarem
                itens: list[Imovel] = []
                for dado in capturados:
                    itens.extend(self.imoveis_de_json(dado))
                if not itens and html:
                    itens = self.parse_search_html(html, url)
                if itens:
                    return _unicos(itens)
        finally:
            page.remove_listener("response", ao_responder)
        if erro:
            raise erro
        return []

    # --- Parsing de JSON (glue-api ou __NEXT_DATA__) ------------------- #

    def imoveis_de_json(self, dado: Any) -> list[Imovel]:
        saida: list[Imovel] = []
        for d in iterar_dicts(dado):
            listing = d.get("listing")
            if not isinstance(listing, dict) or "id" not in listing:
                continue
            try:
                saida.append(self._imovel_de_listing(d, listing))
            except Exception as exc:  # noqa: BLE001
                self.log.debug("Listing ignorado (%s): %s", exc, listing.get("id"))
        return saida

    def _imovel_de_listing(self, envelope: dict[str, Any], listing: dict[str, Any]) -> Imovel:
        endereco = listing.get("address") or {}
        conta = envelope.get("account") or listing.get("account") or {}
        href = ((envelope.get("link") or {}).get("href")) or listing.get("link") or ""
        if not href:
            href = f"/imovel/{listing['id']}/"
        link = urljoin(self.base_url, href)

        negocio = "SALE" if self.params.is_venda else "RENTAL"
        preco = None
        for info in listing.get("pricingInfos") or []:
            if info.get("businessType") == negocio:
                preco = parse_preco(info.get("price"))
                break

        area = parse_area(listing.get("usableAreas")) or parse_area(listing.get("totalAreas"))

        contato = listing.get("advertiserContact") or {}
        telefones = list(contato.get("phones") or [])
        if listing.get("whatsappNumber"):
            telefones.append(str(listing["whatsappNumber"]))
        nome = conta.get("name") or conta.get("tradingName") or ""

        quartos = listing.get("bedrooms")
        if isinstance(quartos, list):
            quartos = quartos[0] if quartos else None

        return Imovel(
            link=link,
            endereco=montar_endereco(
                endereco.get("street", ""),
                endereco.get("streetNumber", ""),
                endereco.get("neighborhood", ""),
                endereco.get("city", ""),
                endereco.get("stateAcronym", "") or endereco.get("state", ""),
            ),
            area_m2=area,
            valor_oferta=preco,
            fonte=self.nome,
            contato=montar_contato(nome, telefones),
            titulo=limpar_texto(listing.get("title", "")),
            tipo_imovel=", ".join(listing.get("unitTypes") or []),
            bairro=endereco.get("neighborhood", ""),
            cidade=endereco.get("city", ""),
            estado=endereco.get("stateAcronym", ""),
            quartos=int(quartos) if isinstance(quartos, (int, float)) else None,
            id_externo=str(listing.get("id", "")),
        )

    # --- Parsing de HTML ---------------------------------------------- #

    def parse_search_html(self, html: str, url: str) -> list[Imovel]:
        soup = soup_de(html)
        next_data = extrair_next_data(soup)
        if next_data:
            itens = self.imoveis_de_json(next_data)
            if itens:
                return _unicos(itens)

        saida: list[Imovel] = []
        cards = soup.select('[data-cy="rp-property-cd"]') or soup.select("li[data-cy], div[data-cy*='property']")
        for card in cards:
            a = card.find("a", href=True)
            if not a:
                continue
            def txt(sel: str) -> str:
                el = card.select_one(sel)
                return limpar_texto(el.get_text(" ")) if el else ""
            local = txt('[data-cy="rp-cardProperty-location-txt"]')
            rua = txt('[data-cy="rp-cardProperty-street-txt"]')
            area_txt = txt('[data-cy="rp-cardProperty-propertyArea-txt"]')
            preco_txt = txt('[data-cy="rp-cardProperty-price-txt"]')
            texto = limpar_texto(card.get_text(" "))
            bairro, cidade = _bairro_cidade_de_local(local)
            saida.append(Imovel(
                link=urljoin(url, a["href"]),
                endereco=montar_endereco(rua, "", bairro, cidade, self.params.estado) if (rua or bairro) else "",
                area_m2=parse_area(area_txt) or area_por_rotulo(texto),
                valor_oferta=parse_preco(preco_txt) or preco_por_rotulo(texto, self.preco_minimo_plausivel),
                fonte=self.nome,
                titulo=local,
                bairro=bairro,
                cidade=cidade,
                estado=self.params.estado,
            ))
        return _unicos(saida)

    def parse_detail_html(self, html: str, imovel: Imovel) -> Imovel:
        soup = soup_de(html)
        next_data = extrair_next_data(soup)
        if next_data:
            detalhes = self.imoveis_de_json(next_data)
            if detalhes:
                d = detalhes[0]
                imovel.endereco = d.endereco or imovel.endereco
                imovel.area_m2 = imovel.area_m2 or d.area_m2
                imovel.valor_oferta = imovel.valor_oferta or d.valor_oferta
                imovel.bairro = imovel.bairro or d.bairro
                if d.contato:
                    imovel.contato = d.contato if not imovel.contato else imovel.contato

        if not imovel.contato or "|" not in imovel.contato:
            nome = _primeiro_texto(soup, SELETORES_ANUNCIANTE)
            if nome:
                imovel.contato = _mesclar_contato(nome, _tels_de(imovel.contato))
        if not imovel.endereco:
            imovel.endereco = _primeiro_texto(soup, SELETORES_ENDERECO_DETALHE)

        tels = telefones_da_pagina(soup, self.seletor_contato)
        if tels:
            imovel.contato = _mesclar_contato(imovel.contato, tels)

        if imovel.area_m2 is None or imovel.valor_oferta is None:
            texto = texto_visivel(soup)
            imovel.area_m2 = imovel.area_m2 or area_por_rotulo(texto)
            imovel.valor_oferta = imovel.valor_oferta or preco_por_rotulo(texto, self.preco_minimo_plausivel)
        return imovel


class ZapScraper(GrupoZapScraper):
    nome = "ZAP Imóveis"
    base_url = "https://www.zapimoveis.com.br/"

    TIPOS = {
        "casa": "casas",
        "apartamento": "apartamentos",
        "terreno": "terrenos-lotes-condominios",
        "casa de condominio": "casas-de-condominio",
        "sala": "salas-comerciais",
        "loja": "lojas-salao",
        "galpao": "galpao-deposito-armazem",
    }

    def urls_busca(self, pagina: int) -> list[str]:
        p = self.params
        tipo = self.TIPOS[p.tipo_norm]
        trans = "venda" if p.is_venda else "aluguel"
        local_cidade = f"{p.estado_slug}+{p.cidade_slug}"
        urls = []
        if p.bairro_slug:
            # Formato "uf+cidade++bairro" (zona vazia) usado pelo ZAP.
            urls.append(self._com_query(f"/{trans}/{tipo}/{local_cidade}++{p.bairro_slug}/", pagina))
        urls.append(self._com_query(f"/{trans}/{tipo}/{local_cidade}/", pagina))
        return urls


class VivaRealScraper(GrupoZapScraper):
    nome = "VivaReal"
    base_url = "https://www.vivareal.com.br/"

    TIPOS = {
        "casa": "casa_residencial",
        "apartamento": "apartamento_residencial",
        "terreno": "lote-terreno_residencial",
        "casa de condominio": "condominio_residencial",
        "sala": "sala_comercial",
        "loja": "ponto-comercial_comercial",
        "galpao": "galpao_comercial",
    }

    def urls_busca(self, pagina: int) -> list[str]:
        p = self.params
        tipo = self.TIPOS[p.tipo_norm]
        trans = "venda" if p.is_venda else "aluguel"
        estado = _slug_estado(p.estado_nome)
        urls = []
        if p.bairro_slug:
            urls.append(self._com_query(
                f"/{trans}/{estado}/{p.cidade_slug}/bairros/{p.bairro_slug}/{tipo}/", pagina))
        urls.append(self._com_query(f"/{trans}/{estado}/{p.cidade_slug}/{tipo}/", pagina))
        return urls


# --------------------------------------------------------------------------- #
# Auxiliares
# --------------------------------------------------------------------------- #


def _slug_estado(nome: str) -> str:
    from garimpador.parsing import slugify

    return slugify(nome)


def _bairro_cidade_de_local(local: str) -> tuple[str, str]:
    """"Casa à venda em Aldeota, Fortaleza" -> ("Aldeota", "Fortaleza")."""
    texto = re.sub(r"(?i)^.*?\bem\s+", "", local or "")
    partes = [limpar_texto(x) for x in texto.split(",") if limpar_texto(x)]
    if len(partes) >= 2:
        return partes[0], partes[1]
    return (partes[0], "") if partes else ("", "")


def _primeiro_texto(soup: Any, seletores: tuple[str, ...]) -> str:
    for sel in seletores:
        el = soup.select_one(sel)
        if el:
            texto = limpar_texto(el.get_text(" "))
            if texto:
                return texto[:200]
    return ""


def _tels_de(contato: str) -> list[str]:
    return re.findall(r"\(\d{2}\) \d{4,5}-\d{4}", contato or "")


def _unicos(itens: list[Imovel]) -> list[Imovel]:
    vistos: dict[str, Imovel] = {}
    for i in itens:
        vistos.setdefault(i.link, i)
    return list(vistos.values())

