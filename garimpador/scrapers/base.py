"""Classe base de todos os adaptadores de portal."""

from __future__ import annotations

import asyncio
import logging
import re
from abc import ABC, abstractmethod
from typing import Any, Optional

from garimpador.browser import BrowserConfig, BrowserPool, jitter
from garimpador.config import SearchParams
from garimpador.html_utils import soup_de, telefones_da_pagina
from garimpador.models import Imovel
from garimpador.parsing import (
    dentro_da_faixa,
    limpar_texto,
    montar_contato,
    normalizar_comparacao,
)

# Textos típicos de botões que revelam o telefone.
PADRAO_BOTAO_TELEFONE = re.compile(
    r"(ver|mostrar|exibir|visualizar)\s+(o\s+)?(telefone|n[úu]mero|contato|fone)|"
    r"^\s*telefone\s*$|ligar",
    re.IGNORECASE,
)

# Sinais de que caímos em uma tela de desafio anti-bot.
PADRAO_BLOQUEIO = re.compile(
    r"just a moment|attention required|access denied|verifique se voc[êe] [ée] humano|"
    r"are you a robot|captcha|request blocked|acesso negado",
    re.IGNORECASE,
)


class BloqueioDetectado(RuntimeError):
    """O portal devolveu uma página de desafio (Cloudflare, Akamai, captcha)."""


class BaseScraper(ABC):
    """Fluxo comum: paginação, pré-filtro, enriquecimento no detalhe e filtro final.

    Um adaptador concreto precisa implementar `urls_busca` e
    `parse_search_html`; pode sobrescrever `coletar_pagina` (para interceptar
    JSON), `parse_detail_html` e `revelar_telefone`.
    """

    nome: str = "Base"
    # Seletor CSS da área de contato na página de detalhe (para regex de telefone).
    seletor_contato: Optional[str] = None
    # Se False, nunca tentamos clicar em "ver telefone" (ex.: OLX exige login).
    suporta_revelar_telefone: bool = True
    # Valor mínimo plausível de preço para não confundir com condomínio/IPTU.
    preco_minimo_plausivel_venda: float = 10_000.0
    preco_minimo_plausivel_aluguel: float = 200.0
    max_tentativas: int = 3

    def __init__(self, params: SearchParams, pool: BrowserPool, cfg: BrowserConfig) -> None:
        self.params = params
        self.pool = pool
        self.cfg = cfg
        self.log = logging.getLogger(f"garimpador.{self.nome}")
        self.descartes: dict[str, int] = {}

    # ------------------------------------------------------------------ #
    # Contrato do adaptador
    # ------------------------------------------------------------------ #

    @abstractmethod
    def urls_busca(self, pagina: int) -> list[str]:
        """URLs candidatas para a página `pagina` (1-based), em ordem de preferência.

        Retornar mais de uma permite fallback: por exemplo, URL com bairro no
        caminho e, se ela não render resultados, URL só da cidade com filtro de
        bairro feito localmente.
        """

    @abstractmethod
    def parse_search_html(self, html: str, url: str) -> list[Imovel]:
        """Extrai anúncios (parcialmente preenchidos) de uma página de resultados."""

    def parse_detail_html(self, html: str, imovel: Imovel) -> Imovel:
        """Completa o anúncio com dados da página de detalhe. Padrão: só telefones."""
        soup = soup_de(html)
        tels = telefones_da_pagina(soup, self.seletor_contato)
        if tels:
            imovel.contato = _mesclar_contato(imovel.contato, tels)
        return imovel

    @property
    def preco_minimo_plausivel(self) -> float:
        return (
            self.preco_minimo_plausivel_venda
            if self.params.is_venda
            else self.preco_minimo_plausivel_aluguel
        )

    # ------------------------------------------------------------------ #
    # Navegação
    # ------------------------------------------------------------------ #

    async def abrir(self, page: Any, url: str) -> str:
        """Navega com retry e backoff exponencial; devolve o HTML final."""
        ultimo_erro: Optional[Exception] = None
        for tentativa in range(1, self.max_tentativas + 1):
            try:
                resp = await page.goto(url, wait_until="domcontentloaded")
                await self.aguardar_conteudo(page)
                html = await page.content()
                status = resp.status if resp else 0
                titulo = await page.title()
                if status in (403, 429, 503) or PADRAO_BLOQUEIO.search(titulo or ""):
                    raise BloqueioDetectado(f"HTTP {status} / título {titulo!r} em {url}")
                if status == 404:
                    return ""
                return html
            except BloqueioDetectado as exc:
                ultimo_erro = exc
                espera = 5 * tentativa
                self.log.warning("Bloqueio anti-bot (%s). Aguardando %ss.", exc, espera)
                await asyncio.sleep(espera)
            except Exception as exc:  # noqa: BLE001 - rede/timeouts do Playwright
                ultimo_erro = exc
                espera = 2 ** tentativa
                self.log.warning("Falha ao abrir %s (tentativa %d/%d): %s",
                                 url, tentativa, self.max_tentativas, _resumo_erro(exc))
                await asyncio.sleep(espera)
        raise RuntimeError(f"Não foi possível abrir {url}: {_resumo_erro(ultimo_erro)}")

    async def aguardar_conteudo(self, page: Any) -> None:
        """Espera a hidratação da página. Sobrescreva para esperar um seletor específico."""
        try:
            await page.wait_for_load_state("networkidle", timeout=12_000)
        except Exception:  # noqa: BLE001 - networkidle nunca chega em sites com polling
            pass

    async def coletar_pagina(self, page: Any, pagina: int) -> list[Imovel]:
        """Tenta as URLs candidatas da página até alguma devolver anúncios.

        Uma URL candidata que falha (404, rede, bloqueio) não interrompe a
        página: passamos à próxima e só propagamos o erro se todas falharem.
        """
        erro: Optional[Exception] = None
        for url in self.urls_busca(pagina):
            try:
                html = await self.abrir(page, url)
            except Exception as exc:  # noqa: BLE001
                erro = exc
                continue
            if not html:
                continue
            itens = self.parse_search_html(html, url)
            if itens:
                return itens
            self.log.debug("Nenhum anúncio em %s; tentando próxima URL candidata.", url)
        if erro:
            raise erro
        return []

    async def revelar_telefone(self, page: Any) -> list[str]:
        """Clica no botão "ver telefone" e lê os números revelados."""
        botoes = page.get_by_role("button", name=PADRAO_BOTAO_TELEFONE)
        links = page.get_by_role("link", name=PADRAO_BOTAO_TELEFONE)
        clicou = False
        for grupo in (botoes, links):
            try:
                total = await grupo.count()
            except Exception:  # noqa: BLE001
                continue
            for i in range(min(total, 3)):
                alvo = grupo.nth(i)
                try:
                    if await alvo.is_visible():
                        await alvo.click(timeout=4_000)
                        clicou = True
                        await asyncio.sleep(1.5)
                except Exception:  # noqa: BLE001 - botão coberto, modal etc.
                    continue
        if not clicou:
            return []
        html = await page.content()
        return telefones_da_pagina(soup_de(html), self.seletor_contato or "body")

    # ------------------------------------------------------------------ #
    # Regras de filtro
    # ------------------------------------------------------------------ #

    def _descartar(self, motivo: str) -> bool:
        self.descartes[motivo] = self.descartes.get(motivo, 0) + 1
        return False

    def bairro_confere(self, imovel: Imovel) -> bool:
        """Portais misturam "imóveis próximos" nos resultados; filtramos pelo bairro."""
        if not self.params.bairro:
            return True
        alvo = normalizar_comparacao(self.params.bairro)
        campo = normalizar_comparacao(imovel.bairro) if imovel.bairro else ""
        if campo:
            return campo == alvo
        return alvo in normalizar_comparacao(f"{imovel.endereco} {imovel.titulo}")

    def passa_pre_filtro(self, imovel: Imovel) -> bool:
        """Descarta cedo o que já sabemos estar fora (economiza visitas ao detalhe)."""
        p = self.params
        if imovel.area_m2 is not None and not dentro_da_faixa(
            imovel.area_m2, p.area_minima_m2, p.area_maxima_m2
        ):
            return self._descartar("area_fora_da_faixa")
        if imovel.valor_oferta is not None and not dentro_da_faixa(
            imovel.valor_oferta, p.preco_minimo, p.preco_maximo
        ):
            return self._descartar("preco_fora_da_faixa")
        if (imovel.bairro or imovel.endereco) and not self.bairro_confere(imovel):
            return self._descartar("outro_bairro")
        return True

    def passa_filtro_final(self, imovel: Imovel) -> bool:
        """Regra estrita: área obrigatória e dentro da faixa; preço se houver limite."""
        p = self.params
        if not imovel.link:
            return self._descartar("sem_link")
        if not dentro_da_faixa(imovel.area_m2, p.area_minima_m2, p.area_maxima_m2):
            motivo = "sem_area" if imovel.area_m2 is None else "area_fora_da_faixa"
            return self._descartar(motivo)
        if not dentro_da_faixa(imovel.valor_oferta, p.preco_minimo, p.preco_maximo):
            motivo = "sem_preco" if imovel.valor_oferta is None else "preco_fora_da_faixa"
            return self._descartar(motivo)
        if not self.bairro_confere(imovel):
            return self._descartar("outro_bairro")
        return True

    def precisa_detalhe(self, imovel: Imovel) -> bool:
        faltando = not imovel.endereco or imovel.area_m2 is None or imovel.valor_oferta is None
        sem_telefone = not re.search(r"\(\d{2}\)", imovel.contato or "")
        quer_telefone = self.params.revelar_telefone and self.suporta_revelar_telefone
        return faltando or not imovel.contato or (sem_telefone and quer_telefone)

    # ------------------------------------------------------------------ #
    # Orquestração
    # ------------------------------------------------------------------ #

    async def enriquecer(self, page: Any, imovel: Imovel) -> Imovel:
        html = await self.abrir(page, imovel.link)
        if html:
            imovel = self.parse_detail_html(html, imovel)
        if (
            self.params.revelar_telefone
            and self.suporta_revelar_telefone
            and not re.search(r"\(\d{2}\)", imovel.contato or "")
        ):
            try:
                tels = await self.revelar_telefone(page)
                if tels:
                    imovel.contato = _mesclar_contato(imovel.contato, tels)
            except Exception as exc:  # noqa: BLE001
                self.log.debug("Não revelou telefone em %s: %s", imovel.link, exc)
        imovel.fonte = imovel.fonte or self.nome
        return imovel

    async def run(self) -> list[Imovel]:
        """Executa a coleta completa deste portal. Nunca propaga exceção de item."""
        validos: list[Imovel] = []
        vistos: set[str] = set()
        total_paginas = self.params.max_paginas
        async with self.pool.contexto() as ctx:
            page = await ctx.new_page()
            for pagina in range(1, total_paginas + 1):
                try:
                    candidatos = await self.coletar_pagina(page, pagina)
                except Exception as exc:  # noqa: BLE001
                    self.log.error("Página %d/%d falhou: %s", pagina, total_paginas, exc)
                    break
                novos = [c for c in candidatos if c.link and c.link not in vistos]
                if not novos:
                    self.log.info("Página %d/%d sem anúncios novos; fim da paginação.",
                                  pagina, total_paginas)
                    break
                validos_pagina = 0
                for imovel in novos:
                    vistos.add(imovel.link)
                    imovel.fonte = self.nome
                    try:
                        if not self.passa_pre_filtro(imovel):
                            continue
                        if self.precisa_detalhe(imovel):
                            await jitter(self.cfg)
                            imovel = await self.enriquecer(page, imovel)
                        if self.passa_filtro_final(imovel):
                            validos.append(imovel)
                            validos_pagina += 1
                    except Exception as exc:  # noqa: BLE001 - falha isolada por anúncio
                        self._descartar("erro_no_item")
                        self.log.warning("Erro no anúncio %s: %s", imovel.link, exc)
                self.log.info(
                    "Página %d/%d processada - %d imóveis válidos encontrados (%d anúncios lidos)",
                    pagina, total_paginas, validos_pagina, len(novos),
                )
                await jitter(self.cfg)
        if self.descartes:
            self.log.info("Descartes: %s", self.descartes)
        self.log.info("Total válido: %d", len(validos))
        return validos


def _resumo_erro(exc: Optional[BaseException]) -> str:
    """Primeira linha da exceção (o Playwright anexa um "call log" longo)."""
    return (str(exc).strip().splitlines() or [type(exc).__name__])[0] if exc else ""


def _mesclar_contato(contato: str, telefones: list[str]) -> str:
    """Acrescenta telefones novos ao campo contato preservando o nome."""
    contato = limpar_texto(contato)
    nome, _, tels_existentes = contato.partition(" | ")
    if re.search(r"\(\d{2}\)", nome):  # contato era só telefone
        nome, tels_existentes = "", nome
    atuais = [t.strip() for t in tels_existentes.split("/") if t.strip()]
    return montar_contato(nome, [*atuais, *telefones])
