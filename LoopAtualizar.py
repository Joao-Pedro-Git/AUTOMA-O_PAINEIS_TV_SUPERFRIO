"""
Agendador diário da automação Pentaho.

Deixe este programa aberto. Ele iniciará automacao.py
no horário configurado.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import schedule

try:
    from testeUnitarios import registrar_logs
except ImportError:
    registrar_logs = None


# ============================================================
# CONFIGURAÇÕES
# ============================================================

# Coloque um horário futuro ao testar.
HORARIO_ATUALIZACAO_ONE = "13:42:00"

PASTA_PROJETO = Path(__file__).resolve().parent
ARQUIVO_AUTOMACAO = PASTA_PROJETO / "automacao.py"

INTERVALO_VERIFICACAO = 0.5
BLOQUEAR_EXECUCOES_SIMULTANEAS = True

# Altere temporariamente para True para testar agora.
EXECUTAR_IMEDIATAMENTE_PARA_TESTE = False


# ============================================================
# ESTADO E LOGGING
# ============================================================

processo_automacao: subprocess.Popen | None = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%d/%m/%Y %H:%M:%S",
)

logger = logging.getLogger("agendador")


# ============================================================
# VALIDAÇÕES
# ============================================================

def validar_horario(horario: str) -> None:
    """Valida horários HH:MM ou HH:MM:SS."""
    for formato in ("%H:%M", "%H:%M:%S"):
        try:
            datetime.strptime(horario, formato)
            return
        except ValueError:
            continue

    raise ValueError(
        f"Horário inválido: {horario!r}. "
        "Use HH:MM ou HH:MM:SS."
    )


def validar_arquivo_automacao() -> None:
    """Confirma que automacao.py existe na mesma pasta."""
    if ARQUIVO_AUTOMACAO.is_file():
        return

    arquivos_python = sorted(
        arquivo.name
        for arquivo in PASTA_PROJETO.glob("*.py")
    )

    raise FileNotFoundError(
        "O arquivo da automação não foi encontrado.\n\n"
        f"Esperado em:\n{ARQUIVO_AUTOMACAO}\n\n"
        "Coloque automacao.py na mesma pasta de LoopAtualizar.py.\n\n"
        f"Arquivos Python encontrados: {arquivos_python}"
    )


# ============================================================
# EXECUÇÃO
# ============================================================

def automacao_esta_executando() -> bool:
    return (
        processo_automacao is not None
        and processo_automacao.poll() is None
    )


def registrar_inicio() -> None:
    if registrar_logs is None:
        logger.warning(
            "testeUnitarios.registrar_logs não foi importado."
        )
        return

    try:
        registrar_logs()
    except Exception:
        logger.exception(
            "Falha em registrar_logs(); a automação continuará."
        )


def job() -> None:
    """Abre automacao.py em outro processo."""
    global processo_automacao

    logger.info(
        "Horário atingido: %s",
        datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
    )

    if (
        BLOQUEAR_EXECUCOES_SIMULTANEAS
        and automacao_esta_executando()
    ):
        logger.warning(
            "A automação anterior ainda está aberta. "
            "A nova execução foi ignorada."
        )
        return

    try:
        validar_arquivo_automacao()
        registrar_inicio()

        logger.info(
            "Iniciando automação: %s",
            ARQUIVO_AUTOMACAO,
        )

        processo_automacao = subprocess.Popen(
            [
                sys.executable,
                str(ARQUIVO_AUTOMACAO),
            ],
            cwd=str(PASTA_PROJETO),
        )

        logger.info(
            "Automação iniciada. PID: %s",
            processo_automacao.pid,
        )

    except Exception:
        logger.exception(
            "Não foi possível iniciar a automação."
        )


# ============================================================
# AGENDADOR
# ============================================================

def mostrar_proxima_execucao() -> None:
    proxima_execucao = schedule.next_run()

    if proxima_execucao is None:
        logger.warning("Nenhuma tarefa foi agendada.")
        return

    logger.info(
        "Próxima execução: %s",
        proxima_execucao.strftime("%d/%m/%Y %H:%M:%S"),
    )


def main() -> int:
    try:
        validar_horario(HORARIO_ATUALIZACAO_ONE)
        validar_arquivo_automacao()

        schedule.every().day.at(
            HORARIO_ATUALIZACAO_ONE
        ).do(job)

        logger.info("Agendador iniciado.")
        logger.info("Pasta do projeto: %s", PASTA_PROJETO)
        logger.info(
            "Horário atual: %s",
            datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        )
        logger.info(
            "Horário programado: %s",
            HORARIO_ATUALIZACAO_ONE,
        )

        mostrar_proxima_execucao()

        if EXECUTAR_IMEDIATAMENTE_PARA_TESTE:
            logger.warning(
                "Modo de teste ativo: executando imediatamente."
            )
            job()

        while True:
            try:
                schedule.run_pending()
            except Exception:
                logger.exception(
                    "Erro ao verificar ou executar tarefas."
                )

            time.sleep(INTERVALO_VERIFICACAO)

    except KeyboardInterrupt:
        logger.info("Agendador encerrado pelo usuário.")
        return 130

    except Exception:
        logger.exception(
            "Não foi possível iniciar o agendador."
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())