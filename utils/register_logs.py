"""
Registro das execuções no arquivo logs_tvs.txt.

No código-fonte:
    salva na raiz acima de utils.

No executável:
    salva ao lado de LoopAtualizar.exe.
"""

from __future__ import annotations

import logging
import os
import sys

from datetime import datetime
from pathlib import Path


def obter_pasta_projeto() -> Path:
    """Retorna a pasta permanente da aplicação."""
    if getattr(
        sys,
        "frozen",
        False,
    ):
        return Path(
            sys.executable
        ).resolve().parent

    return Path(
        __file__
    ).resolve().parent.parent


PASTA_PROJETO = obter_pasta_projeto()
ARQUIVO_LOG = PASTA_PROJETO / "logs_tvs.txt"

logger = logging.getLogger(
    __name__
)


def registrar_logs(
    mensagem: str = "Atualizando as TVs",
) -> bool:
    """Acrescenta uma linha ao arquivo logs_tvs.txt."""
    data_hora = datetime.now().strftime(
        "%d/%m/%Y %H:%M:%S"
    )

    linha = (
        f"{mensagem} {data_hora}"
    )

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
                linha + "\n"
            )

            arquivo.flush()

            try:
                os.fsync(
                    arquivo.fileno()
                )
            except OSError:
                pass

        logger.info(
            "Log registrado: %s",
            linha,
        )

        return True

    except OSError:
        logger.exception(
            "Não foi possível registrar em %s.",
            ARQUIVO_LOG,
        )

        return False


if __name__ == "__main__":
    raise SystemExit(
        0 if registrar_logs() else 1
    )