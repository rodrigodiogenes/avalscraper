"""Camada de navegador (Playwright async) com rotação de identidade e jitter."""

from __future__ import annotations

import asyncio
import logging
import os
import random
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator, Optional

log = logging.getLogger("garimpador.browser")

USER_AGENTS: tuple[str, ...] = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36 Edg/128.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.6 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0",
)

VIEWPORTS: tuple[tuple[int, int], ...] = ((1366, 768), (1440, 900), (1536, 864), (1920, 1080))

# Recursos que não carregamos para economizar banda e reduzir pegada.
_TIPOS_BLOQUEADOS = {"image", "media", "font"}


@dataclass
class BrowserConfig:
    headless: bool = True
    proxy: Optional[str] = None  # ex.: "http://user:senha@host:porta"
    delay_min: float = 1.0
    delay_max: float = 3.0
    timeout_ms: int = 45_000
    bloquear_midia: bool = True
    # Chromium já instalado no sistema (pula o `playwright install`).
    executable_path: Optional[str] = os.environ.get("GARIMPADOR_CHROMIUM") or None


async def jitter(cfg: BrowserConfig) -> None:
    """Pausa aleatória entre requisições (padrão 1 a 3 s)."""
    await asyncio.sleep(random.uniform(cfg.delay_min, cfg.delay_max))


def _proxy_dict(proxy: Optional[str]) -> Optional[dict[str, str]]:
    if not proxy:
        return None
    from urllib.parse import urlsplit

    p = urlsplit(proxy)
    out = {"server": f"{p.scheme}://{p.hostname}:{p.port}"}
    if p.username:
        out["username"] = p.username
        out["password"] = p.password or ""
    return out


class BrowserPool:
    """Abre um único Chromium e entrega contextos isolados por portal.

    Cada contexto recebe um User-Agent, viewport e cabeçalhos próprios, o que
    evita que todos os portais vejam a mesma "impressão digital".
    """

    def __init__(self, cfg: BrowserConfig) -> None:
        self.cfg = cfg
        self._pw = None
        self._browser = None

    async def __aenter__(self) -> "BrowserPool":
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:  # pragma: no cover - depende do ambiente
            raise RuntimeError(
                "Playwright não instalado. Rode: pip install playwright && "
                "python -m playwright install chromium"
            ) from exc
        self._pw = await async_playwright().start()
        try:
            self._browser = await self._pw.chromium.launch(
                headless=self.cfg.headless,
                proxy=_proxy_dict(self.cfg.proxy),
                executable_path=self.cfg.executable_path,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            )
        except Exception as exc:
            await self._pw.stop()
            raise RuntimeError(
                "Não foi possível iniciar o Chromium. Rode `python -m playwright install chromium` "
                "ou aponte um Chromium existente com --chromium / GARIMPADOR_CHROMIUM. "
                f"Detalhe: {str(exc).splitlines()[0]}"
            ) from exc
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()

    @asynccontextmanager
    async def contexto(self) -> AsyncIterator["object"]:
        """Contexto Playwright novo com identidade aleatória."""
        assert self._browser is not None, "Use BrowserPool dentro de 'async with'"
        ua = random.choice(USER_AGENTS)
        largura, altura = random.choice(VIEWPORTS)
        ctx = await self._browser.new_context(
            user_agent=ua,
            viewport={"width": largura, "height": altura},
            locale="pt-BR",
            timezone_id="America/Fortaleza",
            extra_http_headers={
                "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.6,en;q=0.5",
                "DNT": "1",
            },
        )
        ctx.set_default_timeout(self.cfg.timeout_ms)
        # Esconde a flag navigator.webdriver, o sinal de automação mais óbvio.
        await ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        if self.cfg.bloquear_midia:
            async def _rota(route):  # type: ignore[no-untyped-def]
                if route.request.resource_type in _TIPOS_BLOQUEADOS:
                    await route.abort()
                else:
                    await route.continue_()

            await ctx.route("**/*", _rota)
        try:
            yield ctx
        finally:
            await ctx.close()
