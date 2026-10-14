"""Ponto de entrada: `python -m garimpador`.

Sem argumentos, executa o cenário de referência:
Casa à venda, Aldeota, Fortaleza-CE, de 180 m² a 280 m², em todas as fontes.
"""

import sys

from garimpador.cli import main

if __name__ == "__main__":
    sys.exit(main())
