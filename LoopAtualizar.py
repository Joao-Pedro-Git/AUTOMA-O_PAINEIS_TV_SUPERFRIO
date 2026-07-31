"""
Agendador diário da automação Pentaho.

Executa três atualizações por dia e envia, para cada execução,
a Data/Hora inicial e a Data/Hora final correspondentes.

Estrutura:

    projeto/
    ├── LoopAtualizar.py
    ├── logs_tvs.txt
    └── utils/
        ├── __init__.py
        ├── gerar_relatorio.py
        └── register_logs.py
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import schedule

from utils.register_logs import registrar_logs


# ============================================================
# MODELO DOS AGENDAMENTOS
# ============================================================

@dataclass(frozen=True, slots=True)
class AgendamentoPentaho:
    """Configuração de uma execução diária do Pentaho."""

    nome: str

    # Horário em que gerar_relatorio.py será iniciado.
    horario_execucao: str

    # Horários enviados para os filtros do dashboard.
    horario_inicial_pentaho: str
    horario_final_pentaho: str

    # 0 = hoje, 1 = amanhã, -1 = ontem.
    deslocamento_dia_inicial: int = 0
    deslocamento_dia_final: int = 0


# ============================================================
# CONFIGURAÇÕES DOS TRÊS TURNOS
# ============================================================

AGENDAMENTOS: tuple[AgendamentoPentaho, ...] = (
    AgendamentoPentaho(
        nome="T1",
        horario_execucao="16:47:00", # 06:00:00
        horario_inicial_pentaho="14:00:00",
        horario_final_pentaho="21:45:00",
        deslocamento_dia_inicial=0,
        deslocamento_dia_final=0,
    ),
    AgendamentoPentaho(
        nome="T2",
        horario_execucao="14:00:00",
        horario_inicial_pentaho="12:00:00",
        horario_final_pentaho="23:30:00",
        deslocamento_dia_inicial=0,
        deslocamento_dia_final=0,
    ),
    AgendamentoPentaho(
        nome="T3",
        horario_execucao="22:00:00",
        horario_inicial_pentaho="05:00:00",
        horario_final_pentaho="06:30:00",
        deslocamento_dia_inicial=0,
        deslocamento_dia_final=0,
    ),
)


# ============================================================
# CAMINHOS E COMPORTAMENTO
# ============================================================

PASTA_PROJETO = Path(__file__).resolve().parent
PASTA_UTILS = PASTA_PROJETO / "utils/"

ARQUIVO_AUTOMACAO = (
    PASTA_UTILS / "gerar_relatorio.py"
)

INTERVALO_VERIFICACAO = 0.5
BLOQUEAR_EXECUCOES_SIMULTANEAS = True

# Para teste imediato, altere para True e escolha T1, T2 ou T3.
EXECUTAR_IMEDIATAMENTE_PARA_TESTE = False
AGENDAMENTO_TESTE = "T1"


# ============================================================
# ESTADO E LOGGING
# ============================================================

processo_automacao: subprocess.Popen | None = None
agendamento_em_execucao: str | None = None
controle_processo = threading.Lock()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%d/%m/%Y %H:%M:%S",
)

logger = logging.getLogger("agendador")


# ============================================================
# VALIDAÇÕES
# ============================================================

def validar_horario(
    horario: str,
    nome_campo: str,
) -> None:
    """Valida um horário no formato HH:MM ou HH:MM:SS."""
    for formato in (
        "%H:%M",
        "%H:%M:%S",
    ):
        try:
            datetime.strptime(
                horario,
                formato,
            )
            return

        except ValueError:
            continue

    raise ValueError(
        f"{nome_campo} inválido: {horario!r}. "
        "Use HH:MM ou HH:MM:SS."
    )


def validar_agendamento(
    agendamento: AgendamentoPentaho,
) -> None:
    """Valida todos os horários de um turno."""
    if not agendamento.nome.strip():
        raise ValueError(
            "O nome do agendamento não pode ficar vazio."
        )

    validar_horario(
        agendamento.horario_execucao,
        f"Horário de execução de {agendamento.nome}",
    )

    validar_horario(
        agendamento.horario_inicial_pentaho,
        f"Horário inicial de {agendamento.nome}",
    )

    validar_horario(
        agendamento.horario_final_pentaho,
        f"Horário final de {agendamento.nome}",
    )


def validar_agendamentos() -> None:
    """Valida os três agendamentos e impede horários duplicados."""
    horarios_execucao: set[str] = set()
    nomes: set[str] = set()

    if not AGENDAMENTOS:
        raise ValueError(
            "Nenhum agendamento foi configurado."
        )

    for agendamento in AGENDAMENTOS:
        validar_agendamento(
            agendamento
        )

        nome_normalizado = (
            agendamento.nome.strip().upper()
        )

        if nome_normalizado in nomes:
            raise ValueError(
                f"Nome de agendamento duplicado: {agendamento.nome}"
            )

        nomes.add(
            nome_normalizado
        )

        if (
            agendamento.horario_execucao
            in horarios_execucao
        ):
            raise ValueError(
                "Existem dois agendamentos no mesmo horário: "
                f"{agendamento.horario_execucao}"
            )

        horarios_execucao.add(
            agendamento.horario_execucao
        )


def validar_estrutura_projeto() -> None:
    """Confirma que todos os arquivos obrigatórios existem."""
    arquivos_obrigatorios = (
        PASTA_UTILS / "__init__.py",
        PASTA_UTILS / "gerar_relatorio.py",
        PASTA_UTILS / "register_logs.py",
    )

    ausentes = [
        arquivo
        for arquivo in arquivos_obrigatorios
        if not arquivo.is_file()
    ]

    if not ausentes:
        return

    encontrados = sorted(
        str(arquivo.relative_to(PASTA_PROJETO))
        for arquivo in PASTA_PROJETO.rglob("*.py")
    )

    raise FileNotFoundError(
        "A estrutura do projeto está incompleta.\n\n"
        "Arquivos ausentes:\n"
        + "\n".join(
            f"- {arquivo}"
            for arquivo in ausentes
        )
        + "\n\nArquivos Python encontrados:\n"
        + (
            "\n".join(
                f"- {arquivo}"
                for arquivo in encontrados
            )
            if encontrados
            else "- Nenhum"
        )
    )


# ============================================================
# DATA E HORA DOS FILTROS
# ============================================================

def montar_data_hora_pentaho(
    horario: str,
    deslocamento_dias: int = 0,
    referencia: datetime | None = None,
) -> str:
    
    momento_referencia = referencia or datetime.now()

    data_destino = (
        momento_referencia
        + timedelta(days=deslocamento_dias)
    ).date()

    horario_normalizado = datetime.strptime(
        horario,
        "%H:%M:%S",
    ).time()

    data_hora = datetime.combine(
        data_destino,
        horario_normalizado,
    )

    return data_hora.strftime(
        "%d/%m/%Y %H:%M:%S"
    )


def criar_horarios_da_execucao(
    agendamento: AgendamentoPentaho,
    referencia: datetime | None = None,
) -> tuple[str, str]:
    """Cria os dois valores completos enviados ao dashboard."""
    momento_referencia = referencia or datetime.now()

    hora_inicial = montar_data_hora_pentaho(
        horario=agendamento.horario_inicial_pentaho,
        deslocamento_dias=(
            agendamento.deslocamento_dia_inicial
        ),
        referencia=momento_referencia,
    )

    hora_final = montar_data_hora_pentaho(
        horario=agendamento.horario_final_pentaho,
        deslocamento_dias=(
            agendamento.deslocamento_dia_final
        ),
        referencia=momento_referencia,
    )

    return hora_inicial, hora_final


# ============================================================
# EXECUÇÃO DO RELATÓRIO
# ============================================================

def automacao_esta_executando() -> bool:
    """Verifica se gerar_relatorio.py ainda está em execução."""
    return (
        processo_automacao is not None
        and processo_automacao.poll() is None
    )


def criar_ambiente_execucao(
    agendamento: AgendamentoPentaho,
    hora_inicial: str,
    hora_final: str,
) -> dict[str, str]:
    """
    Cria uma cópia das variáveis de ambiente atuais e adiciona
    os valores que serão lidos por gerar_relatorio.py.
    """
    ambiente = os.environ.copy()

    ambiente.update(
        {
            "PENTAHO_NOME_AGENDAMENTO": (
                agendamento.nome
            ),
            "PENTAHO_HORA_INICIAL": (
                hora_inicial
            ),
            "PENTAHO_HORA_FINAL": (
                hora_final
            ),
        }
    )

    return ambiente


def registrar_inicio(
    agendamento: AgendamentoPentaho,
    hora_inicial: str,
    hora_final: str,
) -> None:
    """Registra no logs_tvs.txt qual turno está sendo executado."""
    mensagem = (
        f"Iniciando atualização {agendamento.nome} | "
        f"Filtro inicial: {hora_inicial} | "
        f"Filtro final: {hora_final} |"
    )

    try:
        sucesso = registrar_logs(
            mensagem
        )

        if not sucesso:
            logger.warning(
                "O log não foi salvo, mas a automação continuará."
            )

    except Exception:
        logger.exception(
            "Falha ao registrar o início; "
            "a automação continuará."
        )


def acompanhar_processo(
    processo: subprocess.Popen,
    agendamento: AgendamentoPentaho,
) -> None:
    """Aguarda o fim do subprocesso sem bloquear o agendador."""
    global agendamento_em_execucao

    codigo_saida = processo.wait()

    if codigo_saida == 0:
        logger.info(
            "Automação %s finalizada com sucesso.",
            agendamento.nome,
        )

        registrar_logs(
            f"Atualização {agendamento.nome} finalizada com sucesso |",
            " ------------------------------------------------------ "
        )

    else:
        logger.error(
            "Automação %s terminou com código %s.",
            agendamento.nome,
            codigo_saida,
        )

        registrar_logs(
            f"Atualização {agendamento.nome} terminou "
            f"com código {codigo_saida} |"
        )

    with controle_processo:
        agendamento_em_execucao = None


def job(
    agendamento: AgendamentoPentaho,
) -> None:
    """
    Executa um dos três agendamentos.

    O horário inicial e final são enviados como variáveis de
    ambiente ao processo gerar_relatorio.py.
    """
    global processo_automacao
    global agendamento_em_execucao

    momento_execucao = datetime.now()

    logger.info(
        "Horário atingido: %s | agendamento=%s",
        momento_execucao.strftime(
            "%d/%m/%Y %H:%M:%S"
        ),
        agendamento.nome,
    )

    with controle_processo:
        if (
            BLOQUEAR_EXECUCOES_SIMULTANEAS
            and automacao_esta_executando()
        ):
            logger.warning(
                "A automação %s ainda está ativa. "
                "A execução %s foi ignorada.",
                agendamento_em_execucao,
                agendamento.nome,
            )

            registrar_logs(
                f"Execução {agendamento.nome} ignorada: "
                "automação anterior ainda ativa |"
            )

            return

        hora_inicial, hora_final = (
            criar_horarios_da_execucao(
                agendamento=agendamento,
                referencia=momento_execucao,
            )
        )

        logger.info(
            "%s enviará ao Pentaho: início=%s | fim=%s",
            agendamento.nome,
            hora_inicial,
            hora_final,
        )

        try:
            validar_estrutura_projeto()

            registrar_inicio(
                agendamento=agendamento,
                hora_inicial=hora_inicial,
                hora_final=hora_final,
            )

            ambiente = criar_ambiente_execucao(
                agendamento=agendamento,
                hora_inicial=hora_inicial,
                hora_final=hora_final,
            )

            processo_automacao = subprocess.Popen(
                [
                    sys.executable,
                    str(ARQUIVO_AUTOMACAO),
                ],
                cwd=str(PASTA_PROJETO),
                env=ambiente,
            )

            agendamento_em_execucao = (
                agendamento.nome
            )

            logger.info(
                "Automação %s iniciada. PID: %s",
                agendamento.nome,
                processo_automacao.pid,
            )

            threading.Thread(
                target=acompanhar_processo,
                args=(
                    processo_automacao,
                    agendamento,
                ),
                daemon=True,
                name=f"monitor-{agendamento.nome}",
            ).start()

        except Exception:
            agendamento_em_execucao = None

            logger.exception(
                "Não foi possível iniciar a automação %s.",
                agendamento.nome,
            )


# ============================================================
# AGENDADOR
# ============================================================

def registrar_tarefas() -> None:
    """Registra T1, T2 e T3 no schedule."""
    schedule.clear()

    for agendamento in AGENDAMENTOS:
        tarefa = schedule.every().day.at(
            agendamento.horario_execucao
        ).do(
            job,
            agendamento,
        )

        logger.info(
            "Tarefa registrada: %s às %s | "
            "filtro %s até %s",
            agendamento.nome,
            agendamento.horario_execucao,
            agendamento.horario_inicial_pentaho,
            agendamento.horario_final_pentaho,
        )

        logger.debug(
            "Objeto da tarefa: %s",
            tarefa,
        )


def mostrar_proximas_execucoes() -> None:
    """Exibe no terminal todas as próximas execuções."""
    tarefas = sorted(
        schedule.get_jobs(),
        key=lambda tarefa: tarefa.next_run,
    )

    if not tarefas:
        logger.warning(
            "Nenhuma tarefa foi agendada."
        )
        return

    logger.info(
        "Próximas execuções:"
    )

    for tarefa in tarefas:
        logger.info(
            "- %s",
            tarefa.next_run.strftime(
                "%d/%m/%Y %H:%M:%S"
            ),
        )


def localizar_agendamento(
    nome: str,
) -> AgendamentoPentaho:
    """Localiza T1, T2 ou T3 pelo nome."""
    nome_normalizado = nome.strip().upper()

    for agendamento in AGENDAMENTOS:
        if (
            agendamento.nome.upper()
            == nome_normalizado
        ):
            return agendamento

    nomes = ", ".join(
        agendamento.nome
        for agendamento in AGENDAMENTOS
    )

    raise ValueError(
        f"Agendamento de teste {nome!r} não existe. "
        f"Opções: {nomes}"
    )


def main() -> int:
    """Inicializa as três tarefas e mantém o processo ativo."""
    try:
        validar_agendamentos()
        validar_estrutura_projeto()
        registrar_tarefas()

        logger.info(
            "Agendador iniciado."
        )

        logger.info(
            "Pasta do projeto: %s",
            PASTA_PROJETO,
        )

        logger.info(
            "Arquivo da automação: %s",
            ARQUIVO_AUTOMACAO,
        )

        logger.info(
            "Horário atual: %s",
            datetime.now().strftime(
                "%d/%m/%Y %H:%M:%S"
            ),
        )

        mostrar_proximas_execucoes()

        if EXECUTAR_IMEDIATAMENTE_PARA_TESTE:
            agendamento_teste = localizar_agendamento(
                AGENDAMENTO_TESTE
            )

            logger.warning(
                "Modo de teste ativo: executando %s agora.",
                agendamento_teste.nome,
            )

            job(
                agendamento_teste
            )

        while True:
            try:
                schedule.run_pending()

            except Exception:
                logger.exception(
                    "Erro ao verificar ou executar tarefas."
                )

            time.sleep(
                INTERVALO_VERIFICACAO
            )

    except KeyboardInterrupt:
        logger.info(
            "Agendador encerrado pelo usuário."
        )
        return 130

    except Exception:
        logger.exception(
            "Não foi possível iniciar o agendador."
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())