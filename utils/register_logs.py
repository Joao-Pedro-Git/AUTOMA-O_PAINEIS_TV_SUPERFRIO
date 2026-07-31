"""
Registro das execuções da automação em logs_tvs.txt.

Não abre o Bloco de Notas e não usa interface gráfica.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path


PASTA_UTILS = Path(__file__).resolve().parent
PASTA_PROJETO = PASTA_UTILS.parent

ARQUIVO_LOG = PASTA_PROJETO / "logs_tvs.txt"
FORMATO_DATA_HORA = "%d/%m/%Y %H:%M:%S"

logger = logging.getLogger(__name__)


def registrar_logs(
    mensagem: str = "Atualizando as TVs",
) -> bool:
    """Acrescenta uma linha ao arquivo logs_tvs.txt."""
    momento_atual = datetime.now().strftime(
        FORMATO_DATA_HORA
    )

    texto_final = f"{mensagem} {momento_atual}"

    try:
        ARQUIVO_LOG.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        with ARQUIVO_LOG.open(
            mode="a",
            encoding="utf-8",
            newline="",
        ) as arquivo:
            arquivo.write(
                texto_final + "\n"
            )
            arquivo.flush()
            os.fsync(
                arquivo.fileno()
            )

        logger.info(
            "Log registrado: %s",
            texto_final,
        )

        return True

    except OSError:
        logger.exception(
            "Não foi possível registrar o log em %s.",
            ARQUIVO_LOG,
        )
        return False


def main() -> int:
    return 0 if registrar_logs() else 1


if __name__ == "__main__":
    raise SystemExit(main())