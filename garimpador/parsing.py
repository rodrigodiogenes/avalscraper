"""Funções puras de limpeza e normalização (preço, área, telefone, endereço, URL).

Tudo aqui é determinístico e coberto por testes; os scrapers só devem chamar
estas funções, nunca reimplementar regex de preço/área por conta própria.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable, Optional, Union
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

Numero = Union[int, float]

# --------------------------------------------------------------------------- #
# Números no padrão brasileiro
# --------------------------------------------------------------------------- #

_RE_NUMERO_BR = re.compile(r"\d[\d.,]*")
_RE_MILHAR_PONTO = re.compile(r"^\d{1,3}(\.\d{3})+$")
_RE_MILHAR_VIRGULA = re.compile(r"^\d{1,3}(,\d{3}){2,}$")


def br_para_float(texto: str) -> Optional[float]:
    """Converte um número escrito no padrão brasileiro em float.

    "1.250.000,50" -> 1250000.5 ; "180,5" -> 180.5 ; "1.200" -> 1200.0 ;
    "180.5" -> 180.5 (ponto seguido de 1-2 dígitos é decimal).
    """
    s = texto.strip().replace(" ", "").replace(" ", "")
    if not s:
        return None
    if "." in s and "," in s:
        # O separador que aparece por último é o decimal.
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", "") if _RE_MILHAR_VIRGULA.match(s) else s.replace(",", ".")
    elif "." in s and _RE_MILHAR_PONTO.match(s):
        s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# Preço
# --------------------------------------------------------------------------- #

_RE_MULTIPLICADOR = re.compile(
    r"(\d[\d.,]*)\s*(mi\b|mi\.|milh[õo]es|milh[ãa]o|mm\b|mil\b|k\b)", re.IGNORECASE
)
_SEM_PRECO = re.compile(r"consult|sob\s+consulta|a\s+combinar|negoci", re.IGNORECASE)


def parse_preco(valor: Union[str, Numero, None]) -> Optional[float]:
    """Extrai o valor em reais de um texto de preço.

    Aceita "R$ 1.250.000", "R$ 1,25 mi", "850 mil", "1.2 milhão", números já
    numéricos (vindos de JSON) e devolve None para "Sob consulta" e afins.
    Quando o texto traz vários valores (ex.: "R$ 900.000 Condomínio R$ 800"),
    usa o primeiro, que nos cards dos portais é sempre o preço principal.
    """
    if valor is None:
        return None
    if isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float)):
        return float(valor) if valor > 0 else None

    texto = str(valor).strip()
    if not texto or _SEM_PRECO.search(texto) and not re.search(r"\d", texto):
        return None

    m = _RE_MULTIPLICADOR.search(texto)
    if m:
        base = br_para_float(m.group(1))
        if base is None:
            return None
        sufixo = m.group(2).lower()
        fator = 1_000.0 if sufixo in ("mil", "k") else 1_000_000.0
        return round(base * fator, 2)

    texto = re.sub(r"(?i)r\s*\$", " ", texto)
    m = _RE_NUMERO_BR.search(texto)
    if not m:
        return None
    numero = br_para_float(m.group(0).rstrip(".,"))
    if numero is None or numero <= 0:
        return None
    return numero


# --------------------------------------------------------------------------- #
# Área
# --------------------------------------------------------------------------- #

_RE_AREA_UNIDADE = re.compile(
    r"(\d[\d.,]*)(?:\s*(?:-|a|até)\s*\d[\d.,]*)?\s*(?:m²|m2|m\^2|metros?(?:\s+quadrados)?\b|mts?²?\b|m\b)",
    re.IGNORECASE,
)


def parse_area(valor: Union[str, Numero, Iterable[Numero], None]) -> Optional[float]:
    """Extrai a área em m².

    "180 m²" -> 180.0 ; "180,5m2" -> 180.5 ; "1.200 m²" -> 1200.0 ;
    "180 - 220 m²" -> 180.0 (faixa de lançamento: usa o mínimo, que é o valor
    conservador para o filtro). Também aceita número puro ou lista (JSON do
    Grupo ZAP traz `usableAreas: [180]`).
    """
    if valor is None or isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float)):
        return float(valor) if valor > 0 else None
    if isinstance(valor, (list, tuple)):
        numeros = [parse_area(v) for v in valor]
        numeros = [n for n in numeros if n]
        return min(numeros) if numeros else None

    texto = str(valor).strip()
    if not texto:
        return None
    m = _RE_AREA_UNIDADE.search(texto)
    bruto = m.group(1) if m else None
    if bruto is None:
        # Sem unidade: só aceita se o texto for apenas um número.
        apenas = re.fullmatch(r"\s*(\d[\d.,]*)\s*", texto)
        if not apenas:
            return None
        bruto = apenas.group(1)
    area = br_para_float(bruto.rstrip(".,"))
    if area is None or area <= 0:
        return None
    return area


def dentro_da_faixa(
    valor: Optional[float], minimo: Optional[float], maximo: Optional[float]
) -> bool:
    """True se `valor` estiver em [minimo, maximo] (limites inclusivos).

    Valor ausente nunca passa quando há limite definido.
    """
    if minimo is None and maximo is None:
        return True
    if valor is None:
        return False
    if minimo is not None and valor < minimo:
        return False
    if maximo is not None and valor > maximo:
        return False
    return True


# --------------------------------------------------------------------------- #
# Telefone
# --------------------------------------------------------------------------- #

_RE_TELEFONE = re.compile(
    r"(?:\+?55[\s.-]?)?\(?\b([1-9]{2})\)?[\s.-]?(9?\d{4})[\s.-]?(\d{4})\b"
)


def normalizar_telefone(bruto: str) -> Optional[str]:
    """Normaliza um telefone brasileiro para "(85) 99999-9999"."""
    digitos = re.sub(r"\D", "", bruto or "")
    if digitos.startswith("55") and len(digitos) in (12, 13):
        digitos = digitos[2:]
    if digitos.startswith("0") and len(digitos) in (11, 12):
        digitos = digitos[1:]
    if len(digitos) not in (10, 11) or digitos[0] == "0":
        return None
    ddd, resto = digitos[:2], digitos[2:]
    return f"({ddd}) {resto[:-4]}-{resto[-4:]}"


def extrair_telefones(texto: str) -> list[str]:
    """Encontra todos os telefones BR em um texto, já normalizados e sem repetição."""
    vistos: list[str] = []
    for m in _RE_TELEFONE.finditer(texto or ""):
        tel = normalizar_telefone("".join(m.groups()))
        if tel and tel not in vistos:
            vistos.append(tel)
    return vistos


def montar_contato(nome: str = "", telefones: Iterable[str] = ()) -> str:
    """Junta nome do anunciante e telefones em uma string única."""
    nome = limpar_texto(nome)
    tels = [t for t in (normalizar_telefone(t) or "" for t in telefones) if t]
    tels = list(dict.fromkeys(tels))
    partes = [p for p in (nome, " / ".join(tels)) if p]
    return " | ".join(partes)


# --------------------------------------------------------------------------- #
# Texto, endereço e URL
# --------------------------------------------------------------------------- #


def limpar_texto(texto: Optional[str]) -> str:
    if not texto:
        return ""
    return re.sub(r"\s+", " ", str(texto)).strip(" ,-|\n\t")


def montar_endereco(
    rua: str = "",
    numero: str = "",
    bairro: str = "",
    cidade: str = "",
    estado: str = "",
) -> str:
    """Monta "Rua X, 123 - Aldeota, Fortaleza - CE" omitindo partes vazias."""
    rua, numero = limpar_texto(rua), limpar_texto(str(numero or ""))
    bairro, cidade, estado = limpar_texto(bairro), limpar_texto(cidade), limpar_texto(estado)
    logradouro = ", ".join(p for p in (rua, numero) if p)
    local = " - ".join(p for p in (cidade, estado.upper()) if p)
    blocos = [p for p in (logradouro, bairro) if p]
    inicio = " - ".join(blocos)
    return ", ".join(p for p in (inicio, local) if p)


def remover_acentos(texto: str) -> str:
    nfkd = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def slugify(texto: str, sep: str = "-") -> str:
    """"São José do Rio Preto" -> "sao-jose-do-rio-preto"."""
    s = remover_acentos(texto).lower()
    s = re.sub(r"[^a-z0-9]+", sep, s)
    return s.strip(sep)


def normalizar_comparacao(texto: str) -> str:
    """Forma canônica para comparar bairros/cidades ("Aldeota " == "aldeota")."""
    return slugify(texto or "", sep=" ")


_PARAMS_RASTREIO = re.compile(
    r"^(utm_.*|fbclid|gclid|gad_source|ref|referrer|source|from|origin|tracking.*)$",
    re.IGNORECASE,
)


def canonical_url(url: str) -> str:
    """Normaliza a URL para deduplicação.

    Remove fragmento, barra final e parâmetros de rastreio, mas mantém
    parâmetros de identificação (sites ASP.NET como o da SJ usam querystring
    para identificar o imóvel).
    """
    if not url:
        return ""
    partes = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(partes.query, keep_blank_values=False)
             if not _PARAMS_RASTREIO.match(k)]
    query.sort()
    caminho = partes.path.rstrip("/") or "/"
    return urlunsplit(
        (partes.scheme.lower() or "https", partes.netloc.lower(), caminho, urlencode(query), "")
    )
