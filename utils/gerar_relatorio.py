import logging
import os
import subprocess
import sys
import threading
import time
import tkinter as tk
import traceback
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from tkinter import messagebox
import pyautogui as pg

from selenium import webdriver
from selenium.common.exceptions import (
    InvalidSessionIdException,
    JavascriptException,
    NoSuchElementException,
    NoSuchFrameException,
    NoSuchWindowException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys


# ============================================================
# CONFIGURAÇÕES
# ============================================================

URL = "http://operationsreports.superfrio.com.br:8080/pentaho/Home"

# ============================================================
# NAVEGADOR DA AUTOMAÇÃO
# ============================================================

# Opções aceitas:
#   "CHROME"
#   "EDGE"
#
# Também pode ser definido no Windows pela variável:
#   PENTAHO_NAVEGADOR=CHROME
#   PENTAHO_NAVEGADOR=EDGE
NAVEGADOR = os.getenv(
    "PENTAHO_NAVEGADOR",
    "EDGE",
).strip().upper()

NAVEGADORES_SUPORTADOS = (
    "CHROME",
    "EDGE",
)

USUARIO = os.getenv("PENTAHO_USUARIO", "JOAO.PEREIRA").strip()
SENHA = os.getenv("PENTAHO_SENHA", "jPereira!@#")


def ler_bool_ambiente(
    nome: str,
    padrao: bool,
) -> bool:
    """Lê uma variável booleana do ambiente com segurança."""
    valor = os.getenv(nome)

    if valor is None:
        return padrao

    valor_normalizado = valor.strip().lower()

    if valor_normalizado in {
        "1",
        "true",
        "sim",
        "yes",
        "on",
    }:
        return True

    if valor_normalizado in {
        "0",
        "false",
        "nao",
        "não",
        "no",
        "off",
    }:
        return False

    return padrao


def ler_float_ambiente(
    nome: str,
    padrao: float,
    *,
    minimo: float | None = None,
) -> float:
    """Lê uma variável numérica sem derrubar a automação."""
    valor = os.getenv(nome)

    try:
        resultado = (
            float(valor)
            if valor is not None
            else float(padrao)
        )
    except (TypeError, ValueError):
        resultado = float(padrao)

    if minimo is not None:
        resultado = max(
            minimo,
            resultado,
        )

    return resultado


# True: as esperas críticas não possuem prazo máximo. O processo
# continua aguardando enquanto a página, o computador ou a rede
# ainda estiverem respondendo lentamente.
#
# False: utiliza PENTAHO_TIMEOUT_SEGUNDOS como limite por espera.
ESPERA_INDEFINIDA: bool = ler_bool_ambiente(
    "PENTAHO_ESPERA_INDEFINIDA",
    True,
)

TIMEOUT_CONFIGURADO = ler_float_ambiente(
    "PENTAHO_TIMEOUT_SEGUNDOS",
    600.0,
    minimo=0.0,
)

# Compatibilidade: o restante do arquivo continua usando TIMEOUT.
# None significa espera sem limite para as funções adaptativas.
TIMEOUT: float | None = (
    None
    if ESPERA_INDEFINIDA or TIMEOUT_CONFIGURADO <= 0
    else TIMEOUT_CONFIGURADO
)

INTERVALO_VERIFICACAO = ler_float_ambiente(
    "PENTAHO_INTERVALO_VERIFICACAO",
    0.50,
    minimo=0.10,
)

PAUSA_GLOBAL = ler_float_ambiente(
    "PENTAHO_PAUSA_GLOBAL",
    3.0,
    minimo=0.0,
)

# Durante esperas longas, atualiza a janela e o log periodicamente
# para mostrar que a automação não travou.
INTERVALO_AVISO_ESPERA = ler_float_ambiente(
    "PENTAHO_INTERVALO_AVISO_ESPERA",
    15.0,
    minimo=3.0,
)

MAX_PROFUNDIDADE_IFRAMES = int(
    ler_float_ambiente(
        "PENTAHO_MAX_PROFUNDIDADE_IFRAMES",
        15,
        minimo=1,
    )
)

TENTATIVAS_CRIAR_DRIVER = int(
    ler_float_ambiente(
        "PENTAHO_TENTATIVAS_CRIAR_DRIVER",
        3,
        minimo=1,
    )
)

INTERVALO_TENTATIVAS_DRIVER = ler_float_ambiente(
    "PENTAHO_INTERVALO_TENTATIVAS_DRIVER",
    8.0,
    minimo=1.0,
)

# Esperas curtas são usadas apenas para decidir um fallback, nunca
# para encerrar o processo principal.
TIMEOUT_FALLBACK_CURTO = ler_float_ambiente(
    "PENTAHO_TIMEOUT_FALLBACK_CURTO",
    8.0,
    minimo=1.0,
)

CONTAGEM_INICIAL = 5

CAMINHO_PUBLIC = "/public"
CAMINHO_DASHBOARDS = "/public/dashboards"
CAMINHO_GESTAO = "/public/dashboards/gestao-operacional"
ARQUIVO_DESTINO = "acompanhamento_separacao_v01.wcdf"
ABRIR_OUTRA_GUIA_ARQUIVO_DESTINO = "Open in a new window"

# ============================================================
# CONFIGURAÇÕES DO DASHBOARD
# ============================================================

UNIDADE_DESTINO = "CWBII"
CLIENTE_PARA_REMOVER = "MDLZ-MP"
INTERVALO_ATUALIZACAO = "05 Minutos"
DATA_BASE = "Agendamento"
INCLUIR_BACKLOG = "SIM"


def montar_data_hora_padrao(horario: str) -> str:
    """Monta DD/MM/AAAA HH:MM:SS usando a data atual."""
    data_atual = datetime.now().strftime("%d/%m/%Y")
    return f"{data_atual} {horario}"


NOME_AGENDAMENTO = os.getenv(
    "PENTAHO_NOME_AGENDAMENTO",
    "EXECUCAO_MANUAL",
).strip()

HORA_INICIAL = os.getenv(
    "PENTAHO_HORA_INICIAL",
    montar_data_hora_padrao("05:00:00"),
).strip()

HORA_FINAL = os.getenv(
    "PENTAHO_HORA_FINAL",
    montar_data_hora_padrao("13:45:00"),
).strip()

BOTAO_APLICAR_FILTRO = "Aplicar Filtro (Todos)"


# ============================================================
# ARQUIVOS, ERROS E PREPARAÇÃO DO NAVEGADOR
# ============================================================

EXECUTANDO_COMO_EXE = bool(
    getattr(sys, "frozen", False)
)


def obter_pasta_projeto() -> Path:
    """
    Retorna a raiz do projeto.

    Em desenvolvimento, gerar_relatorio.py fica em utils/,
    portanto a raiz é a pasta pai de utils.

    No executável, a raiz é a pasta onde está o próprio EXE.
    """
    if EXECUTANDO_COMO_EXE:
        return Path(sys.executable).resolve().parent

    pasta_arquivo = Path(__file__).resolve().parent

    if pasta_arquivo.name.lower() == "utils":
        return pasta_arquivo.parent

    return pasta_arquivo


PASTA_PROJETO = obter_pasta_projeto()
ARQUIVO_ERRO = PASTA_PROJETO / "erro.txt"
PASTA_DIAGNOSTICOS = PASTA_PROJETO / "diagnosticos"

# True:
#   encerra todas as janelas/processos do Chrome e Edge antes
#   de iniciar a automação e aguarda 10 segundos.
#
# False:
#   não fecha nenhum navegador existente e abre uma nova janela
#   do navegador selecionado em NAVEGADOR.
FECHAR_TELAS: bool = True

# Tenta impedir a exibição da faixa:
# "O Chrome está sendo controlado por um software de teste automatizado".
OCULTAR_AVISO_AUTOMACAO: bool = True

# Ao concluir o processo, coloca o relatório em tela cheia.
ATIVAR_F11_NO_FINAL: bool = True

TEMPO_ESPERA_APOS_FECHAR_NAVEGADORES = 10
TENTATIVAS_FECHAR_NAVEGADORES = 3
INTERVALO_TENTATIVAS_NAVEGADORES = 1.0

PROCESSOS_NAVEGADORES = (
    "chrome.exe",
    "msedge.exe",
    "chromedriver.exe",
    "msedgedriver.exe",
)


# ============================================================
# ESTADO GLOBAL
# ============================================================

driver = None
processo_em_execucao = False

# Permite interromper laços de espera adaptativos de forma segura.
EVENTO_CANCELAMENTO = threading.Event()


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)

logger = logging.getLogger("pentaho-automation")


# ============================================================
# IDENTIFICAÇÃO E VALIDAÇÃO DO NAVEGADOR
# ============================================================

def normalizar_navegador(
    valor: str,
) -> str:
    """
    Normaliza o nome do navegador.

    Valores aceitos:
        CHROME
        GOOGLE CHROME
        CHROMIUM
        EDGE
        MICROSOFT EDGE
        MSEDGE
    """
    valor_normalizado = (
        str(valor or "")
        .strip()
        .upper()
        .replace("_", " ")
        .replace("-", " ")
    )

    aliases = {
        "CHROME": "CHROME",
        "GOOGLE CHROME": "CHROME",
        "CHROMIUM": "CHROME",
        "EDGE": "EDGE",
        "MICROSOFT EDGE": "EDGE",
        "MSEDGE": "EDGE",
        "MS EDGE": "EDGE",
    }

    navegador = aliases.get(
        valor_normalizado
    )

    if navegador is None:
        raise ValueError(
            "Navegador inválido. Use CHROME ou EDGE. "
            f"Valor recebido: {valor!r}"
        )

    return navegador


def navegador_selecionado() -> str:
    """Retorna CHROME ou EDGE após validar a configuração."""
    return normalizar_navegador(
        NAVEGADOR
    )


def nome_navegador_exibicao() -> str:
    """Retorna o nome amigável do navegador selecionado."""
    return (
        "Google Chrome"
        if navegador_selecionado() == "CHROME"
        else "Microsoft Edge"
    )



# ============================================================
# REGISTRO DE ERROS
# ============================================================

_erro_lock = threading.Lock()


def registrar_erro_txt(
    *,
    etapa: str,
    erro: BaseException | str,
    traceback_texto: str | None = None,
) -> None:
    """
    Acrescenta uma ocorrência ao arquivo erro.txt.

    O arquivo fica na raiz do projeto, ao lado do executável
    ou do LoopAtualizar.py.
    """
    momento = datetime.now().strftime(
        "%d/%m/%Y %H:%M:%S"
    )

    if isinstance(erro, BaseException):
        nome_erro = type(erro).__name__
        mensagem_erro = str(erro)
    else:
        nome_erro = "Erro"
        mensagem_erro = str(erro)

    if traceback_texto is None:
        traceback_texto = traceback.format_exc()

        if traceback_texto.strip() == "NoneType: None":
            traceback_texto = ""

    bloco = (
        "\n"
        + "=" * 80
        + "\n"
        + f"DATA/HORA: {momento}\n"
        + f"AGENDAMENTO: {NOME_AGENDAMENTO}\n"
        + f"ETAPA: {etapa}\n"
        + f"NAVEGADOR: {NAVEGADOR}\n"
        + f"TIPO: {nome_erro}\n"
        + f"MENSAGEM: {mensagem_erro}\n"
        + f"HORA INICIAL: {HORA_INICIAL}\n"
        + f"HORA FINAL: {HORA_FINAL}\n"
    )

    if traceback_texto:
        bloco += (
            "\nTRACEBACK:\n"
            + traceback_texto.rstrip()
            + "\n"
        )

    bloco += "=" * 80 + "\n"

    try:
        with _erro_lock:
            ARQUIVO_ERRO.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            with ARQUIVO_ERRO.open(
                mode="a",
                encoding="utf-8",
                newline="",
            ) as arquivo:
                arquivo.write(bloco)
                arquivo.flush()

                try:
                    os.fsync(
                        arquivo.fileno()
                    )
                except OSError:
                    pass

        logger.error(
            "Erro registrado em: %s",
            ARQUIVO_ERRO,
        )

    except OSError:
        logger.exception(
            "Não foi possível gravar o arquivo de erros em %s.",
            ARQUIVO_ERRO,
        )


def registrar_excecao_nao_tratada(
    tipo_excecao,
    excecao,
    traceback_objeto,
) -> None:
    """Registra falhas não capturadas na thread principal."""
    texto = "".join(
        traceback.format_exception(
            tipo_excecao,
            excecao,
            traceback_objeto,
        )
    )

    registrar_erro_txt(
        etapa="Exceção não tratada na thread principal",
        erro=excecao,
        traceback_texto=texto,
    )

    sys.__excepthook__(
        tipo_excecao,
        excecao,
        traceback_objeto,
    )


def registrar_excecao_thread(
    argumentos,
) -> None:
    """Registra falhas não capturadas em qualquer thread."""
    texto = "".join(
        traceback.format_exception(
            argumentos.exc_type,
            argumentos.exc_value,
            argumentos.exc_traceback,
        )
    )

    registrar_erro_txt(
        etapa=(
            "Exceção não tratada na thread "
            f"{argumentos.thread.name}"
        ),
        erro=argumentos.exc_value,
        traceback_texto=texto,
    )


sys.excepthook = registrar_excecao_nao_tratada
threading.excepthook = registrar_excecao_thread


# ============================================================
# FECHAMENTO DO CHROME E EDGE
# ============================================================

def processo_windows_esta_ativo(
    nome_processo: str,
) -> bool:
    """Verifica pelo tasklist se um processo está ativo."""
    if os.name != "nt":
        return False

    try:
        resultado = subprocess.run(
            [
                "tasklist",
                "/FI",
                f"IMAGENAME eq {nome_processo}",
                "/FO",
                "CSV",
                "/NH",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            creationflags=getattr(
                subprocess,
                "CREATE_NO_WINDOW",
                0,
            ),
        )

        saida = (
            resultado.stdout or ""
        ).lower()

        return nome_processo.lower() in saida

    except (
        OSError,
        subprocess.SubprocessError,
    ) as erro:
        registrar_erro_txt(
            etapa=f"Verificar processo {nome_processo}",
            erro=erro,
        )
        return False


def encerrar_processo_windows(
    nome_processo: str,
) -> bool:
    """
    Encerra um processo e seus filhos usando taskkill.

    Retorna True quando o processo não existe mais.
    """
    if os.name != "nt":
        logger.warning(
            "O fechamento automático de navegadores "
            "está disponível apenas no Windows."
        )
        return True

    if not processo_windows_esta_ativo(
        nome_processo
    ):
        logger.info(
            "Processo já estava fechado: %s",
            nome_processo,
        )
        return True

    try:
        resultado = subprocess.run(
            [
                "taskkill",
                "/F",
                "/T",
                "/IM",
                nome_processo,
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            creationflags=getattr(
                subprocess,
                "CREATE_NO_WINDOW",
                0,
            ),
        )

        if resultado.returncode == 0:
            logger.info(
                "Processo encerrado: %s",
                nome_processo,
            )
        else:
            logger.warning(
                "taskkill retornou código %s para %s. Saída: %s",
                resultado.returncode,
                nome_processo,
                (
                    resultado.stderr
                    or resultado.stdout
                    or ""
                ).strip(),
            )

    except (
        OSError,
        subprocess.SubprocessError,
    ) as erro:
        registrar_erro_txt(
            etapa=f"Encerrar processo {nome_processo}",
            erro=erro,
        )
        return False

    time.sleep(
        0.5
    )

    return not processo_windows_esta_ativo(
        nome_processo
    )


def fechar_chrome_e_edge() -> None:
    """
    Fecha todas as janelas e processos do Chrome e do Edge.

    Isso fecha todas as abas abertas pelo usuário nos dois
    navegadores, inclusive sessões que não pertencem à automação.
    """
    atualizar_status(
        "Fechando todas as abas do Chrome e Edge..."
    )

    logger.info(
        "Iniciando fechamento de Chrome e Edge."
    )

    pendentes = set(
        PROCESSOS_NAVEGADORES
    )

    for tentativa in range(
        1,
        TENTATIVAS_FECHAR_NAVEGADORES + 1,
    ):
        logger.info(
            "Tentativa %d/%d de fechar navegadores.",
            tentativa,
            TENTATIVAS_FECHAR_NAVEGADORES,
        )

        for nome_processo in tuple(
            pendentes
        ):
            if encerrar_processo_windows(
                nome_processo
            ):
                pendentes.discard(
                    nome_processo
                )

        if not pendentes:
            break

        time.sleep(
            INTERVALO_TENTATIVAS_NAVEGADORES
        )

    # Edge Startup Boost pode recriar processos em segundo plano.
    time.sleep(
        1.0
    )

    for nome_processo in PROCESSOS_NAVEGADORES:
        if processo_windows_esta_ativo(
            nome_processo
        ):
            encerrar_processo_windows(
                nome_processo
            )

    ainda_ativos = [
        nome
        for nome in PROCESSOS_NAVEGADORES
        if processo_windows_esta_ativo(nome)
    ]

    if ainda_ativos:
        mensagem = (
            "Não foi possível confirmar o fechamento de: "
            + ", ".join(ainda_ativos)
        )

        logger.warning(
            mensagem
        )

        registrar_erro_txt(
            etapa="Fechamento dos navegadores",
            erro=mensagem,
            traceback_texto="",
        )
    else:
        logger.info(
            "Chrome e Edge foram fechados."
        )


def aguardar_antes_de_iniciar(
    segundos: int = TEMPO_ESPERA_APOS_FECHAR_NAVEGADORES,
) -> None:
    """Aguarda com atualização visual segundo a segundo."""
    for restante in range(
        segundos,
        0,
        -1,
    ):
        atualizar_status(
            "Chrome e Edge fechados. "
            f"Iniciando o processo em {restante}s..."
        )

        time.sleep(
            1
        )


def preparar_navegadores() -> None:
    """
    Prepara o ambiente do navegador conforme FECHAR_TELAS.

    FECHAR_TELAS=True:
        Fecha Chrome, Edge e drivers antigos.
        Aguarda TEMPO_ESPERA_APOS_FECHAR_NAVEGADORES segundos.

    FECHAR_TELAS=False:
        Preserva todos os navegadores já abertos.
        O Selenium abrirá uma nova janela do navegador escolhido.
    """
    if not FECHAR_TELAS:
        logger.info(
            "FECHAR_TELAS=False: Chrome e Edge existentes "
            "serão preservados."
        )

        atualizar_status(
            "Navegadores existentes serão mantidos. "
            f"Abrindo uma nova janela do {nome_navegador_exibicao()}..."
        )

        return

    logger.info(
        "FECHAR_TELAS=True: Chrome e Edge serão encerrados."
    )

    fechar_chrome_e_edge()

    aguardar_antes_de_iniciar(
        TEMPO_ESPERA_APOS_FECHAR_NAVEGADORES
    )



# ============================================================
# CONFIGURAÇÃO DO CHROME E TELA CHEIA
# ============================================================

def criar_opcoes_navegador():
    """
    Cria opções estáveis para Chrome ou Edge em computadores lentos.

    pageLoadStrategy=eager evita que driver.get() fique bloqueado
    esperando imagens e recursos secundários. As funções adaptativas
    continuam aguardando os elementos reais do Pentaho.
    """
    navegador = navegador_selecionado()

    if navegador == "CHROME":
        opcoes = webdriver.ChromeOptions()
    else:
        opcoes = webdriver.EdgeOptions()

    opcoes.page_load_strategy = "eager"

    opcoes.add_argument(
        "--start-maximized"
    )

    opcoes.add_argument(
        "--disable-background-timer-throttling"
    )

    opcoes.add_argument(
        "--disable-backgrounding-occluded-windows"
    )

    opcoes.add_argument(
        "--disable-renderer-backgrounding"
    )

    opcoes.add_argument(
        "--disable-features=CalculateNativeWinOcclusion"
    )

    opcoes.add_experimental_option(
        "detach",
        True,
    )

    if OCULTAR_AVISO_AUTOMACAO:
        opcoes.add_argument(
            "--disable-infobars"
        )

        opcoes.add_argument(
            "--disable-blink-features=AutomationControlled"
        )

        opcoes.add_experimental_option(
            "excludeSwitches",
            [
                "enable-automation",
                "enable-logging",
            ],
        )

        opcoes.add_experimental_option(
            "useAutomationExtension",
            False,
        )

    return opcoes

def criar_driver_navegador():
    """
    Cria o WebDriver com tentativas automáticas.

    Isso cobre inicialização lenta do navegador, Selenium Manager,
    antivírus e máquinas com pouco recurso disponível.
    """
    navegador = navegador_selecionado()
    ultimo_erro: BaseException | None = None

    for tentativa in range(
        1,
        TENTATIVAS_CRIAR_DRIVER + 1,
    ):
        atualizar_status(
            f"Abrindo o {nome_navegador_exibicao()} "
            f"(tentativa {tentativa}/{TENTATIVAS_CRIAR_DRIVER})..."
        )

        try:
            opcoes = criar_opcoes_navegador()

            if navegador == "CHROME":
                navegador_driver = webdriver.Chrome(
                    options=opcoes
                )
            else:
                navegador_driver = webdriver.Edge(
                    options=opcoes
                )

            # Não combinar implicit wait com nossas esperas explícitas.
            navegador_driver.implicitly_wait(0)

            logger.info(
                "%s iniciado na tentativa %d.",
                nome_navegador_exibicao(),
                tentativa,
            )

            return navegador_driver

        except Exception as erro:
            ultimo_erro = erro
            logger.exception(
                "Falha ao iniciar %s na tentativa %d/%d.",
                nome_navegador_exibicao(),
                tentativa,
                TENTATIVAS_CRIAR_DRIVER,
            )

            if tentativa < TENTATIVAS_CRIAR_DRIVER:
                atualizar_status(
                    "O navegador ainda não iniciou. "
                    f"Nova tentativa em {INTERVALO_TENTATIVAS_DRIVER:.0f}s..."
                )
                pausa_responsiva(
                    INTERVALO_TENTATIVAS_DRIVER
                )

    raise WebDriverException(
        f"Não foi possível iniciar {nome_navegador_exibicao()} "
        f"após {TENTATIVAS_CRIAR_DRIVER} tentativas. "
        f"Último erro: {ultimo_erro}"
    )

def criar_opcoes_chrome():
    """
    Alias mantido por compatibilidade com versões anteriores.

    Novos trechos devem usar criar_opcoes_navegador().
    """
    return criar_opcoes_navegador()


def aplicar_ajustes_apos_criar_driver() -> None:
    """
    Aplica ajustes Chromium após criar o WebDriver.

    ChromeDriver e EdgeDriver oferecem execute_cdp_cmd(), pois
    ambos controlam navegadores baseados em Chromium.
    """
    if not OCULTAR_AVISO_AUTOMACAO:
        return

    try:
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {
                "source": (
                    "Object.defineProperty("
                    "navigator, 'webdriver', "
                    "{get: () => undefined}"
                    ");"
                )
            },
        )

        logger.info(
            "Ajustes de automação aplicados no %s.",
            nome_navegador_exibicao(),
        )

    except Exception as erro:
        logger.warning(
            "Não foi possível aplicar todos os ajustes "
            "no %s: %s",
            nome_navegador_exibicao(),
            erro,
        )


def focar_ultima_janela_navegador() -> None:
    """Muda o Selenium para a última guia/janela disponível."""
    if driver is None:
        return

    janelas = driver.window_handles

    if not janelas:
        raise WebDriverException(
            "Nenhuma janela do navegador está disponível."
        )

    driver.switch_to.window(
        janelas[-1]
    )

    try:
        driver.execute_script(
            "window.focus();"
        )
    except WebDriverException:
        pass


def focar_ultima_janela_chrome() -> None:
    """
    Alias mantido para não quebrar chamadas antigas.
    """
    focar_ultima_janela_navegador()


def ativar_tela_cheia_final() -> None:
    """
    Coloca o relatório em tela cheia ao final do processo.

    1. Foca a última janela do Chrome.
    2. Usa fullscreen_window(), equivalente ao modo F11.
    3. Caso o comando WebDriver falhe, usa PyAutoGUI para
       pressionar F11 como fallback.
    """
    if not ATIVAR_F11_NO_FINAL:
        logger.info(
            "ATIVAR_F11_NO_FINAL=False: tela cheia desativada."
        )
        return

    atualizar_status(
        "Ativando tela cheia..."
    )

    try:
        focar_ultima_janela_navegador()

        # Comando WebDriver equivalente ao F11.
        driver.fullscreen_window()

        time.sleep(
            1.0
        )

        logger.info(
            "Tela cheia ativada pelo WebDriver."
        )

        return

    except Exception as erro:
        logger.warning(
            "fullscreen_window() falhou; "
            "tentando pressionar F11: %s",
            erro,
        )

    try:
        focar_ultima_janela_navegador()

        time.sleep(
            0.8
        )

        # Fallback literal solicitado.
        pg.press(
            "f11"
        )

        time.sleep(
            1.0
        )

        logger.info(
            "F11 pressionado com PyAutoGUI."
        )

    except Exception as erro:
        registrar_erro_txt(
            etapa="Ativar tela cheia no final",
            erro=erro,
        )

        logger.exception(
            "Não foi possível ativar a tela cheia."
        )


# ============================================================
# INTERFACE
# ============================================================

def atualizar_status(texto: str) -> None:
    """Atualiza o status sem falhar quando a janela já foi fechada."""
    logger.info(texto)

    try:
        if janela.winfo_exists():
            janela.after(
                0,
                lambda texto=texto: status_variavel.set(texto),
            )
    except (tk.TclError, RuntimeError):
        pass

def exibir_erro(titulo: str, mensagem: str) -> None:
    """Exibe uma mensagem de erro pela thread principal do Tkinter."""
    try:
        if janela.winfo_exists():
            janela.after(
                0,
                lambda titulo=titulo, mensagem=mensagem: messagebox.showerror(
                    titulo,
                    mensagem,
                ),
            )
    except (tk.TclError, RuntimeError):
        pass

def concluir_interface() -> None:
    """
    Mostra a conclusão e fecha a pequena janela automaticamente.

    O F11 agora é acionado pela função ativar_tela_cheia_final()
    ainda na thread do Selenium, garantindo que o Chrome seja
    o alvo do comando.
    """
    if not janela.winfo_exists():
        return

    status_variavel.set(
        "Processo concluído com sucesso."
    )

    contador_variavel.set(
        "Concluído"
    )

    janela.after(
        2500,
        janela.destroy,
    )


# ============================================================
# DIAGNÓSTICO
# ============================================================

def salvar_diagnostico(nome: str = "erro") -> None:
    """Salva screenshot e HTML quando ocorre um erro."""
    if driver is None:
        return

    try:
        pasta = PASTA_DIAGNOSTICOS
        pasta.mkdir(parents=True, exist_ok=True)

        timestamp = time.strftime("%Y%m%d_%H%M%S")
        screenshot = pasta / f"{nome}_{timestamp}.png"
        html = pasta / f"{nome}_{timestamp}.html"

        driver.save_screenshot(str(screenshot))
        html.write_text(driver.page_source, encoding="utf-8")

        logger.info("Diagnóstico salvo em: %s", pasta.resolve())

    except Exception as erro:
        logger.exception("Não foi possível salvar o diagnóstico.")

        registrar_erro_txt(
            etapa="Salvar diagnóstico",
            erro=erro,
        )


# ============================================================
# ESPERAS
# ============================================================

def normalizar_timeout(
    timeout: float | int | None,
) -> float | None:
    """
    Normaliza o prazo de uma espera.

    None, zero ou valor negativo significam espera sem limite.
    """
    if timeout is None:
        return None

    try:
        valor = float(timeout)
    except (TypeError, ValueError):
        return None

    return valor if valor > 0 else None


def aguardar_condicao(
    condicao: Callable[[], object],
    *,
    descricao: str,
    timeout: float | int | None = TIMEOUT,
    intervalo: float | None = None,
    atualizar_status_periodicamente: bool = True,
):
    """
    Espera adaptativa utilizada em toda a automação.

    Diferentemente de WebDriverWait com prazo rígido, esta função
    pode aguardar indefinidamente. Enquanto espera, mantém um
    heartbeat no log e na janela, o que diferencia lentidão de
    travamento real.
    """
    prazo = normalizar_timeout(timeout)
    intervalo_real = max(
        0.05,
        float(
            INTERVALO_VERIFICACAO
            if intervalo is None
            else intervalo
        ),
    )

    inicio = time.monotonic()
    proximo_aviso = inicio + INTERVALO_AVISO_ESPERA
    ultima_excecao: BaseException | None = None

    while True:
        if EVENTO_CANCELAMENTO.is_set():
            raise InterruptedError(
                f"Espera cancelada: {descricao}"
            )

        try:
            resultado = condicao()

            if resultado:
                decorrido = time.monotonic() - inicio
                logger.info(
                    "%s disponível após %.1fs.",
                    descricao,
                    decorrido,
                )
                return resultado

        except (
            InvalidSessionIdException,
            NoSuchWindowException,
        ):
            # Sessão encerrada ou janela fechada não é lentidão.
            # Nesses casos, continuar esperando ocultaria o erro real.
            raise

        except (
            JavascriptException,
            NoSuchElementException,
            NoSuchFrameException,
            StaleElementReferenceException,
            WebDriverException,
        ) as erro:
            # Erros transitórios são comuns enquanto o Pentaho
            # recria o DOM. Eles não encerram a espera.
            ultima_excecao = erro

        agora = time.monotonic()
        decorrido = agora - inicio

        if prazo is not None and decorrido >= prazo:
            complemento = (
                f" Último erro transitório: {ultima_excecao}"
                if ultima_excecao is not None
                else ""
            )

            raise TimeoutException(
                f"Não foi possível concluir: {descricao}. "
                f"Tempo aguardado: {decorrido:.1f}s."
                + complemento
            )

        if (
            atualizar_status_periodicamente
            and agora >= proximo_aviso
        ):
            mensagem = (
                f"Ainda aguardando {descricao} "
                f"({decorrido:.0f}s)..."
            )
            atualizar_status(mensagem)
            proximo_aviso = agora + INTERVALO_AVISO_ESPERA

        EVENTO_CANCELAMENTO.wait(
            intervalo_real
        )


def pausa_responsiva(segundos: float) -> None:
    """Pausa que pode ser interrompida pelo evento de cancelamento."""
    if segundos <= 0:
        return

    if EVENTO_CANCELAMENTO.wait(segundos):
        raise InterruptedError(
            "Automação cancelada durante uma pausa."
        )


def pausa_adicional(descricao: str) -> None:
    """
    Aplica apenas uma margem curta depois de grandes etapas.

    A sincronização principal não depende desta pausa; ela depende
    das condições reais da página.
    """
    if PAUSA_GLOBAL <= 0:
        return

    atualizar_status(
        f"{descricao} concluído. "
        f"Aguardando estabilização por {PAUSA_GLOBAL:.1f}s..."
    )
    pausa_responsiva(PAUSA_GLOBAL)

def aguardar_documento_pronto(
    timeout: float | int | None = TIMEOUT,
) -> None:
    """
    Aguarda o documento ficar pronto sem prazo rígido por padrão.

    O modo pageLoadStrategy=eager permite que driver.get() retorne
    cedo; esta função então aguarda o estado real do DOM.
    """
    def documento_pronto():
        if driver is None:
            return False

        estado = driver.execute_script(
            "return document.readyState"
        )

        return estado == "complete"

    aguardar_condicao(
        documento_pronto,
        descricao="o documento terminar de carregar",
        timeout=timeout,
    )

def elemento_esta_disponivel(elemento, clicavel: bool) -> bool:
    """Verifica se um elemento está visível e utilizável."""
    try:
        if not elemento.is_displayed():
            return False

        if clicavel and not elemento.is_enabled():
            return False

        return True

    except (
        StaleElementReferenceException,
        WebDriverException,
    ):
        return False


# ============================================================
# BUSCA NA PÁGINA E NOS IFRAMES
# ============================================================

def procurar_no_contexto_atual(
    localizadores: list[tuple[str, str]],
    clicavel: bool,
):
    """Procura no documento ou iframe atual."""
    for tipo, seletor in localizadores:
        try:
            elementos = driver.find_elements(tipo, seletor)
        except WebDriverException:
            continue

        for elemento in elementos:
            if elemento_esta_disponivel(elemento, clicavel):
                return elemento

    return None


def procurar_recursivamente_nos_frames(
    localizadores: list[tuple[str, str]],
    clicavel: bool,
    profundidade: int = 0,
):
    """
    Procura na página e dentro de frames/iframes.

    Quando encontra, o driver permanece no frame correto.
    """
    if profundidade > MAX_PROFUNDIDADE_IFRAMES:
        return None

    elemento = procurar_no_contexto_atual(
        localizadores,
        clicavel,
    )

    if elemento is not None:
        return elemento

    try:
        frames = driver.find_elements(
            By.CSS_SELECTOR,
            "iframe, frame",
        )
    except WebDriverException:
        return None

    for frame in frames:
        entrou_no_frame = False

        try:
            driver.switch_to.frame(frame)
            entrou_no_frame = True

            elemento = procurar_recursivamente_nos_frames(
                localizadores,
                clicavel,
                profundidade + 1,
            )

            if elemento is not None:
                return elemento

        except (
            NoSuchFrameException,
            StaleElementReferenceException,
            WebDriverException,
        ):
            pass

        if entrou_no_frame:
            try:
                driver.switch_to.parent_frame()
            except WebDriverException:
                driver.switch_to.default_content()

    return None


def esperar_elemento(
    localizadores: list[tuple[str, str]],
    *,
    clicavel: bool = True,
    timeout: float | int | None = TIMEOUT,
    descricao: str = "elemento",
):
    """
    Aguarda um elemento na página ou em qualquer iframe.

    Por padrão não existe prazo máximo. Um prazo explícito ainda
    pode ser informado para decisões de fallback.
    """
    def procurar():
        if driver is None:
            return False

        try:
            driver.switch_to.default_content()
        except WebDriverException:
            return False

        return procurar_recursivamente_nos_frames(
            localizadores,
            clicavel,
        ) or False

    elemento = aguardar_condicao(
        procurar,
        descricao=descricao,
        timeout=timeout,
    )

    logger.info(
        "Elemento encontrado: %s",
        descricao,
    )

    return elemento

# ============================================================
# CLIQUES
# ============================================================

def rolar_ate_elemento(elemento) -> None:
    """
    Centraliza o elemento na área visível.

    StaleElementReferenceException é propagada. Um elemento stale
    não pode ser recuperado; a função chamadora precisa localizá-lo
    novamente no DOM.
    """
    driver.execute_script(
        """
        arguments[0].scrollIntoView({
            block: "center",
            inline: "center",
            behavior: "instant"
        });
        """,
        elemento,
    )

    pausa_responsiva(
        0.4
    )


def clicar(elemento) -> None:
    """
    Tenta clique normal, ActionChains e JavaScript.

    Quando o elemento fica stale, a exceção é propagada
    imediatamente. Reutilizar o mesmo WebElement em outro método
    não funciona porque ele já não pertence ao DOM atual.
    """
    rolar_ate_elemento(
        elemento
    )

    ultimo_erro: BaseException | None = None

    try:
        elemento.click()
        return

    except StaleElementReferenceException:
        raise

    except WebDriverException as erro:
        ultimo_erro = erro

    try:
        ActionChains(
            driver
        ).move_to_element(
            elemento
        ).pause(
            0.3
        ).click().perform()

        return

    except StaleElementReferenceException:
        raise

    except WebDriverException as erro:
        ultimo_erro = erro

    try:
        driver.execute_script(
            """
            const elemento = arguments[0];

            elemento.scrollIntoView({
                block: "center",
                inline: "center",
                behavior: "instant"
            });

            elemento.dispatchEvent(
                new MouseEvent("mousedown", {
                    bubbles: true,
                    cancelable: true,
                    view: window,
                    button: 0,
                    buttons: 1
                })
            );

            elemento.dispatchEvent(
                new MouseEvent("mouseup", {
                    bubbles: true,
                    cancelable: true,
                    view: window,
                    button: 0,
                    buttons: 0
                })
            );

            elemento.click();
            """,
            elemento,
        )

        return

    except StaleElementReferenceException:
        raise

    except WebDriverException as erro:
        ultimo_erro = erro

    raise WebDriverException(
        "Não foi possível clicar no elemento atual. "
        f"Último erro: {ultimo_erro}"
    )


def clicar_duas_vezes(elemento) -> None:
    """Executa duplo clique com fallback JavaScript."""
    rolar_ate_elemento(elemento)

    try:
        ActionChains(driver).move_to_element(
            elemento
        ).pause(0.3).double_click().perform()
        return
    except (
        StaleElementReferenceException,
        WebDriverException,
    ):
        pass

    driver.execute_script(
        """
        arguments[0].dispatchEvent(
            new MouseEvent("dblclick", {
                bubbles: true,
                cancelable: true,
                view: window,
                detail: 2
            })
        );
        """,
        elemento,
    )


# ============================================================
# LOGIN
# ============================================================

def realizar_login() -> None:
    """
    Aguarda até identificar uma destas situações:

    1. a tela de login apareceu; ou
    2. a sessão já está autenticada e o Browse Files apareceu.

    Não existe o antigo limite de 12 segundos, que podia falhar em
    computadores lentos.
    """
    atualizar_status(
        "Aguardando a tela de login ou uma sessão autenticada..."
    )

    localizadores_usuario = [
        (By.ID, "j_username"),
        (By.NAME, "j_username"),
        (By.ID, "username"),
        (By.NAME, "username"),
        (By.CSS_SELECTOR, "input[type='text']"),
    ]

    localizadores_sessao = [
        (
            By.CSS_SELECTOR,
            "[onclick*='browser.perspective']",
        ),
        (
            By.XPATH,
            "//*[normalize-space()='Browse Files']",
        ),
    ]

    def detectar_estado():
        try:
            driver.switch_to.default_content()
        except WebDriverException:
            return False

        campo = procurar_recursivamente_nos_frames(
            localizadores_usuario,
            True,
        )

        if campo is not None:
            return (
                "LOGIN",
                campo,
            )

        try:
            driver.switch_to.default_content()
        except WebDriverException:
            return False

        sessao = procurar_recursivamente_nos_frames(
            localizadores_sessao,
            True,
        )

        if sessao is not None:
            return (
                "AUTENTICADO",
                sessao,
            )

        return False

    estado, campo_usuario = aguardar_condicao(
        detectar_estado,
        descricao="a tela de login ou a sessão do Pentaho",
        timeout=TIMEOUT,
    )

    if estado == "AUTENTICADO":
        atualizar_status(
            "Sessão do Pentaho já está autenticada."
        )
        pausa_adicional(
            "Verificação do login"
        )
        return

    campo_senha = esperar_elemento(
        [
            (By.ID, "j_password"),
            (By.NAME, "j_password"),
            (By.ID, "password"),
            (By.NAME, "password"),
            (By.CSS_SELECTOR, "input[type='password']"),
        ],
        descricao="campo de senha",
    )

    campo_usuario.clear()
    campo_usuario.send_keys(USUARIO)

    campo_senha.clear()
    campo_senha.send_keys(SENHA)

    botao_login = esperar_elemento(
        [
            (By.ID, "loginbtn"),
            (By.NAME, "loginbtn"),
            (By.CSS_SELECTOR, "button[type='submit']"),
            (By.CSS_SELECTOR, "input[type='submit']"),
        ],
        descricao="botão de login",
    )

    atualizar_status(
        "Realizando login..."
    )
    clicar(botao_login)

    try:
        driver.switch_to.default_content()
    except WebDriverException:
        pass

    atualizar_status(
        "Aguardando o Pentaho concluir o login..."
    )

    # Em vez de depender apenas do readyState, espera o recurso
    # que comprova que a sessão autenticada terminou de carregar.
    esperar_elemento(
        localizadores_sessao,
        clicavel=True,
        timeout=TIMEOUT,
        descricao="Browse Files após o login",
    )

    pausa_adicional(
        "Login"
    )

# ============================================================
# BROWSE FILES
# ============================================================

def abrir_browse_por_javascript() -> bool:
    """Executa diretamente mantle_setPerspective."""
    driver.switch_to.default_content()

    try:
        return bool(
            driver.execute_script(
                """
                if (
                    typeof window.mantle_setPerspective === "function"
                ) {
                    window.mantle_setPerspective(
                        "browser.perspective"
                    );
                    return true;
                }

                if (
                    window.parent &&
                    typeof window.parent.mantle_setPerspective
                        === "function"
                ) {
                    window.parent.mantle_setPerspective(
                        "browser.perspective"
                    );
                    return true;
                }

                return false;
                """
            )
        )
    except JavascriptException:
        return False


def abrir_browse_files() -> None:
    """
    Abre a perspectiva Browse Files com espera adaptativa.

    Tenta o botão e o JavaScript repetidamente até um deles funcionar.
    """
    atualizar_status(
        "Aguardando a opção Browse Files..."
    )

    localizadores_botao = [
        (
            By.CSS_SELECTOR,
            "[onclick*='browser.perspective']",
        ),
        (
            By.XPATH,
            "//*[normalize-space()='Browse Files']",
        ),
        (
            By.XPATH,
            "//*[contains(normalize-space(), 'Browse Files')]",
        ),
    ]

    acionado = False

    def tentar_abrir():
        nonlocal acionado

        if acionado:
            return True

        try:
            driver.switch_to.default_content()
        except WebDriverException:
            return False

        botao = procurar_recursivamente_nos_frames(
            localizadores_botao,
            True,
        )

        if botao is not None:
            atualizar_status(
                "Clicando em Browse Files..."
            )
            clicar(botao)
            acionado = True
            return True

        if abrir_browse_por_javascript():
            atualizar_status(
                "Browse Files acionado por JavaScript."
            )
            acionado = True
            return True

        return False

    aguardar_condicao(
        tentar_abrir,
        descricao="a opção Browse Files ficar disponível",
        timeout=TIMEOUT,
    )

    atualizar_status(
        "Aguardando a árvore de pastas..."
    )

    esperar_elemento(
        localizadores_pasta(
            "Public",
            CAMINHO_PUBLIC,
        ),
        clicavel=False,
        timeout=TIMEOUT,
        descricao="pasta Public",
    )

    pausa_adicional(
        "Abertura do Browse Files"
    )

# ============================================================
# ÁRVORE DE PASTAS
# ============================================================

def localizadores_pasta(
    nome: str,
    caminho: str,
) -> list[tuple[str, str]]:
    """Cria seletores para uma pasta do Pentaho."""
    return [
        (
            By.CSS_SELECTOR,
            f"div.folder[path='{caminho}']",
        ),
        (
            By.CSS_SELECTOR,
            f"[path='{caminho}']",
        ),
        (
            By.XPATH,
            (
                "//div["
                "contains("
                "concat(' ', normalize-space(@class), ' '),"
                "' folder '"
                ") "
                f"and @path='{caminho}'"
                "]"
            ),
        ),
        (
            By.XPATH,
            (
                "//div["
                "contains("
                "concat(' ', normalize-space(@class), ' '),"
                "' title '"
                ") "
                f"and normalize-space()='{nome}'"
                "]"
                "/ancestor::div["
                "contains("
                "concat(' ', normalize-space(@class), ' '),"
                "' folder '"
                ")"
                "][1]"
            ),
        ),
    ]


def localizar_titulo_da_pasta(pasta, nome: str):
    """Localiza o título direto da pasta."""
    localizadores_relativos = [
        (
            By.XPATH,
            "./div[contains(@class,'element')]"
            "/div[contains(@class,'title')]",
        ),
        (
            By.CSS_SELECTOR,
            ":scope > .element > .title",
        ),
    ]

    for tipo, seletor in localizadores_relativos:
        try:
            elementos = pasta.find_elements(tipo, seletor)

            for elemento in elementos:
                if (
                    elemento.is_displayed()
                    and elemento.text.strip() == nome
                ):
                    return elemento

        except (
            StaleElementReferenceException,
            WebDriverException,
        ):
            continue

    try:
        return pasta.find_element(
            By.XPATH,
            (
                ".//div["
                "contains(@class,'title') "
                f"and normalize-space()='{nome}'"
                "]"
            ),
        )
    except NoSuchElementException as erro:
        raise TimeoutException(
            f"O título da pasta {nome} não foi encontrado."
        ) from erro


def localizar_expansor_da_pasta(pasta):
    """Localiza a seta usada para expandir a pasta."""
    localizadores = [
        (
            By.XPATH,
            "./div[contains(@class,'element')]"
            "/div[contains(@class,'expandCollapse')]",
        ),
        (
            By.CSS_SELECTOR,
            ":scope > .element > .expandCollapse",
        ),
    ]

    for tipo, seletor in localizadores:
        try:
            elementos = pasta.find_elements(tipo, seletor)

            for elemento in elementos:
                if elemento.is_displayed():
                    return elemento

        except (
            StaleElementReferenceException,
            WebDriverException,
        ):
            continue

    return None


def abrir_e_selecionar_pasta(
    nome: str,
    caminho: str,
) -> None:
    """Expande e seleciona uma pasta da árvore."""
    atualizar_status(f"Aguardando a pasta {nome}...")

    pasta = esperar_elemento(
        localizadores_pasta(nome, caminho),
        clicavel=False,
        timeout=TIMEOUT,
        descricao=f"pasta {nome}",
    )

    rolar_ate_elemento(pasta)

    try:
        classes = (pasta.get_attribute("class") or "").split()
    except StaleElementReferenceException:
        classes = []

    if "open" not in classes:
        expansor = localizar_expansor_da_pasta(pasta)

        if expansor is not None:
            atualizar_status(f"Expandindo a pasta {nome}...")
            clicar(expansor)
            time.sleep(1)

    # O DOM pode ser recriado depois da expansão.
    pasta = esperar_elemento(
        localizadores_pasta(nome, caminho),
        clicavel=False,
        timeout=TIMEOUT,
        descricao=f"pasta {nome} após expansão",
    )

    titulo = localizar_titulo_da_pasta(pasta, nome)

    atualizar_status(f"Selecionando a pasta {nome}...")
    clicar(titulo)
    pausa_adicional(f"Seleção da pasta {nome}")


# ============================================================
# ARQUIVO WCDF
# ============================================================

def localizadores_arquivo(
    nome_arquivo: str,
) -> list[tuple[str, str]]:
    """Cria seletores possíveis para um arquivo na coluna Files."""
    return [
        (
            By.XPATH,
            f"//*[normalize-space()='{nome_arquivo}']",
        ),
        (
            By.CSS_SELECTOR,
            f"[title='{nome_arquivo}']",
        ),
        (
            By.CSS_SELECTOR,
            f"[path$='/{nome_arquivo}']",
        ),
        (
            By.XPATH,
            (
                "//*["
                "contains("
                "concat(' ', normalize-space(@class), ' '),"
                "' file '"
                ") "
                f"and contains(normalize-space(), '{nome_arquivo}')"
                "]"
            ),
        ),
    ]


def obter_elemento_clicavel_do_arquivo(elemento):
    """
    Retorna o container clicável do arquivo quando ele existir.

    Caso não seja possível localizar o container, retorna o próprio
    elemento encontrado.
    """
    localizadores_container = [
        (
            By.XPATH,
            (
                "./ancestor::*["
                "contains("
                "concat(' ', normalize-space(@class), ' '),"
                "' file '"
                ")"
                "][1]"
            ),
        ),
        (
            By.XPATH,
            (
                "./ancestor::*["
                "@path or @title"
                "][1]"
            ),
        ),
    ]

    for tipo, seletor in localizadores_container:
        try:
            container = elemento.find_element(
                tipo,
                seletor,
            )

            if container.is_displayed():
                return container

        except (
            NoSuchElementException,
            StaleElementReferenceException,
            WebDriverException,
        ):
            continue

    return elemento


def localizar_arquivo_clicavel(nome_arquivo: str):
    """
    Localiza novamente o arquivo e retorna seu elemento clicável.

    O Pentaho pode recriar o DOM depois de cada interação. Por isso,
    essa função deve ser chamada novamente antes de cada nova ação.
    """
    elemento = esperar_elemento(
        localizadores_arquivo(nome_arquivo),
        clicavel=True,
        timeout=TIMEOUT,
        descricao=f"arquivo {nome_arquivo}",
    )

    return obter_elemento_clicavel_do_arquivo(
        elemento
    )


def arquivo_esta_selecionado(elemento) -> bool:
    """Verifica sinais comuns de seleção no arquivo."""
    try:
        classes = (
            elemento.get_attribute("class") or ""
        ).lower().split()

        aria_selected = (
            elemento.get_attribute("aria-selected") or ""
        ).lower()

        return (
            "selected" in classes
            or "active" in classes
            or "highlighted" in classes
            or aria_selected == "true"
        )

    except (
        StaleElementReferenceException,
        WebDriverException,
    ):
        return False


def selecionar_arquivo(nome_arquivo: str) -> None:
    """
    Seleciona o arquivo com um único clique.

    Esta função não abre o arquivo e não executa duplo clique.
    """
    atualizar_status(
        f"Aguardando o arquivo {nome_arquivo}..."
    )

    arquivo = localizar_arquivo_clicavel(
        nome_arquivo
    )

    atualizar_status(
        f"Selecionando {nome_arquivo}..."
    )

    # Clique simples: apenas seleciona.
    clicar(arquivo)

    # Dá tempo para o Pentaho atualizar a barra de ações.
    time.sleep(0.8)

    # Tenta confirmar visualmente a seleção, sem transformar
    # a ausência dessa classe em erro.
    try:
        arquivo = localizar_arquivo_clicavel(
            nome_arquivo
        )

        if arquivo_esta_selecionado(arquivo):
            logger.info(
                "Arquivo confirmado como selecionado: %s",
                nome_arquivo,
            )
        else:
            logger.info(
                "Clique simples executado no arquivo: %s",
                nome_arquivo,
            )

    except TimeoutException:
        logger.warning(
            "O arquivo não pôde ser localizado novamente "
            "após a seleção."
        )

    pausa_adicional("Seleção do arquivo")


def clicar_com_botao_direito(elemento) -> None:
    """Abre o menu de contexto usando o botão direito."""
    rolar_ate_elemento(elemento)

    try:
        ActionChains(driver).move_to_element(
            elemento
        ).pause(
            0.3
        ).context_click().perform()

        return

    except (
        StaleElementReferenceException,
        WebDriverException,
    ):
        pass

    driver.execute_script(
        """
        arguments[0].dispatchEvent(
            new MouseEvent("contextmenu", {
                bubbles: true,
                cancelable: true,
                view: window,
                button: 2,
                buttons: 2
            })
        );
        """,
        elemento,
    )


def localizadores_acao_arquivo(
    nome_acao: str,
) -> list[tuple[str, str]]:
    """Cria seletores para ações exibidas após selecionar o arquivo."""
    return [
        (
            By.XPATH,
            f"//*[normalize-space()='{nome_acao}']",
        ),
        (
            By.XPATH,
            (
                "//*[contains("
                f"normalize-space(), '{nome_acao}'"
                ")]"
            ),
        ),
        (
            By.CSS_SELECTOR,
            f"[title='{nome_acao}']",
        ),
        (
            By.CSS_SELECTOR,
            f"[aria-label='{nome_acao}']",
        ),
        (
            By.CSS_SELECTOR,
            f"[data-tooltip='{nome_acao}']",
        ),
        (
            By.XPATH,
            (
                "//*[@title="
                f"'{nome_acao}' or "
                f"@aria-label='{nome_acao}']"
            ),
        ),
    ]


def aguardar_nova_janela(
    janelas_anteriores: set[str],
    timeout: float | int | None = TIMEOUT,
    url_anterior: str | None = None,
) -> str | None:
    """
    Aguarda uma nova janela ou a reutilização da janela atual.

    Quando o Pentaho reutiliza a guia atual, a mudança de URL é
    considerada sucesso e None é retornado.
    """
    def detectar_destino():
        novas = (
            set(driver.window_handles)
            - janelas_anteriores
        )

        if novas:
            return (
                "NOVA_JANELA",
                novas.pop(),
            )

        if url_anterior is not None:
            try:
                if driver.current_url != url_anterior:
                    return (
                        "MESMA_JANELA",
                        None,
                    )
            except WebDriverException:
                return False

        return False

    try:
        tipo, identificador = aguardar_condicao(
            detectar_destino,
            descricao="a abertura do relatório",
            timeout=timeout,
        )
    except TimeoutException:
        return None

    if tipo == "NOVA_JANELA":
        return identificador

    return None

def abrir_arquivo_em_nova_janela(
    nome_arquivo: str,
    nome_acao: str = "Open in a new window",
) -> None:
    """
    Abre o arquivo selecionado por meio da opção
    'Open in a new window'.

    Fluxo:
    1. O arquivo já deve estar selecionado.
    2. Procura a ação na barra de ferramentas.
    3. Se não encontrar, abre o menu de contexto.
    4. Clica na ação.
    5. Troca para a nova guia/janela quando ela existir.
    """
    atualizar_status(
        f"Procurando a opção {nome_acao}..."
    )

    janelas_anteriores = set(
        driver.window_handles
    )

    try:
        url_anterior = driver.current_url
    except WebDriverException:
        url_anterior = None

    try:
        # Algumas versões mostram a ação diretamente na barra
        # depois que o arquivo é selecionado.
        opcao = esperar_elemento(
            localizadores_acao_arquivo(
                nome_acao
            ),
            clicavel=True,
            timeout=TIMEOUT_FALLBACK_CURTO,
            descricao=f"opção {nome_acao}",
        )

    except TimeoutException:
        atualizar_status(
            "Ação não apareceu na barra. "
            "Abrindo o menu de contexto..."
        )

        # Localiza novamente, pois o DOM pode ter sido recriado.
        arquivo = localizar_arquivo_clicavel(
            nome_arquivo
        )

        clicar_com_botao_direito(
            arquivo
        )

        opcao = esperar_elemento(
            localizadores_acao_arquivo(
                nome_acao
            ),
            clicavel=True,
            timeout=TIMEOUT,
            descricao=f"opção {nome_acao}",
        )

    atualizar_status(
        f"Clicando em {nome_acao}..."
    )

    clicar(opcao)

    nova_janela = aguardar_nova_janela(
        janelas_anteriores,
        timeout=TIMEOUT,
        url_anterior=url_anterior,
    )

    if nova_janela is not None:
        driver.switch_to.window(
            nova_janela
        )

        atualizar_status(
            "Relatório aberto em uma nova janela."
        )

        try:
            aguardar_documento_pronto(
                timeout=TIMEOUT
            )
        except TimeoutException:
            logger.warning(
                "A nova janela foi aberta, mas o documento "
                "não atingiu readyState=complete dentro do prazo."
            )

    else:
        logger.warning(
            "A ação foi clicada, mas nenhuma nova guia foi "
            "detectada. O Pentaho pode ter reutilizado a guia atual."
        )

        atualizar_status(
            "A ação foi executada na guia atual."
        )

    pausa_adicional(
        "Abertura do relatório em nova janela"
    )


def abrir_arquivo(nome_arquivo: str) -> None:
    """
    Abre um arquivo com duplo clique.

    Esta função permanece disponível para outros processos.
    Para o acompanhamento de separação, use selecionar_arquivo()
    seguido de abrir_arquivo_em_nova_janela().
    """
    atualizar_status(
        f"Aguardando o arquivo {nome_arquivo}..."
    )

    arquivo = localizar_arquivo_clicavel(
        nome_arquivo
    )

    atualizar_status(
        f"Abrindo {nome_arquivo}..."
    )

    clicar_duas_vezes(
        arquivo
    )

    pausa_adicional(
        "Abertura do relatório"
    )



# ============================================================
# AUTOMAÇÃO DOS FILTROS DO DASHBOARD
# ============================================================

SCRIPT_LOCALIZAR_TEXTO_EXATO = r"""
const procurado = String(arguments[0] || "");

function normalizar(texto) {
    return String(texto || "")
        .normalize("NFD")
        .replace(/[\u0300-\u036f]/g, "")
        .replace(/\s+/g, " ")
        .trim()
        .toLowerCase();
}

function visivel(elemento) {
    if (!elemento) {
        return false;
    }

    const estilo = window.getComputedStyle(elemento);
    const caixa = elemento.getBoundingClientRect();

    return (
        estilo.display !== "none" &&
        estilo.visibility !== "hidden" &&
        Number(estilo.opacity || 1) !== 0 &&
        caixa.width > 0 &&
        caixa.height > 0
    );
}

const alvo = normalizar(procurado);
const elementos = Array.from(
    document.querySelectorAll("body *")
).filter((elemento) => {
    return (
        visivel(elemento) &&
        normalizar(elemento.textContent) === alvo
    );
});

if (!elementos.length) {
    return null;
}

function pontuar(elemento) {
    let pontos = 0;
    let atual = elemento;

    for (let nivel = 0; nivel < 6 && atual; nivel += 1) {
        const papel = normalizar(
            atual.getAttribute &&
            atual.getAttribute("role")
        );

        const classes = normalizar(
            atual.className
        );

        if (
            papel === "option" ||
            papel === "menuitem" ||
            papel === "listitem"
        ) {
            pontos += 100;
        }

        if (
            classes.includes("option") ||
            classes.includes("result") ||
            classes.includes("menu-item") ||
            classes.includes("list-item") ||
            classes.includes("dropdown-item")
        ) {
            pontos += 70;
        }

        if (
            atual.querySelector &&
            atual.querySelector(
                'input[type="radio"], input[type="checkbox"]'
            )
        ) {
            pontos += 90;
        }

        if (
            atual.tagName === "BUTTON" ||
            atual.tagName === "A" ||
            atual.tagName === "LABEL"
        ) {
            pontos += 60;
        }

        if (
            window.getComputedStyle(atual).cursor === "pointer"
        ) {
            pontos += 30;
        }

        atual = atual.parentElement;
    }

    // Prefere o elemento mais específico.
    pontos -= elemento.children.length * 2;

    return pontos;
}

elementos.sort(
    (a, b) => pontuar(b) - pontuar(a)
);

let escolhido = elementos[0];
let atual = escolhido;

for (let nivel = 0; nivel < 6 && atual; nivel += 1) {
    const papel = normalizar(
        atual.getAttribute &&
        atual.getAttribute("role")
    );

    const classes = normalizar(
        atual.className
    );

    const possuiMarcador = Boolean(
        atual.querySelector &&
        atual.querySelector(
            'input[type="radio"], input[type="checkbox"]'
        )
    );

    if (
        possuiMarcador ||
        papel === "option" ||
        papel === "menuitem" ||
        atual.tagName === "BUTTON" ||
        atual.tagName === "A" ||
        atual.tagName === "LABEL" ||
        classes.includes("option") ||
        classes.includes("dropdown-item") ||
        classes.includes("menu-item")
    ) {
        escolhido = atual;
        break;
    }

    atual = atual.parentElement;
}

return escolhido;
"""


SCRIPT_LOCALIZAR_CONTROLE_POR_ROTULO = r"""
const rotuloProcurado = String(arguments[0] || "");

function normalizar(texto) {
    return String(texto || "")
        .normalize("NFD")
        .replace(/[\u0300-\u036f]/g, "")
        .replace(/\s+/g, " ")
        .trim()
        .toLowerCase();
}

function visivel(elemento) {
    if (!elemento) {
        return false;
    }

    const estilo = window.getComputedStyle(elemento);
    const caixa = elemento.getBoundingClientRect();

    return (
        estilo.display !== "none" &&
        estilo.visibility !== "hidden" &&
        Number(estilo.opacity || 1) !== 0 &&
        caixa.width > 0 &&
        caixa.height > 0
    );
}

function pontuarControle(elemento) {
    if (!visivel(elemento)) {
        return -1000;
    }

    let pontos = 0;
    const tag = elemento.tagName;
    const papel = normalizar(
        elemento.getAttribute("role")
    );

    const classes = normalizar(
        elemento.className
    );

    if (
        tag === "INPUT" ||
        tag === "SELECT" ||
        tag === "TEXTAREA"
    ) {
        pontos += 150;
    }

    if (papel === "combobox") {
        pontos += 140;
    }

    if (
        elemento.getAttribute("aria-haspopup") === "listbox"
    ) {
        pontos += 120;
    }

    if (
        classes.includes("select2") ||
        classes.includes("chosen") ||
        classes.includes("dropdown") ||
        classes.includes("select")
    ) {
        pontos += 80;
    }

    if (
        window.getComputedStyle(elemento).cursor === "pointer"
    ) {
        pontos += 40;
    }

    const caixa = elemento.getBoundingClientRect();

    if (
        caixa.width >= 80 &&
        caixa.height >= 20 &&
        caixa.height <= 90
    ) {
        pontos += 30;
    }

    return pontos;
}

const alvo = normalizar(rotuloProcurado);

const rotulos = Array.from(
    document.querySelectorAll(
        "label, span, div, p, td, th"
    )
).filter((elemento) => {
    return (
        visivel(elemento) &&
        normalizar(elemento.textContent) === alvo
    );
});

for (const rotulo of rotulos) {
    let ancestral = rotulo.parentElement;

    for (
        let nivel = 0;
        nivel < 7 && ancestral;
        nivel += 1
    ) {
        const candidatos = Array.from(
            ancestral.querySelectorAll(
                [
                    "input:not([type='hidden'])",
                    "select",
                    "textarea",
                    "[role='combobox']",
                    "[aria-haspopup='listbox']",
                    ".select2-container",
                    ".chosen-container",
                    ".dropdown-toggle",
                    "[class*='select']",
                    "[class*='dropdown']"
                ].join(",")
            )
        ).filter((elemento) => {
            return (
                elemento !== rotulo &&
                visivel(elemento)
            );
        });

        candidatos.sort(
            (a, b) => (
                pontuarControle(b) -
                pontuarControle(a)
            )
        );

        if (
            candidatos.length &&
            pontuarControle(candidatos[0]) > 0
        ) {
            return candidatos[0];
        }

        // Tenta o próximo irmão do rótulo.
        let irmao = rotulo.nextElementSibling;

        while (irmao) {
            if (
                visivel(irmao) &&
                pontuarControle(irmao) > 0
            ) {
                return irmao;
            }

            const interno = irmao.querySelector &&
                irmao.querySelector(
                    [
                        "input:not([type='hidden'])",
                        "select",
                        "textarea",
                        "[role='combobox']",
                        "[aria-haspopup='listbox']",
                        ".select2-container",
                        ".chosen-container",
                        ".dropdown-toggle",
                        "[class*='select']",
                        "[class*='dropdown']"
                    ].join(",")
                );

            if (
                interno &&
                visivel(interno)
            ) {
                return interno;
            }

            irmao = irmao.nextElementSibling;
        }

        ancestral = ancestral.parentElement;
    }
}

return null;
"""


SCRIPT_DEFINIR_CHECKBOX_POR_TEXTO = r"""
const textoProcurado = String(arguments[0] || "");
const estadoDesejado = Boolean(arguments[1]);

function normalizar(texto) {
    return String(texto || "")
        .normalize("NFD")
        .replace(/[\u0300-\u036f]/g, "")
        .replace(/\s+/g, " ")
        .trim()
        .toLowerCase();
}

function visivel(elemento) {
    if (!elemento) {
        return false;
    }

    const estilo = window.getComputedStyle(elemento);
    const caixa = elemento.getBoundingClientRect();

    return (
        estilo.display !== "none" &&
        estilo.visibility !== "hidden" &&
        Number(estilo.opacity || 1) !== 0 &&
        caixa.width > 0 &&
        caixa.height > 0
    );
}

const alvo = normalizar(textoProcurado);

const textos = Array.from(
    document.querySelectorAll("body *")
).filter((elemento) => {
    return (
        visivel(elemento) &&
        normalizar(elemento.textContent) === alvo
    );
});

for (const texto of textos) {
    let linha = texto;

    for (
        let nivel = 0;
        nivel < 7 && linha;
        nivel += 1
    ) {
        const checkbox = linha.querySelector &&
            linha.querySelector(
                'input[type="checkbox"]'
            );

        if (checkbox && visivel(linha)) {
            if (
                Boolean(checkbox.checked) !==
                estadoDesejado
            ) {
                checkbox.click();

                checkbox.dispatchEvent(
                    new Event(
                        "input",
                        { bubbles: true }
                    )
                );

                checkbox.dispatchEvent(
                    new Event(
                        "change",
                        { bubbles: true }
                    )
                );
            }

            return true;
        }

        linha = linha.parentElement;
    }
}

return false;
"""


def procurar_resultado_script_recursivamente(
    script: str,
    argumentos: tuple,
    profundidade: int = 0,
):
    """
    Executa um script na página principal e dentro de iframes.

    Quando encontra um elemento, mantém o driver no frame correto.
    """
    if profundidade > MAX_PROFUNDIDADE_IFRAMES:
        return None

    try:
        resultado = driver.execute_script(
            script,
            *argumentos,
        )

        if resultado:
            return resultado

    except (
        JavascriptException,
        WebDriverException,
    ):
        pass

    try:
        frames = driver.find_elements(
            By.CSS_SELECTOR,
            "iframe, frame",
        )

    except WebDriverException:
        return None

    for frame in frames:
        entrou = False

        try:
            driver.switch_to.frame(frame)
            entrou = True

            resultado = (
                procurar_resultado_script_recursivamente(
                    script,
                    argumentos,
                    profundidade + 1,
                )
            )

            if resultado:
                return resultado

        except (
            NoSuchFrameException,
            StaleElementReferenceException,
            WebDriverException,
        ):
            pass

        if entrou:
            try:
                driver.switch_to.parent_frame()
            except WebDriverException:
                driver.switch_to.default_content()

    return None


def esperar_resultado_script(
    script: str,
    *argumentos,
    timeout: float | int | None = TIMEOUT,
    descricao: str = "elemento",
):
    """Aguarda um JavaScript retornar um elemento ou valor válido."""
    def executar_busca():
        if driver is None:
            return False

        try:
            driver.switch_to.default_content()
        except WebDriverException:
            return False

        return (
            procurar_resultado_script_recursivamente(
                script,
                argumentos,
            )
            or False
        )

    resultado = aguardar_condicao(
        executar_busca,
        descricao=descricao,
        timeout=timeout,
    )

    logger.info(
        "Elemento encontrado por script: %s",
        descricao,
    )

    return resultado

def esperar_dashboard_carregar() -> None:
    """Aguarda os controles principais do dashboard aparecerem."""
    atualizar_status(
        "Aguardando o dashboard carregar..."
    )

    esperar_resultado_script(
        SCRIPT_LOCALIZAR_CONTROLE_POR_ROTULO,
        "Selecione a unidade",
        timeout=TIMEOUT,
        descricao="campo Selecione a unidade",
    )

    pausa_adicional(
        "Carregamento do dashboard"
    )


def localizar_controle_por_rotulo(
    rotulo: str,
):
    """Localiza um campo usando o texto apresentado acima dele."""
    return esperar_resultado_script(
        SCRIPT_LOCALIZAR_CONTROLE_POR_ROTULO,
        rotulo,
        timeout=TIMEOUT,
        descricao=f"campo {rotulo}",
    )


def localizar_texto_exato_visivel(
    texto: str,
    timeout: float | int | None = TIMEOUT,
):
    """Localiza uma opção ou botão pelo texto exato visível."""
    return esperar_resultado_script(
        SCRIPT_LOCALIZAR_TEXTO_EXATO,
        texto,
        timeout=timeout,
        descricao=f"texto {texto}",
    )


def selecionar_opcao_dropdown(
    rotulo: str,
    opcao: str,
) -> None:
    """
    Abre um campo de seleção e escolhe uma opção pelo texto.

    Funciona com os seletores customizados observados no dashboard.
    """
    atualizar_status(
        f"Configurando {rotulo}: {opcao}..."
    )

    controle = localizar_controle_por_rotulo(
        rotulo
    )

    clicar(controle)
    time.sleep(0.6)

    elemento_opcao = localizar_texto_exato_visivel(
        opcao,
        timeout=TIMEOUT,
    )

    clicar(elemento_opcao)
    time.sleep(0.7)

    logger.info(
        "Opção selecionada: %s = %s",
        rotulo,
        opcao,
    )


def localizadores_opcao_multiselect(
    opcao: str,
) -> list[tuple[str, str]]:
    """Cria seletores exatos para uma opção do multiselect."""
    return [
        (
            By.CSS_SELECTOR,
            (
                "div.filter-item-label"
                f"[title='{opcao}']"
            ),
        ),
        (
            By.XPATH,
            (
                "//div["
                "contains("
                "concat(' ', normalize-space(@class), ' '),"
                "' filter-item-label '"
                ") "
                f"and @title='{opcao}' "
                f"and normalize-space()='{opcao}'"
                "]"
            ),
        ),
    ]


def tentar_localizar_linha_opcao_multiselect(
    opcao: str,
):
    """
    Procura a opção visível sem aguardar.

    A busca sempre recomeça na página principal e percorre todos
    os frames novamente. Isso evita reutilizar um frame ou elemento
    que o Pentaho acabou de reconstruir.
    """
    if driver is None:
        return None

    try:
        driver.switch_to.default_content()

    except (
        InvalidSessionIdException,
        NoSuchWindowException,
    ):
        raise

    except WebDriverException:
        return None

    return procurar_recursivamente_nos_frames(
        localizadores_opcao_multiselect(
            opcao
        ),
        clicavel=False,
    )


def localizar_linha_opcao_multiselect(
    opcao: str,
):
    """
    Aguarda uma opção visível do multiselect pelo atributo title.

    O elemento é sempre procurado novamente no DOM e no frame atual.
    """
    return aguardar_condicao(
        lambda: (
            tentar_localizar_linha_opcao_multiselect(
                opcao
            )
            or False
        ),
        descricao=f"opção do cliente {opcao}",
        timeout=TIMEOUT,
    )


def obter_corpo_opcao_multiselect(
    rotulo_opcao,
):
    """Retorna a linha filter-item-body da opção atual."""
    try:
        return rotulo_opcao.find_element(
            By.XPATH,
            (
                "./ancestor::div["
                "contains("
                "concat(' ', normalize-space(@class), ' '),"
                "' filter-item-body '"
                ")"
                "][1]"
            ),
        )

    except StaleElementReferenceException:
        raise

    except NoSuchElementException as erro:
        raise TimeoutException(
            "Não foi possível localizar a linha da opção."
        ) from erro


def obter_icone_selecao_multiselect(
    corpo_opcao,
):
    """Localiza o ícone atual usado para marcar ou desmarcar."""
    try:
        return corpo_opcao.find_element(
            By.CSS_SELECTOR,
            ".filter-item-selection-icon",
        )

    except StaleElementReferenceException:
        raise

    except NoSuchElementException as erro:
        raise TimeoutException(
            "O ícone de seleção do cliente não foi encontrado."
        ) from erro


SCRIPT_ESTADO_OPCAO_MULTISELECT = r"""
const rotulo = arguments[0];

if (!rotulo) {
    return "unknown";
}

function classes(elemento) {
    return String(
        elemento && elemento.className || ""
    )
        .trim()
        .toLowerCase()
        .split(/\s+/)
        .filter(Boolean);
}

const corpo = (
    rotulo.closest(".filter-item-body") ||
    rotulo.parentElement ||
    rotulo
);

let atual = corpo;

for (
    let nivel = 0;
    nivel < 7 && atual;
    nivel += 1
) {
    const lista = classes(atual);

    if (
        lista.includes("none-selected") ||
        lista.includes("unselected")
    ) {
        return "unselected";
    }

    if (
        lista.includes("all-selected") ||
        lista.includes("selected") ||
        lista.includes("checked") ||
        lista.includes("active")
    ) {
        return "selected";
    }

    if (
        lista.includes("some-selected") ||
        lista.includes("partially-selected") ||
        lista.includes("partial-selected") ||
        lista.includes("indeterminate")
    ) {
        return "partial";
    }

    const ariaChecked = String(
        atual.getAttribute &&
        atual.getAttribute("aria-checked") || ""
    ).trim().toLowerCase();

    const ariaSelected = String(
        atual.getAttribute &&
        atual.getAttribute("aria-selected") || ""
    ).trim().toLowerCase();

    if (
        ariaChecked === "true" ||
        ariaSelected === "true"
    ) {
        return "selected";
    }

    if (
        ariaChecked === "false" ||
        ariaSelected === "false"
    ) {
        return "unselected";
    }

    if (ariaChecked === "mixed") {
        return "partial";
    }

    atual = atual.parentElement;
}

const checkbox = corpo.querySelector(
    'input[type="checkbox"]'
);

if (checkbox) {
    if (checkbox.indeterminate) {
        return "partial";
    }

    return checkbox.checked
        ? "selected"
        : "unselected";
}

const icone = corpo.querySelector(
    ".filter-item-selection-icon"
);

if (icone) {
    const antes = window.getComputedStyle(
        icone,
        "::before"
    );

    const depois = window.getComputedStyle(
        icone,
        "::after"
    );

    const conteudo = [
        icone.textContent,
        antes.content,
        depois.content
    ].join(" ");

    if (
        conteudo.includes("✓") ||
        conteudo.includes("✔") ||
        conteudo.toLowerCase().includes("check")
    ) {
        return "selected";
    }
}

return "unknown";
"""


def obter_estado_opcao_multiselect(
    rotulo_opcao,
) -> str:
    """
    Retorna selected, unselected, partial ou unknown.

    O elemento deve ter sido localizado imediatamente antes desta
    chamada. StaleElementReferenceException é propagada para que a
    função principal relocalize tudo.
    """
    estado = driver.execute_script(
        SCRIPT_ESTADO_OPCAO_MULTISELECT,
        rotulo_opcao,
    )

    estado_texto = str(
        estado or "unknown"
    ).strip().lower()

    if estado_texto in {
        "selected",
        "unselected",
        "partial",
        "unknown",
    }:
        return estado_texto

    return "unknown"


def localizadores_botao_apply_multiselect() -> list[tuple[str, str]]:
    """Seletores do Apply dirty e habilitado."""
    return [
        (
            By.CSS_SELECTOR,
            (
                "button.filter-btn-apply"
                ".dirty:not([disabled])"
            ),
        ),
        (
            By.XPATH,
            (
                "//button["
                "contains("
                "concat(' ', normalize-space(@class), ' '),"
                "' filter-btn-apply '"
                ") "
                "and contains("
                "concat(' ', normalize-space(@class), ' '),"
                "' dirty '"
                ") "
                "and not(@disabled) "
                "and normalize-space()='Apply'"
                "]"
            ),
        ),
    ]


def tentar_localizar_botao_apply_multiselect():
    """
    Procura o Apply habilitado sem aguardar.

    A busca também relocaliza o frame, portanto não depende do frame
    ou do WebElement usado antes da atualização do dashboard.
    """
    if driver is None:
        return None

    try:
        driver.switch_to.default_content()

    except (
        InvalidSessionIdException,
        NoSuchWindowException,
    ):
        raise

    except WebDriverException:
        return None

    return procurar_recursivamente_nos_frames(
        localizadores_botao_apply_multiselect(),
        clicavel=True,
    )


def localizar_botao_apply_multiselect(
    timeout: float | int | None = TIMEOUT,
):
    """
    Aguarda o Apply ficar dirty e habilitado, relocalizando o DOM.
    """
    return aguardar_condicao(
        lambda: (
            tentar_localizar_botao_apply_multiselect()
            or False
        ),
        descricao="botão Apply habilitado do multiselect",
        timeout=timeout,
    )


def fechar_multiselect_sem_aplicar() -> None:
    """Fecha um multiselect aberto usando Escape."""
    if driver is None:
        return

    try:
        driver.switch_to.active_element.send_keys(
            Keys.ESCAPE
        )

        pausa_responsiva(
            0.5
        )

    except (
        StaleElementReferenceException,
        WebDriverException,
    ):
        pass


def abrir_multiselect_relocalizando(
    rotulo: str,
    opcao_visivel: str,
):
    """
    Abre o multiselect relocalizando controle, frame e opção.

    O painel somente é considerado aberto quando a opção desejada
    está realmente visível.
    """
    tentativa = 0

    while True:
        if EVENTO_CANCELAMENTO.is_set():
            raise InterruptedError(
                f"Automação cancelada ao abrir {rotulo}."
            )

        tentativa += 1

        rotulo_opcao = (
            tentar_localizar_linha_opcao_multiselect(
                opcao_visivel
            )
        )

        if rotulo_opcao is not None:
            logger.info(
                "%s aberto e confirmado pela opção %s.",
                rotulo,
                opcao_visivel,
            )

            return rotulo_opcao

        atualizar_status(
            f"Abrindo {rotulo} "
            f"— tentativa {tentativa}..."
        )

        try:
            controle = localizar_controle_por_rotulo(
                rotulo
            )

            clicar(
                controle
            )

        except (
            StaleElementReferenceException,
            NoSuchFrameException,
        ) as erro:
            logger.warning(
                "O controle %s foi recriado antes do clique: %s. "
                "Relocalizando...",
                rotulo,
                erro,
            )

            pausa_responsiva(
                0.8
            )

            continue

        except (
            InvalidSessionIdException,
            NoSuchWindowException,
        ):
            raise

        except WebDriverException as erro:
            logger.warning(
                "Falha temporária ao clicar em %s: %s. "
                "Relocalizando...",
                rotulo,
                erro,
            )

            pausa_responsiva(
                0.8
            )

            continue

        try:
            return aguardar_condicao(
                lambda: (
                    tentar_localizar_linha_opcao_multiselect(
                        opcao_visivel
                    )
                    or False
                ),
                descricao=(
                    f"a abertura de {rotulo} "
                    f"com a opção {opcao_visivel}"
                ),
                timeout=max(
                    5.0,
                    TIMEOUT_FALLBACK_CURTO,
                ),
                intervalo=0.25,
                atualizar_status_periodicamente=False,
            )

        except TimeoutException:
            logger.warning(
                "%s ainda não abriu após a tentativa %d. "
                "O controle será relocalizado.",
                rotulo,
                tentativa,
            )

            pausa_responsiva(
                0.8
            )


def aguardar_multiselect_fechar(
    timeout: float | int | None = 30,
) -> None:
    """
    Aguarda o painel fechar, relocalizando em todos os frames.

    A ausência de confirmação não interrompe a automação.
    """
    def painel_fechou():
        try:
            if (
                tentar_localizar_botao_apply_multiselect()
                is not None
            ):
                return False

            return (
                tentar_localizar_linha_opcao_multiselect(
                    CLIENTE_PARA_REMOVER
                )
                is None
            )

        except (
            StaleElementReferenceException,
            NoSuchFrameException,
            WebDriverException,
        ):
            return True

    try:
        aguardar_condicao(
            painel_fechou,
            descricao="o painel multiselect fechar",
            timeout=timeout,
            atualizar_status_periodicamente=False,
        )

    except TimeoutException:
        logger.warning(
            "O painel de clientes não confirmou o fechamento, "
            "mas o botão Apply já foi acionado."
        )


def clicar_apply_multiselect_relocalizando(
    opcao: str,
) -> None:
    """
    Clica no Apply usando um WebElement novo em cada tentativa.
    """
    tentativa = 0

    while True:
        if EVENTO_CANCELAMENTO.is_set():
            raise InterruptedError(
                "Automação cancelada antes do Apply."
            )

        tentativa += 1

        botao_apply = (
            tentar_localizar_botao_apply_multiselect()
        )

        if botao_apply is None:
            if (
                tentar_localizar_linha_opcao_multiselect(
                    opcao
                )
                is None
            ):
                logger.info(
                    "O painel fechou após o Apply."
                )

                return

            botao_apply = localizar_botao_apply_multiselect(
                timeout=TIMEOUT
            )

        atualizar_status(
            "Aplicando a remoção de "
            f"{opcao} — tentativa {tentativa}..."
        )

        try:
            clicar(
                botao_apply
            )

            aguardar_multiselect_fechar(
                timeout=30
            )

            return

        except (
            StaleElementReferenceException,
            NoSuchFrameException,
        ) as erro:
            logger.warning(
                "O botão Apply foi recriado antes do clique: %s. "
                "Relocalizando...",
                erro,
            )

            pausa_responsiva(
                0.8
            )

        except (
            InvalidSessionIdException,
            NoSuchWindowException,
        ):
            raise

        except WebDriverException as erro:
            logger.warning(
                "Falha temporária ao clicar no Apply: %s. "
                "Relocalizando...",
                erro,
            )

            pausa_responsiva(
                0.8
            )


def desmarcar_opcao_multiselect(
    rotulo: str,
    opcao: str,
) -> None:
    """
    Desmarca somente a opção informada e aplica a alteração.

    Todos os elementos são relocalizados depois de qualquer mudança:
    controle, frame, rótulo, linha, ícone e botão Apply.
    """
    tentativa = 0
    proximo_aviso = (
        time.monotonic()
        + INTERVALO_AVISO_ESPERA
    )

    while True:
        if EVENTO_CANCELAMENTO.is_set():
            raise InterruptedError(
                f"Automação cancelada ao remover {opcao}."
            )

        tentativa += 1

        atualizar_status(
            f"Configurando {rotulo}: remover {opcao} "
            f"— tentativa {tentativa}..."
        )

        try:
            # Uma tentativa anterior pode já ter alterado a opção.
            if (
                tentar_localizar_botao_apply_multiselect()
                is not None
            ):
                logger.info(
                    "Alteração pendente detectada. "
                    "Aplicando sem repetir o clique em %s.",
                    opcao,
                )

                clicar_apply_multiselect_relocalizando(
                    opcao
                )

                pausa_adicional(
                    f"Remoção do cliente {opcao}"
                )

                return

            abrir_multiselect_relocalizando(
                rotulo,
                opcao,
            )

            rotulo_opcao = localizar_linha_opcao_multiselect(
                opcao
            )

            estado_inicial = obter_estado_opcao_multiselect(
                rotulo_opcao
            )

            logger.info(
                "Estado atual de %s: %s",
                opcao,
                estado_inicial,
            )

            if estado_inicial == "unselected":
                logger.info(
                    "%s já estava desmarcado. "
                    "Nenhum clique é necessário.",
                    opcao,
                )

                fechar_multiselect_sem_aplicar()

                pausa_adicional(
                    f"Verificação do cliente {opcao}"
                )

                return

            # Relocaliza toda a cadeia imediatamente antes do clique.
            rotulo_opcao = localizar_linha_opcao_multiselect(
                opcao
            )

            corpo_opcao = obter_corpo_opcao_multiselect(
                rotulo_opcao
            )

            icone_selecao = obter_icone_selecao_multiselect(
                corpo_opcao
            )

            atualizar_status(
                f"Desmarcando somente {opcao}..."
            )

            clicar(
                icone_selecao
            )

            try:
                aguardar_condicao(
                    lambda: (
                        tentar_localizar_botao_apply_multiselect()
                        or False
                    ),
                    descricao=(
                        "o Apply ficar habilitado após "
                        f"desmarcar {opcao}"
                    ),
                    timeout=max(
                        8.0,
                        TIMEOUT_FALLBACK_CURTO,
                    ),
                    intervalo=0.25,
                    atualizar_status_periodicamente=False,
                )

            except TimeoutException:
                # Antes de repetir o clique, confere o estado novo.
                rotulo_atual = (
                    tentar_localizar_linha_opcao_multiselect(
                        opcao
                    )
                )

                if rotulo_atual is not None:
                    estado_atual = (
                        obter_estado_opcao_multiselect(
                            rotulo_atual
                        )
                    )

                    logger.warning(
                        "Apply ainda não apareceu; "
                        "estado relocalizado de %s: %s",
                        opcao,
                        estado_atual,
                    )

                    if (
                        estado_atual == "unselected"
                        and tentar_localizar_botao_apply_multiselect()
                        is None
                    ):
                        logger.info(
                            "%s está desmarcado e não existe "
                            "alteração pendente para aplicar.",
                            opcao,
                        )

                        fechar_multiselect_sem_aplicar()

                        pausa_adicional(
                            f"Remoção do cliente {opcao}"
                        )

                        return

                pausa_responsiva(
                    0.8
                )

                continue

            clicar_apply_multiselect_relocalizando(
                opcao
            )

            logger.info(
                "Cliente desmarcado e aplicado com sucesso: %s",
                opcao,
            )

            pausa_adicional(
                f"Remoção do cliente {opcao}"
            )

            return

        except (
            StaleElementReferenceException,
            NoSuchFrameException,
        ) as erro:
            logger.warning(
                "O Pentaho recriou o DOM durante a remoção "
                "de %s: %s. Tudo será relocalizado.",
                opcao,
                erro,
            )

        except (
            InvalidSessionIdException,
            NoSuchWindowException,
        ):
            raise

        except (
            JavascriptException,
            TimeoutException,
            WebDriverException,
        ) as erro:
            logger.warning(
                "Falha transitória ao remover %s "
                "na tentativa %d: %s. "
                "O processo continuará relocalizando.",
                opcao,
                tentativa,
                erro,
            )

        agora = time.monotonic()

        if agora >= proximo_aviso:
            atualizar_status(
                f"Ainda configurando {opcao}. "
                f"Tentativas realizadas: {tentativa}. "
                "O processo continuará aguardando..."
            )

            proximo_aviso = (
                agora
                + INTERVALO_AVISO_ESPERA
            )

        pausa_responsiva(
            1.0
        )


def validar_data_hora(
    valor: str,
    nome_campo: str,
) -> str:
    """
    Valida datas no formato DD/MM/AAAA HH:MM:SS.

    Retorna o mesmo valor normalizado.
    """
    valor_normalizado = valor.strip()

    try:
        data = datetime.strptime(
            valor_normalizado,
            "%d/%m/%Y %H:%M:%S",
        )

    except ValueError as erro:
        raise ValueError(
            f"{nome_campo} deve usar o formato "
            "DD/MM/AAAA HH:MM:SS. "
            f"Valor recebido: {valor!r}"
        ) from erro

    return data.strftime(
        "%d/%m/%Y %H:%M:%S"
    )


def localizadores_input_data_hora(
    painel_id: str,
    input_id: str,
) -> list[tuple[str, str]]:
    """Retorna os seletores exatos de um campo de data/hora."""
    return [
        (
            By.CSS_SELECTOR,
            f"div#{painel_id} input#{input_id}",
        ),
        (
            By.CSS_SELECTOR,
            f"#{painel_id} input#{input_id}",
        ),
        (
            By.XPATH,
            f"//div[@id='{painel_id}']//input[@id='{input_id}']",
        ),
        (
            By.CSS_SELECTOR,
            f"#{painel_id} input[type='text']",
        ),
    ]


def localizar_input_data_hora(
    painel_id: str,
    input_id: str,
    descricao: str,
):
    """Localiza o input de data/hora, inclusive em iframes."""
    return esperar_elemento(
        localizadores_input_data_hora(
            painel_id,
            input_id,
        ),
        clicavel=True,
        timeout=TIMEOUT,
        descricao=descricao,
    )

def definir_valor_input_com_eventos(
    campo,
    valor: str,
) -> None:
    """
    Define o valor do input e dispara os eventos usados pelo dashboard.

    Primeiro tenta digitação real. Depois confirma o valor e usa
    JavaScript apenas como fallback.
    """
    rolar_ate_elemento(
        campo
    )

    clicar(
        campo
    )

    try:
        campo.send_keys(
            Keys.CONTROL,
            "a",
        )

        campo.send_keys(
            Keys.BACKSPACE
        )

        campo.send_keys(
            valor
        )

        # TAB costuma confirmar o valor em componentes de data.
        campo.send_keys(
            Keys.TAB
        )

    except (
        StaleElementReferenceException,
        WebDriverException,
    ):
        pass

    time.sleep(0.4)

    try:
        valor_atual = (
            campo.get_attribute("value") or ""
        ).strip()

    except (
        StaleElementReferenceException,
        WebDriverException,
    ):
        valor_atual = ""

    if valor_atual != valor:
        driver.execute_script(
            """
            const campo = arguments[0];
            const valor = arguments[1];

            campo.focus();

            const descritor = Object.getOwnPropertyDescriptor(
                HTMLInputElement.prototype,
                "value"
            );

            if (descritor && descritor.set) {
                descritor.set.call(campo, valor);
            } else {
                campo.value = valor;
            }

            campo.dispatchEvent(
                new Event(
                    "input",
                    { bubbles: true }
                )
            );

            campo.dispatchEvent(
                new Event(
                    "change",
                    { bubbles: true }
                )
            );

            campo.dispatchEvent(
                new KeyboardEvent(
                    "keyup",
                    {
                        bubbles: true,
                        key: "Tab"
                    }
                )
            );

            campo.blur();
            """,
            campo,
            valor,
        )

    else:
        # Mesmo quando a digitação funcionar, garante que o Pentaho
        # receba os eventos input/change/blur.
        driver.execute_script(
            """
            const campo = arguments[0];

            campo.dispatchEvent(
                new Event(
                    "input",
                    { bubbles: true }
                )
            );

            campo.dispatchEvent(
                new Event(
                    "change",
                    { bubbles: true }
                )
            );

            campo.dispatchEvent(
                new Event(
                    "blur",
                    { bubbles: true }
                )
            );
            """,
            campo,
        )

    time.sleep(0.5)


def confirmar_valor_input(
    painel_id: str,
    input_id: str,
    valor_esperado: str,
    descricao: str,
) -> None:
    """
    Confirma o valor sem o antigo limite fixo de 15 segundos.

    Usa uma busca de tentativa única dentro da condição para evitar
    esperas aninhadas.
    """
    localizadores = localizadores_input_data_hora(
        painel_id,
        input_id,
    )

    def valor_foi_aplicado():
        try:
            driver.switch_to.default_content()
        except WebDriverException:
            return False

        campo = procurar_recursivamente_nos_frames(
            localizadores,
            True,
        )

        if campo is None:
            return False

        try:
            valor_atual = (
                campo.get_attribute("value")
                or ""
            ).strip()
        except (
            StaleElementReferenceException,
            WebDriverException,
        ):
            return False

        return campo if valor_atual == valor_esperado else False

    aguardar_condicao(
        valor_foi_aplicado,
        descricao=f"a confirmação de {descricao}",
        timeout=TIMEOUT,
    )

def preencher_data_hora_por_id(
    *,
    painel_id: str,
    input_id: str,
    valor: str,
    descricao: str,
) -> None:
    """
    Preenche exclusivamente o input indicado por painel_id/input_id.

    Isso impede que o valor seja digitado no seletor da unidade CWBII
    ou em qualquer outro controle próximo.
    """
    valor_validado = validar_data_hora(
        valor,
        descricao,
    )

    atualizar_status(
        f"Preenchendo {descricao}: {valor_validado}..."
    )

    campo = localizar_input_data_hora(
        painel_id=painel_id,
        input_id=input_id,
        descricao=descricao,
    )

    logger.info(
        "Campo de data localizado: painel=%s | input=%s",
        painel_id,
        input_id,
    )

    definir_valor_input_com_eventos(
        campo,
        valor_validado,
    )

    confirmar_valor_input(
        painel_id=painel_id,
        input_id=input_id,
        valor_esperado=valor_validado,
        descricao=descricao,
    )

    logger.info(
        "%s preenchida corretamente: %s",
        descricao,
        valor_validado,
    )


def horainicial(
    valor: str,
) -> None:
    """
    Define exclusivamente o campo Data/Hora inicial.

    HTML esperado:

        <div id="panelFilterDataInicial">
            <input
                id="render_ticDataInicial"
                name="render_ticDataInicial"
            >
        </div>
    """
    preencher_data_hora_por_id(
        painel_id="panelFilterDataInicial",
        input_id="render_ticDataInicial",
        valor=valor,
        descricao="Data/Hora inicial",
    )


def horafinal(
    valor: str,
) -> None:
    """
    Define exclusivamente o campo Data/Hora final.

    HTML esperado:

        <div id="panelFilterDataFinal">
            <input
                id="render_ticDataFinal"
                name="render_ticDataFinal"
            >
        </div>
    """
    preencher_data_hora_por_id(
        painel_id="panelFilterDataFinal",
        input_id="render_ticDataFinal",
        valor=valor,
        descricao="Data/Hora final",
    )



def aplicar_filtro_todos() -> None:
    """Clica no botão Aplicar Filtro (Todos)."""
    atualizar_status(
        "Aplicando o filtro de todos..."
    )

    botao = localizar_texto_exato_visivel(
        BOTAO_APLICAR_FILTRO,
        timeout=TIMEOUT,
    )

    clicar(botao)

    pausa_adicional(
        "Aplicação do filtro"
    )


def configurar_dashboard(
    hora_inicial: str,
    hora_final: str,
) -> None:
    """
    Configura todos os filtros solicitados no relatório.

    Ordem:
    1. Unidade: CWBII.
    2. Remove cliente MDLZ-MP.
    3. Intervalo: 05 Minutos.
    4. Data base: Agendamento.
    5. Backlog: SIM.
    6. Data/Hora inicial.
    7. Data/Hora final.
    8. Aplicar Filtro (Todos).
    """
    esperar_dashboard_carregar()

    selecionar_opcao_dropdown(
        "Selecione a unidade",
        UNIDADE_DESTINO,
    )

    desmarcar_opcao_multiselect(
        "Selecione o(s) cliente(s)",
        CLIENTE_PARA_REMOVER,
    )

    selecionar_opcao_dropdown(
        "Selecione o intervalo de atualização",
        INTERVALO_ATUALIZACAO,
    )

    selecionar_opcao_dropdown(
        "Selecione a data base",
        DATA_BASE,
    )

    selecionar_opcao_dropdown(
        "Incluir backlog",
        INCLUIR_BACKLOG,
    )

    horainicial(
        hora_inicial
    )

    horafinal(
        hora_final
    )

    aplicar_filtro_todos()

# ============================================================
# PROCESSO COMPLETO
# ============================================================

def executar_processo() -> None:
    """
    Executa automaticamente:

    Pentaho > Browse Files > Public > dashboards >
    gestao-operacional > acompanhamento_separacao_v01.wcdf
    """
    global driver
    global processo_em_execucao

    try:
        # Antes de criar o WebDriver:
        # 1. fecha Chrome e Edge;
        # 2. aguarda 10 segundos;
        # 3. só então abre o navegador selecionado.
        preparar_navegadores()

        driver = criar_driver_navegador()

        aplicar_ajustes_apos_criar_driver()

        driver.get(
            URL
        )

        atualizar_status("Aguardando a página inicial...")
        aguardar_documento_pronto()
        pausa_adicional("Carregamento da página inicial")

        realizar_login()
        abrir_browse_files()

        abrir_e_selecionar_pasta(
            nome="Public",
            caminho=CAMINHO_PUBLIC,
        )

        abrir_e_selecionar_pasta(
            nome="dashboards",
            caminho=CAMINHO_DASHBOARDS,
        )

        abrir_e_selecionar_pasta(
            nome="gestao-operacional",
            caminho=CAMINHO_GESTAO,
        )

        # Apenas seleciona o WCDF com um clique simples.
        selecionar_arquivo(
            ARQUIVO_DESTINO
        )

        # Depois clica na ação "Open in a new window".
        abrir_arquivo_em_nova_janela(
            nome_arquivo=ARQUIVO_DESTINO,
            nome_acao=ABRIR_OUTRA_GUIA_ARQUIVO_DESTINO,
        )

        logger.info(
            "Agendamento recebido: %s | início=%s | fim=%s",
            NOME_AGENDAMENTO,
            HORA_INICIAL,
            HORA_FINAL,
        )

        # Configura automaticamente os filtros do dashboard.
        configurar_dashboard(
            hora_inicial=HORA_INICIAL,
            hora_final=HORA_FINAL,
        )

        # O filtro já foi aplicado. Agora coloca o relatório
        # na última janela do navegador em tela cheia.
        ativar_tela_cheia_final()

        logger.info(
            "Processo concluído com sucesso."
        )

        if janela.winfo_exists():
            janela.after(
                0,
                concluir_interface,
            )

    except TimeoutException as erro:
        logger.exception("Tempo limite excedido.")

        registrar_erro_txt(
            etapa="Timeout durante a automação",
            erro=erro,
        )

        atualizar_status("Tempo limite excedido.")
        salvar_diagnostico("timeout")

        exibir_erro(
            "Tempo limite",
            (
                "Uma espera configurada atingiu o prazo.\n\n"
                f"{erro}\n\n"
                "Um screenshot e o HTML foram salvos "
                "na pasta diagnosticos."
            ),
        )

    except Exception as erro:
        logger.exception("Erro durante a automação.")

        registrar_erro_txt(
            etapa="Erro geral durante a automação",
            erro=erro,
        )

        atualizar_status("Erro durante o processo.")
        salvar_diagnostico("erro")

        exibir_erro(
            "Erro",
            (
                f"{type(erro).__name__}: {erro}\n\n"
                "Um screenshot e o HTML foram salvos "
                "na pasta diagnosticos."
            ),
        )

    finally:
        processo_em_execucao = False


# ============================================================
# INÍCIO AUTOMÁTICO COM CONTAGEM REGRESSIVA
# ============================================================

def validar_configuracao() -> bool:
    """Valida navegador, credenciais e horários."""
    try:
        navegador = navegador_selecionado()

    except ValueError as erro:
        registrar_erro_txt(
            etapa="Validação do navegador",
            erro=erro,
        )

        messagebox.showwarning(
            "Navegador inválido",
            (
                f"{erro}\n\n"
                "Configure NAVEGADOR como CHROME ou EDGE."
            ),
        )
        return False

    if not USUARIO or USUARIO == "SEU_USUARIO":
        messagebox.showwarning(
            "Credenciais",
            "Defina PENTAHO_USUARIO ou altere USUARIO no arquivo.",
        )
        return False

    if not SENHA or SENHA == "SUA_SENHA":
        messagebox.showwarning(
            "Credenciais",
            "Defina PENTAHO_SENHA ou altere SENHA no arquivo.",
        )
        return False

    try:
        validar_data_hora(
            HORA_INICIAL,
            "Data/Hora inicial",
        )

        validar_data_hora(
            HORA_FINAL,
            "Data/Hora final",
        )

    except ValueError as erro:
        registrar_erro_txt(
            etapa="Validação das datas e horários",
            erro=erro,
        )

        messagebox.showwarning(
            "Horários inválidos",
            str(erro),
        )
        return False

    if not ESPERA_INDEFINIDA and TIMEOUT is None:
        messagebox.showwarning(
            "Espera inválida",
            "Configure PENTAHO_TIMEOUT_SEGUNDOS com um valor maior que zero.",
        )
        return False

    logger.info(
        "Configuração validada: navegador=%s | "
        "agendamento=%s | início=%s | fim=%s | "
        "fechar_telas=%s | ocultar_aviso=%s | f11_final=%s | "
        "espera_indefinida=%s | timeout=%s | intervalo=%.2fs",
        navegador,
        NOME_AGENDAMENTO,
        HORA_INICIAL,
        HORA_FINAL,
        FECHAR_TELAS,
        OCULTAR_AVISO_AUTOMACAO,
        ATIVAR_F11_NO_FINAL,
        ESPERA_INDEFINIDA,
        TIMEOUT,
        INTERVALO_VERIFICACAO,
    )

    return True


def iniciar_processo_automaticamente() -> None:
    """Inicia a automação em outra thread."""
    global processo_em_execucao

    if processo_em_execucao:
        return

    if not validar_configuracao():
        contador_variavel.set("Configuração pendente")
        status_variavel.set("Informe o usuário e a senha.")
        return

    EVENTO_CANCELAMENTO.clear()
    processo_em_execucao = True
    contador_variavel.set("Executando...")
    status_variavel.set("Iniciando o processo...")

    threading.Thread(
        target=executar_processo,
        daemon=True,
        name="processo-pentaho",
    ).start()


def executar_contagem_regressiva(segundos: int) -> None:
    """Exibe 5, 4, 3, 2, 1 e inicia automaticamente."""
    if segundos > 0:
        contador_variavel.set(f"Executando em: {segundos}")
        status_variavel.set(
            "A automação será iniciada automaticamente."
        )

        janela.after(
            1000,
            executar_contagem_regressiva,
            segundos - 1,
        )
        return

    iniciar_processo_automaticamente()


def ao_fechar_janela() -> None:
    """Confirma o fechamento durante uma execução."""
    if processo_em_execucao:
        fechar = messagebox.askyesno(
            "Processo em execução",
            (
                "A automação ainda está em execução.\n"
                "Deseja fechar somente esta janela?\n\n"
                f"O {nome_navegador_exibicao()} poderá continuar aberto."
            ),
        )

        if not fechar:
            return

    janela.destroy()


# ============================================================
# JANELA
# ============================================================

janela = tk.Tk()
janela.title(f"Automação Pentaho — {NAVEGADOR}")
janela.geometry("470x220")
janela.resizable(False, False)
janela.protocol("WM_DELETE_WINDOW", ao_fechar_janela)

contador_variavel = tk.StringVar(
    value=f"Executando em: {CONTAGEM_INICIAL}"
)

status_variavel = tk.StringVar(
    value="A automação será iniciada automaticamente."
)

contador_label = tk.Label(
    janela,
    textvariable=contador_variavel,
    font=("Arial", 22, "bold"),
)
contador_label.pack(pady=(42, 18))

status_label = tk.Label(
    janela,
    textvariable=status_variavel,
    font=("Arial", 10),
    wraplength=420,
)
status_label.pack(pady=8)

cancelar_label = tk.Label(
    janela,
    text="Feche a janela durante a contagem para cancelar.",
    font=("Arial", 9),
)
cancelar_label.pack(pady=8)


def main() -> int:
    """Inicia a aplicação e registra falhas de inicialização."""
    try:
        janela.after(
            250,
            executar_contagem_regressiva,
            CONTAGEM_INICIAL,
        )

        janela.mainloop()
        return 0

    except KeyboardInterrupt:
        logger.warning(
            "Automação interrompida pelo usuário."
        )
        return 130

    except Exception as erro:
        logger.exception(
            "Erro ao iniciar ou manter a interface."
        )

        registrar_erro_txt(
            etapa="Inicialização da aplicação",
            erro=erro,
        )

        return 1


if __name__ == "__main__":
    raise SystemExit(
        main()
    )