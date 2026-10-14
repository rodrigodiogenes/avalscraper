# avalscraper: Garimpador de Dados Imobiliários

Coleta ofertas de imóveis em portais (ZAP Imóveis, VivaReal, OLX, Chaves na Mão) e em sites de imobiliárias de Fortaleza, normaliza os campos e exporta para `imoveis_garimpados.csv` e `imoveis_garimpados.json`.

Cada linha da saída traz os seis campos obrigatórios (`link`, `endereco`, `area_m2`, `valor_oferta`, `fonte`, `contato`) e alguns extras úteis para análise (`titulo`, `bairro`, `cidade`, `estado`, `quartos`, `id_externo`, `coletado_em`).

## 1. Visão geral e arquitetura

```
garimpador/
  config.py         SearchParams: parâmetros de busca validados
  models.py         Imovel: o registro normalizado
  parsing.py        regex de preço, área, telefone, endereço e URL canônica
  html_utils.py     extratores genéricos: JSON-LD, __NEXT_DATA__, tel:/wa.me, rótulos
  browser.py        Playwright async, rotação de User-Agent/viewport, proxy, jitter 1-3 s
  scrapers/
    base.py         BaseScraper: paginação, pré-filtro, detalhe, "ver telefone", filtro final
    grupozap.py     ZapScraper e VivaRealScraper (intercepta o JSON da glue-api)
    olx.py          OLXScraper (JSON do Next.js)
    generico.py     SiteGenericoScraper (dirigido por YAML) e ChavesNaMaoScraper
  data/imobiliarias.yaml   as 8 imobiliárias locais
  pipeline.py       roda as fontes em paralelo, consolida, deduplica e exporta
  exporters.py      DataFrame, deduplicação por link, CSV/JSON
  recon.py          diagnóstico de um site para ajustar seletores
  cli.py            linha de comando
```

O fluxo de cada fonte é o mesmo, definido em `BaseScraper.run()`:

1. Monta as URLs de busca da página N (com fallback: URL com bairro, depois URL só da cidade).
2. Extrai os cards. Nos portais grandes a extração lê o JSON que a própria página consome, que é muito mais estável que o HTML.
3. Pré-filtra o que já se sabe estar fora da faixa de área/preço ou de outro bairro, sem gastar visita ao detalhe.
4. Visita o detalhe só quando falta algum campo ou telefone, e clica em "Ver telefone" se habilitado.
5. Aplica o filtro final estrito (`area_minima_m2 <= area <= area_maxima_m2`; anúncio sem área é descartado).
6. Registra no log `Página 1/5 processada - 12 imóveis válidos encontrados` e um resumo dos descartes por motivo.

Falhas são isoladas em três níveis: por anúncio (try/except por item), por URL candidata (tenta a próxima) e por fonte (uma fonte que cai não derruba as outras).

### Situação de cada fonte

| Fonte | Chave | Estratégia | Confiabilidade esperada |
|---|---|---|---|
| ZAP Imóveis | `zap` | JSON da glue-api, fallback `__NEXT_DATA__` e cards `data-cy` | Alta |
| VivaReal | `vivareal` | Idem ZAP | Alta |
| OLX | `olx` | `__NEXT_DATA__` da busca e `initial-data` do anúncio | Média; telefone só com login (não automatizado) |
| Chaves na Mão | `chavesnamao` | Genérico: links `/imovel/` + JSON-LD + rótulos | Média; URLs a confirmar com `recon` |
| 8 imobiliárias | `magnomuniz`, `fiducial`, `megaimoveis`, `cesarrego`, `torresdemeloneto`, `ciropaiva`, `alessandrobelchior`, `sj` | Genérico via YAML | **Não validado**: requer `recon` |

Importante: o código foi desenvolvido num ambiente sem acesso de rede a esses domínios. A lógica de parsing, filtros, deduplicação e exportação está coberta por testes, e o caminho de navegador (interceptação de JSON, paginação, clique em "Ver telefone") foi testado com Chromium real contra páginas simuladas. As URLs de busca e os seletores de cada site real, porém, ainda precisam ser confirmados na primeira execução com internet. As imobiliárias estão marcadas `validated: false` no YAML e geram um aviso no log até você ajustá-las.

## 2. Dependências e ambiente

Python 3.10 ou superior.

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium
```

`requirements.txt` instala `playwright`, `pandas`, `beautifulsoup4`, `lxml`, `pyyaml`, `pytest` e `pytest-asyncio`.

Se você já tem um Chromium instalado e não quer baixar outro, aponte para ele com `--chromium /caminho/do/chrome` ou com a variável `GARIMPADOR_CHROMIUM`.

Testes:

```bash
pytest -q
```

## 3. Uso

Cenário de referência (Casa à venda, Aldeota, Fortaleza-CE, 180 a 280 m², todas as fontes):

```bash
python -m garimpador
# ou, explicitando os parâmetros:
python -m garimpador --tipo Casa --transacao Venda --cidade Fortaleza --estado CE \
    --bairro Aldeota --area-min 180 --area-max 280
# ou pelo script de exemplo:
python exemplo_aldeota.py
```

Outros exemplos:

```bash
# Só os portais do Grupo ZAP, 2 páginas, sem clicar em telefone
python -m garimpador --fontes zap vivareal --max-paginas 2 --sem-telefone

# Apartamento para aluguel no Meireles, até R$ 6 mil
python -m garimpador --tipo Apartamento --transacao Aluguel --bairro Meireles \
    --area-min 70 --area-max 140 --preco-max 6000

# Cidade inteira, saída em outra pasta, navegador visível para depurar
python -m garimpador --bairro "" --saida resultados/ --headful

# Lista as chaves de fontes disponíveis
python -m garimpador listar-fontes
```

Uso programático:

```python
import asyncio
from garimpador.config import SearchParams
from garimpador.pipeline import garimpar

params = SearchParams(tipo_imovel="Casa", transacao="Venda", cidade="Fortaleza", estado="CE",
                      bairro="Aldeota", area_minima_m2=180, area_maxima_m2=280)
df = asyncio.run(garimpar(params))   # grava os arquivos e devolve o DataFrame
```

Parâmetros aceitos por `SearchParams`: `tipo_imovel` (Casa, Apartamento, Terreno, Casa de Condominio, Sala, Loja, Galpao), `transacao` (Venda, Aluguel), `cidade`, `estado` (UF), `bairro` (vazio para a cidade inteira), `area_minima_m2`, `area_maxima_m2`, `preco_minimo`, `preco_maximo`, além de `max_paginas`, `revelar_telefone` e `fontes`.

### Personalização dos filtros e das fontes

**Ajustar uma imobiliária (primeira execução).** Abra a busca de casas à venda no site, copie a URL e rode:

```bash
python -m garimpador recon "https://www.magnomuniz.com.br/<url-da-busca>"
```

O comando salva em `recon/<host>/` o HTML renderizado, as respostas JSON que a página buscou (APIs internas costumam aparecer aqui) e um `diagnostico.txt` com os padrões de link mais frequentes. Com isso, edite a entrada correspondente em `garimpador/data/imobiliarias.yaml`:

- `urls_busca`: troque pelo formato real, usando placeholders `{transacao} {tipo} {tipo_plural} {cidade} {bairro} {uf} {pagina} {area_min} {area_max} {preco_min} {preco_max}`.
- `padrao_link`: regex que identifica a URL de um anúncio.
- `seletor_card` e `seletores_detalhe` (`area`, `preco`, `endereco`, `anunciante`): opcionais, só se a heurística genérica errar.
- `seletor_contato`: onde procurar telefones após o clique.
- `validated: true` quando conferir uma amostra.

**Adicionar uma imobiliária nova** é só acrescentar um bloco ao YAML; ela passa a aparecer em `listar-fontes` com a `chave` definida.

**Adicionar um portal com lógica própria**: crie uma subclasse de `BaseScraper` implementando `urls_busca()` e `parse_search_html()` (e opcionalmente `parse_detail_html()`, `coletar_pagina()` e `revelar_telefone()`), e registre em `garimpador/scrapers/__init__.py`.

**Regras de filtro**: ficam em `BaseScraper.passa_pre_filtro()` e `passa_filtro_final()`. Por padrão os limites são inclusivos, anúncios sem área são descartados quando há faixa de área, e anúncios de outro bairro (portais misturam "imóveis próximos") também.

**Ao interpretar os números**, considere que a área de cada portal pode ser útil, privativa ou construída conforme o anunciante; o parser prefere área útil/construída quando ambas aparecem, e para faixas de lançamento ("180 - 220 m²") usa o mínimo.

## 4. Contato, "Ver telefone" e responsabilidade

O garimpador clica em botões "Ver telefone" quando `--sem-telefone` não é usado. Isso tem custos que convém ponderar:

- Cada clique é uma interação registrada pelo portal e aumenta a chance de bloqueio; em alguns portais o clique abre formulário de lead e o número só aparece após cadastro, caso em que o campo fica apenas com o nome do anunciante.
- Os Termos de Uso dos portais normalmente proíbem coleta automatizada. Use para análise própria, com volume baixo.
- Telefone de corretor pessoa física é dado pessoal pela LGPD. Guarde só o necessário, não revenda nem use para disparo em massa, e apague quando a análise terminar.

Na OLX o telefone exige login, que o garimpador não automatiza; o campo `contato` traz o nome do anunciante e telefones que ele tenha publicado na descrição.

## 5. Escala e rate limit

- **Ritmo**: o padrão é 1 a 3 s aleatórios entre requisições, uma página por vez em cada fonte e até 3 fontes em paralelo (`--concorrencia`). Para volumes maiores, aumente `--delay-min/--delay-max` antes de aumentar paralelismo; bloqueios em portais grandes vêm mais de frequência por IP do que de volume total.
- **Proxies**: `--proxy http://usuario:senha@host:porta`. Para rodadas longas, use proxies residenciais brasileiros com rotação por sessão (um IP por fonte e por execução), porque IPs de datacenter costumam ser desafiados pelo Cloudflare logo na primeira página.
- **Identidade**: cada fonte recebe contexto de navegador próprio, com User-Agent, viewport, `Accept-Language: pt-BR` e fuso de Fortaleza; a flag `navigator.webdriver` é escondida e imagens/fontes não são baixadas.
- **Bloqueio detectado**: respostas 403/429/503 ou títulos de desafio ("Just a moment", captcha) disparam espera crescente e novas tentativas. Se persistir, rode com `--headful` para ver o desafio, reduza o ritmo ou troque o proxy.
- **Agendamento**: para acompanhar o mercado, rode uma vez por dia (cron/Agendador de Tarefas) com `--saida resultados/AAAA-MM-DD/`, e compare execuções por `link` para ver entradas, saídas e reduções de preço.
- **Incremental**: o campo `id_externo` e o `link` canônico permitem manter um histórico em banco (SQLite/Postgres) e só visitar o detalhe de anúncios novos.
