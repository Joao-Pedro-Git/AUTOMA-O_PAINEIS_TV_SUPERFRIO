"""
Agendador diário da automação Pentaho.

Executa três atualizações por dia e envia, para cada execução,
a Data/Hora inicial e a Data/Hora final correspondentes.

Em desenvolvimento:
    LoopAtualizar.py inicia utils/gerar_relatorio.py.

Depois da compilação:
    LoopAtualizar.exe inicia gerar_relatorio.exe.

Os dois executáveis precisam permanecer na mesma pasta.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
import traceback

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
    horario_execucao: str
    horario_inicial_pentaho: str
    horario_final_pentaho: str
    deslocamento_dia_inicial: int = 0
    deslocamento_dia_final: int = 0


# ============================================================
# CONFIGURAÇÕES DOS TRÊS TURNOS
# ============================================================

AGENDAMENTOS: tuple[AgendamentoPentaho, ...] = (
    AgendamentoPentaho(
        nome="T1",
        horario_execucao="05:45:00", # Esse horarios é responsável por disparar a automação.
        horario_inicial_pentaho="06:00:00", # Esse horario é responsável pela data INICIAL do RELATOIO DO PENTAHO.
        horario_final_pentaho="14:00:00", # Esse horario é responsável pela data FINAL do RELATOIO DO PENTAHO.
        deslocamento_dia_inicial=0,
        deslocamento_dia_final=0,
    ),
    AgendamentoPentaho(
        nome="T2",
        horario_execucao="14:25:00",
        horario_inicial_pentaho="14:00:00",
        horario_final_pentaho="22:00:00",
        deslocamento_dia_inicial=0,
        deslocamento_dia_final=0,
    ),
    AgendamentoPentaho(
        nome="T3",
        horario_execucao="21:45:00",
        horario_inicial_pentaho="22:00:00",
        horario_final_pentaho="05:50:00",
        deslocamento_dia_inicial=0,
        deslocamento_dia_final=0,
    ),
)


# ============================================================
# CAMINHOS E COMPORTAMENTO
# ============================================================

EXECUTANDO_COMO_EXE = bool(
    getattr(sys, "frozen", False)
)


def obter_pasta_aplicacao() -> Path:
    """
    Retorna a pasta permanente da aplicação.

    Código-fonte:
        pasta onde está LoopAtualizar.py.

    Executável:
        pasta onde está LoopAtualizar.exe.

    sys._MEIPASS não é utilizado para arquivos persistentes,
    porque no modo --onefile ele aponta para uma pasta temporária.
    """
    if EXECUTANDO_COMO_EXE:
        return Path(
            sys.executable
        ).resolve().parent

    return Path(
        __file__
    ).resolve().parent


PASTA_PROJETO = obter_pasta_aplicacao()
PASTA_UTILS = PASTA_PROJETO / "utils"

if EXECUTANDO_COMO_EXE:
    # No modo executável, gerar_relatorio.exe deve ficar
    # ao lado de LoopAtualizar.exe.
    ARQUIVO_AUTOMACAO = (
        PASTA_PROJETO
        / "gerar_relatorio.exe"
    )
else:
    # No modo de desenvolvimento, executa o arquivo Python.
    ARQUIVO_AUTOMACAO = (
        PASTA_UTILS
        / "gerar_relatorio.py"
    )


ARQUIVO_ERRO = (
    PASTA_PROJETO
    / "erro.txt"
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
controle_erro = threading.Lock()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%d/%m/%Y %H:%M:%S",
)

logger = logging.getLogger("agendador")


# ============================================================
# REGISTRO DE ERROS DO AGENDADOR
# ============================================================

def registrar_erro(
    etapa: str,
    erro: BaseException | str,
    traceback_texto: str | None = None,
) -> None:
    """Acrescenta uma ocorrência no erro.txt ao lado do EXE."""
    momento = datetime.now().strftime(
        "%d/%m/%Y %H:%M:%S"
    )

    if isinstance(erro, BaseException):
        tipo = type(erro).__name__
        mensagem = str(erro)
    else:
        tipo = "Erro"
        mensagem = str(erro)

    if traceback_texto is None:
        traceback_texto = traceback.format_exc()

        if traceback_texto.strip() == "NoneType: None":
            traceback_texto = ""

    bloco = (
        "\n"
        + "=" * 80
        + "\n"
        + f"DATA/HORA: {momento}\n"
        + f"ORIGEM: LoopAtualizar\n"
        + f"ETAPA: {etapa}\n"
        + f"TIPO: {tipo}\n"
        + f"MENSAGEM: {mensagem}\n"
    )

    if traceback_texto:
        bloco += (
            "\nTRACEBACK:\n"
            + traceback_texto.rstrip()
            + "\n"
        )

    bloco += "=" * 80 + "\n"

    try:
        with controle_erro:
            with ARQUIVO_ERRO.open(
                mode="a",
                encoding="utf-8",
                newline="",
            ) as arquivo:
                arquivo.write(bloco)
                arquivo.flush()

                try:
                    os.fsync(arquivo.fileno())
                except OSError:
                    pass

    except OSError:
        logger.exception(
            "Não foi possível gravar %s.",
            ARQUIVO_ERRO,
        )


# ============================================================
# VALIDAÇÕES
# ============================================================

def validar_horario(
    horario: str,
    nome_campo: str,
) -> None:
    """Valida um horário HH:MM ou HH:MM:SS."""
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
    """Valida todos os dados de um turno."""
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
    """Valida os turnos e impede nomes/horários duplicados."""
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
                f"Nome duplicado: {agendamento.nome}"
            )

        nomes.add(
            nome_normalizado
        )

        if agendamento.horario_execucao in horarios_execucao:
            raise ValueError(
                "Existem dois agendamentos no mesmo horário: "
                f"{agendamento.horario_execucao}"
            )

        horarios_execucao.add(
            agendamento.horario_execucao
        )

def validar_estrutura_projeto() -> None:
    """
    Valida a estrutura conforme o modo atual.

    Em desenvolvimento:
        exige os arquivos Python dentro de utils.

    No executável:
        exige somente gerar_relatorio.exe ao lado de
        LoopAtualizar.exe. Os módulos importados, como
        utils.register_logs, já ficam incorporados no EXE.
    """
    if EXECUTANDO_COMO_EXE:
        if ARQUIVO_AUTOMACAO.is_file():
            return

        executaveis_encontrados = sorted(
            arquivo.name
            for arquivo in PASTA_PROJETO.glob(
                "*.exe"
            )
        )

        raise FileNotFoundError(
            "O executável gerar_relatorio.exe não foi encontrado.\n\n"
            f"Esperado em:\n{ARQUIVO_AUTOMACAO}\n\n"
            "Mantenha LoopAtualizar.exe e gerar_relatorio.exe "
            "juntos na mesma pasta.\n\n"
            "Executáveis encontrados: "
            f"{executaveis_encontrados}"
        )

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

    arquivos_encontrados = sorted(
        str(
            arquivo.relative_to(
                PASTA_PROJETO
            )
        )
        for arquivo in PASTA_PROJETO.rglob(
            "*.py"
        )
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
                for arquivo in arquivos_encontrados
            )
            if arquivos_encontrados
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
    """Monta DD/MM/AAAA HH:MM:SS para os filtros."""
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
    """Cria os valores completos enviados ao relatório."""
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
    """Verifica se a automação anterior ainda está ativa."""
    return (
        processo_automacao is not None
        and processo_automacao.poll() is None
    )


def criar_ambiente_execucao(
    agendamento: AgendamentoPentaho,
    hora_inicial: str,
    hora_final: str,
) -> dict[str, str]:
    """Cria o ambiente que será recebido pelo relatório."""
    ambiente = os.environ.copy()

    ambiente.update(
        {
            "PENTAHO_NOME_AGENDAMENTO": agendamento.nome,
            "PENTAHO_HORA_INICIAL": hora_inicial,
            "PENTAHO_HORA_FINAL": hora_final,
        }
    )

    if EXECUTANDO_COMO_EXE:
        # Faz gerar_relatorio.exe iniciar como outro aplicativo
        # PyInstaller independente.
        ambiente["PYINSTALLER_RESET_ENVIRONMENT"] = "1"

    return ambiente


def registrar_inicio(
    agendamento: AgendamentoPentaho,
    hora_inicial: str,
    hora_final: str,
) -> None:
    """Registra o início no logs_tvs.txt."""
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

    except Exception as erro:
        logger.exception(
            "Falha ao registrar o início."
        )

        registrar_erro(
            "Registrar início",
            erro,
        )


def montar_comando_automacao() -> list[str]:
    """
    Monta o comando certo para cada modo.

    No EXE, não usa sys.executable como Python porque ele aponta
    para LoopAtualizar.exe.
    """
    if EXECUTANDO_COMO_EXE:
        return [
            str(ARQUIVO_AUTOMACAO),
        ]

    return [
        sys.executable,
        str(ARQUIVO_AUTOMACAO),
    ]


def acompanhar_processo(
    processo: subprocess.Popen,
    agendamento: AgendamentoPentaho,
) -> None:
    """Acompanha o subprocesso sem bloquear o agendador."""
    global agendamento_em_execucao
    global processo_automacao

    try:
        codigo_saida = processo.wait()

        if codigo_saida == 0:
            logger.info(
                "Automação %s finalizada com sucesso.",
                agendamento.nome,
            )

            # registrar_logs aceita uma única mensagem.
            registrar_logs(
                (
                    f"Atualização {agendamento.nome} "
                    "finalizada com sucesso | "
                    + "-" * 54
                )
            )

        else:
            mensagem = (
                f"Automação {agendamento.nome} terminou "
                f"com código {codigo_saida}."
            )

            logger.error(
                mensagem
            )

            registrar_logs(
                (
                    f"Atualização {agendamento.nome} terminou "
                    f"com código {codigo_saida} |"
                )
            )

            registrar_erro(
                "Subprocesso gerar_relatorio",
                mensagem,
                traceback_texto="",
            )

    except Exception as erro:
        logger.exception(
            "Erro ao acompanhar a automação %s.",
            agendamento.nome,
        )

        registrar_erro(
            f"Acompanhar processo {agendamento.nome}",
            erro,
        )

    finally:
        with controle_processo:
            agendamento_em_execucao = None
            processo_automacao = None


def job(
    agendamento: AgendamentoPentaho,
) -> None:
    """Inicia um dos três agendamentos."""
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
                (
                    f"Execução {agendamento.nome} ignorada: "
                    "automação anterior ainda ativa |"
                )
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

            comando = montar_comando_automacao()

            logger.info(
                "Comando da automação: %s",
                comando,
            )

            processo_automacao = subprocess.Popen(
                comando,
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

        except Exception as erro:
            processo_automacao = None
            agendamento_em_execucao = None

            logger.exception(
                "Não foi possível iniciar a automação %s.",
                agendamento.nome,
            )

            registrar_erro(
                f"Iniciar automação {agendamento.nome}",
                erro,
            )


# ============================================================
# AGENDADOR
# ============================================================

def registrar_tarefas() -> None:
    """Registra T1, T2 e T3."""
    schedule.clear()

    for agendamento in AGENDAMENTOS:
        schedule.every().day.at(
            agendamento.horario_execucao
        ).do(
            job,
            agendamento,
        )

        logger.info(
            "Tarefa registrada: %s às %s | filtro %s até %s",
            agendamento.nome,
            agendamento.horario_execucao,
            agendamento.horario_inicial_pentaho,
            agendamento.horario_final_pentaho,
        )


def mostrar_proximas_execucoes() -> None:
    """Exibe todas as próximas execuções."""
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
    """Localiza um turno pelo nome."""
    nome_normalizado = nome.strip().upper()

    for agendamento in AGENDAMENTOS:
        if agendamento.nome.upper() == nome_normalizado:
            return agendamento

    nomes = ", ".join(
        agendamento.nome
        for agendamento in AGENDAMENTOS
    )

    raise ValueError(
        f"Agendamento {nome!r} não existe. Opções: {nomes}"
    )


def main() -> int:
    """Inicializa e mantém o agendador ativo."""
    try:
        validar_agendamentos()
        validar_estrutura_projeto()
        registrar_tarefas()

        logger.info(
            "Agendador iniciado."
        )

        logger.info(
            "Modo: %s",
            "EXECUTÁVEL" if EXECUTANDO_COMO_EXE else "CÓDIGO-FONTE",
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

            except Exception as erro:
                logger.exception(
                    "Erro ao verificar ou executar tarefas."
                )

                registrar_erro(
                    "schedule.run_pending",
                    erro,
                )

            time.sleep(
                INTERVALO_VERIFICACAO
            )

    except KeyboardInterrupt:
        logger.info(
            "Agendador encerrado pelo usuário."
        )
        return 130

    except Exception as erro:
        logger.exception(
            "Não foi possível iniciar o agendador."
        )

        registrar_erro(
            "Inicialização do agendador",
            erro,
        )

        return 1


if __name__ == "__main__":
    raise SystemExit(
        main()
    )