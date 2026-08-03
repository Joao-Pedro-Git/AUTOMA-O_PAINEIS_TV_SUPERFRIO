import logging
import os
import subprocess
import sys
import threading
import time
import tkinter as tk
import traceback
from datetime import datetime
from pathlib import Path
from tkinter import messagebox
import pyautogui as pg

from selenium import webdriver
from selenium.common.exceptions import (
    JavascriptException,
    NoSuchElementException,
    NoSuchFrameException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait


# ============================================================
# CONFIGURAÇÕES
# ============================================================

URL = "http://operationsreports.superfrio.com.br:8080/pentaho/Home"

USUARIO = os.getenv("PENTAHO_USUARIO", "JOAO.PEREIRA").strip()
SENHA = os.getenv("PENTAHO_SENHA", "jPereira!@#")


TIMEOUT = 90
INTERVALO_VERIFICACAO = 0.30
PAUSA_GLOBAL = 3.0
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
#   não fecha nenhum navegador existente e abre um novo Chrome.
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
        O Selenium abrirá uma nova janela do Chrome normalmente.
    """
    if not FECHAR_TELAS:
        logger.info(
            "FECHAR_TELAS=False: Chrome e Edge existentes "
            "serão preservados."
        )

        atualizar_status(
            "Navegadores existentes serão mantidos. "
            "Abrindo uma nova janela..."
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

def criar_opcoes_chrome():
    """
    Cria as opções do Chrome mantendo o comportamento atual.

    Quando OCULTAR_AVISO_AUTOMACAO=True, remove o switch que
    normalmente exibe a faixa de automação no topo da janela.
    """
    opcoes = webdriver.ChromeOptions()

    opcoes.add_argument(
        "--start-maximized"
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


def aplicar_ajustes_apos_criar_driver() -> None:
    """
    Aplica ajustes adicionais após criar o ChromeDriver.

    O script abaixo reduz a indicação JavaScript de automação.
    Caso o Chrome rejeite esse comando, a automação continua.
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
            "Ajustes para ocultar o aviso de automação aplicados."
        )

    except Exception as erro:
        logger.warning(
            "Não foi possível aplicar todos os ajustes "
            "de ocultação do aviso: %s",
            erro,
        )


def focar_ultima_janela_chrome() -> None:
    """Muda o Selenium para a última guia/janela disponível."""
    if driver is None:
        return

    janelas = driver.window_handles

    if not janelas:
        raise WebDriverException(
            "Nenhuma janela do Chrome está disponível."
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
        focar_ultima_janela_chrome()

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
        focar_ultima_janela_chrome()

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
    """Atualiza o status na thread principal do Tkinter."""
    logger.info(texto)

    if janela.winfo_exists():
        janela.after(
            0,
            lambda texto=texto: status_variavel.set(texto),
        )


def exibir_erro(titulo: str, mensagem: str) -> None:
    """Exibe um erro sem acessar o Tkinter pela thread do Selenium."""
    if janela.winfo_exists():
        janela.after(
            0,
            lambda titulo=titulo, mensagem=mensagem: messagebox.showerror(
                titulo,
                mensagem,
            ),
        )


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

def pausa_adicional(descricao: str) -> None:
    """Aplica a pausa global depois de uma grande etapa."""
    atualizar_status(
        f"{descricao} concluído. Aguardando {PAUSA_GLOBAL:.1f}s..."
    )
    time.sleep(PAUSA_GLOBAL)


def aguardar_documento_pronto(timeout: int = TIMEOUT) -> None:
    """Espera document.readyState ficar igual a complete."""
    WebDriverWait(
        driver,
        timeout,
        poll_frequency=INTERVALO_VERIFICACAO,
    ).until(
        lambda navegador: navegador.execute_script(
            "return document.readyState"
        ) == "complete"
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
    if profundidade > 10:
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
    timeout: int = TIMEOUT,
    descricao: str = "elemento",
):
    """Aguarda um elemento na página ou em qualquer iframe."""
    limite = time.monotonic() + timeout

    while time.monotonic() < limite:
        try:
            driver.switch_to.default_content()

            elemento = procurar_recursivamente_nos_frames(
                localizadores,
                clicavel,
            )

            if elemento is not None:
                logger.info("Elemento encontrado: %s", descricao)
                return elemento

        except WebDriverException:
            pass

        time.sleep(INTERVALO_VERIFICACAO)

    try:
        driver.switch_to.default_content()
    except WebDriverException:
        pass

    raise TimeoutException(
        f"Não foi possível encontrar: {descricao}"
    )


# ============================================================
# CLIQUES
# ============================================================

def rolar_ate_elemento(elemento) -> None:
    """Centraliza o elemento na área visível."""
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
    time.sleep(0.4)


def clicar(elemento) -> None:
    """Tenta clique normal, ActionChains e JavaScript."""
    rolar_ate_elemento(elemento)

    try:
        elemento.click()
        return
    except (
        StaleElementReferenceException,
        WebDriverException,
    ):
        pass

    try:
        ActionChains(driver).move_to_element(
            elemento
        ).pause(0.3).click().perform()
        return
    except (
        StaleElementReferenceException,
        WebDriverException,
    ):
        pass

    driver.execute_script(
        """
        arguments[0].dispatchEvent(
            new MouseEvent("mousedown", {
                bubbles: true,
                cancelable: true,
                view: window
            })
        );

        arguments[0].dispatchEvent(
            new MouseEvent("mouseup", {
                bubbles: true,
                cancelable: true,
                view: window
            })
        );

        arguments[0].click();
        """,
        elemento,
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
    """Realiza o login ou continua se a sessão já estiver autenticada."""
    atualizar_status("Verificando a tela de login...")

    try:
        campo_usuario = esperar_elemento(
            [
                (By.ID, "j_username"),
                (By.NAME, "j_username"),
                (By.ID, "username"),
                (By.NAME, "username"),
                (By.CSS_SELECTOR, "input[type='text']"),
            ],
            clicavel=True,
            timeout=12,
            descricao="campo de usuário",
        )
    except TimeoutException:
        atualizar_status(
            "Tela de login não encontrada; a sessão pode estar autenticada."
        )
        pausa_adicional("Verificação do login")
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

    atualizar_status("Realizando login...")
    clicar(botao_login)

    driver.switch_to.default_content()
    atualizar_status("Aguardando o Pentaho concluir o login...")
    aguardar_documento_pronto()
    pausa_adicional("Login")


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
    """Abre a perspectiva Browse Files."""
    atualizar_status("Aguardando o botão Browse Files...")

    try:
        botao = esperar_elemento(
            [
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
            ],
            timeout=30,
            descricao="botão Browse Files",
        )

        atualizar_status("Clicando em Browse Files...")
        clicar(botao)

    except TimeoutException:
        atualizar_status(
            "Botão não encontrado; abrindo Browse Files por JavaScript..."
        )

        if not abrir_browse_por_javascript():
            raise TimeoutException(
                "Não foi possível abrir a perspectiva Browse Files."
            )

    atualizar_status("Aguardando a árvore de pastas...")

    esperar_elemento(
        localizadores_pasta("Public", CAMINHO_PUBLIC),
        clicavel=False,
        timeout=TIMEOUT,
        descricao="pasta Public",
    )

    pausa_adicional("Abertura do Browse Files")


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
    timeout: int = 20,
) -> str | None:
    """
    Aguarda uma nova guia ou janela.

    Retorna o identificador da nova janela ou None quando o Pentaho
    reutilizar a própria guia.
    """
    try:
        WebDriverWait(
            driver,
            timeout,
            poll_frequency=INTERVALO_VERIFICACAO,
        ).until(
            lambda navegador: bool(
                set(navegador.window_handles)
                - janelas_anteriores
            )
        )

    except TimeoutException:
        return None

    novas_janelas = (
        set(driver.window_handles)
        - janelas_anteriores
    )

    if not novas_janelas:
        return None

    return novas_janelas.pop()


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
        # Algumas versões mostram a ação diretamente na barra
        # depois que o arquivo é selecionado.
        opcao = esperar_elemento(
            localizadores_acao_arquivo(
                nome_acao
            ),
            clicavel=True,
            timeout=5,
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
        timeout=20,
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
    if profundidade > 10:
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
    timeout: int = TIMEOUT,
    descricao: str = "elemento",
):
    """Aguarda um script retornar um elemento ou valor válido."""
    limite = time.monotonic() + timeout

    while time.monotonic() < limite:
        try:
            driver.switch_to.default_content()

            resultado = (
                procurar_resultado_script_recursivamente(
                    script,
                    argumentos,
                )
            )

            if resultado:
                logger.info(
                    "Elemento encontrado por script: %s",
                    descricao,
                )

                return resultado

        except WebDriverException:
            pass

        time.sleep(INTERVALO_VERIFICACAO)

    try:
        driver.switch_to.default_content()
    except WebDriverException:
        pass

    raise TimeoutException(
        f"Não foi possível encontrar: {descricao}"
    )


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
    timeout: int = TIMEOUT,
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


def localizar_linha_opcao_multiselect(
    opcao: str,
):
    """
    Localiza uma opção do multiselect pelo atributo title.

    O componente deste dashboard não utiliza um input checkbox
    tradicional. Cada item possui a seguinte estrutura:

        div.filter-item-body
            div.filter-item-selection-icon
            span.filter-item-only-this
            div.filter-item-label[title="MDLZ-MP"]
    """
    return esperar_elemento(
        [
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
        ],
        clicavel=False,
        timeout=TIMEOUT,
        descricao=f"opção do cliente {opcao}",
    )


def obter_corpo_opcao_multiselect(
    rotulo_opcao,
):
    """Retorna a linha filter-item-body da opção."""
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

    except NoSuchElementException as erro:
        raise TimeoutException(
            "Não foi possível localizar a linha da opção "
            f"{rotulo_opcao.text!r}."
        ) from erro


def obter_icone_selecao_multiselect(
    corpo_opcao,
):
    """Localiza o ícone usado para marcar ou desmarcar a opção."""
    try:
        return corpo_opcao.find_element(
            By.CSS_SELECTOR,
            ".filter-item-selection-icon",
        )

    except NoSuchElementException as erro:
        raise TimeoutException(
            "O ícone de seleção do cliente não foi encontrado."
        ) from erro


def localizar_botao_apply_multiselect(
    timeout: int = TIMEOUT,
):
    """
    Aguarda o botão Apply ficar habilitado.

    Quando uma alteração é realizada, o componente adiciona
    a classe 'dirty' ao botão:

        button.filter-btn-apply.dirty
    """
    return esperar_elemento(
        [
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
        ],
        clicavel=True,
        timeout=timeout,
        descricao="botão Apply habilitado do multiselect",
    )


def aguardar_multiselect_fechar(
    timeout: int = 15,
) -> None:
    """
    Aguarda o painel do multiselect fechar após o Apply.

    A ausência dessa confirmação não interrompe o restante
    da automação, pois algumas versões mantêm o painel aberto.
    """
    try:
        WebDriverWait(
            driver,
            timeout,
            poll_frequency=INTERVALO_VERIFICACAO,
        ).until(
            lambda navegador: not any(
                elemento.is_displayed()
                for elemento in navegador.find_elements(
                    By.CSS_SELECTOR,
                    "button.filter-btn-apply",
                )
            )
        )

    except (
        TimeoutException,
        StaleElementReferenceException,
        WebDriverException,
    ):
        logger.warning(
            "O painel de clientes não confirmou o fechamento, "
            "mas o botão Apply já foi acionado."
        )


def desmarcar_opcao_multiselect(
    rotulo: str,
    opcao: str,
) -> None:
    """
    Abre o seletor de clientes, desmarca somente a opção informada
    e clica no botão Apply habilitado.

    Esta implementação é específica para o DOM observado:

        div.filter-item-label[title="MDLZ-MP"]
        div.filter-item-selection-icon
        button.filter-btn-apply.dirty
    """
    atualizar_status(
        f"Abrindo {rotulo}..."
    )

    controle = localizar_controle_por_rotulo(
        rotulo
    )

    clicar(controle)

    # Aguarda a animação de abertura do painel.
    time.sleep(0.8)

    atualizar_status(
        f"Localizando somente o cliente {opcao}..."
    )

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

    # Clica diretamente no ícone azul de seleção.
    # Não clica em "All", "Only" nem em MDLZ-PA.
    clicar(icone_selecao)

    # O botão Apply deve ganhar a classe "dirty" depois da mudança.
    atualizar_status(
        "Aguardando o botão Apply ser habilitado..."
    )

    botao_apply = localizar_botao_apply_multiselect(
        timeout=TIMEOUT
    )

    atualizar_status(
        "Aplicando a remoção de MDLZ-MP..."
    )

    clicar(botao_apply)

    aguardar_multiselect_fechar(
        timeout=15
    )

    time.sleep(0.8)

    logger.info(
        "Cliente desmarcado e aplicado com sucesso: %s",
        opcao,
    )

    pausa_adicional(
        f"Remoção do cliente {opcao}"
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


def localizar_input_data_hora(
    painel_id: str,
    input_id: str,
    descricao: str,
):
    """
    Localiza o input de data usando os IDs exatos do dashboard.

    Exemplos conhecidos:

        #panelFilterDataInicial
        #render_ticDataInicial

        #panelFilterDataFinal
        #render_ticDataFinal

    A busca continua funcionando caso o relatório esteja dentro
    de um iframe.
    """
    return esperar_elemento(
        [
            (
                By.CSS_SELECTOR,
                (
                    f"div#{painel_id} "
                    f"input#{input_id}"
                ),
            ),
            (
                By.CSS_SELECTOR,
                (
                    f"#{painel_id} "
                    f"input#{input_id}"
                ),
            ),
            (
                By.XPATH,
                (
                    f"//div[@id='{painel_id}']"
                    f"//input[@id='{input_id}']"
                ),
            ),
            (
                By.CSS_SELECTOR,
                (
                    f"#{painel_id} "
                    "input[type='text']"
                ),
            ),
        ],
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
    Localiza novamente o campo e confirma que o valor foi aplicado.

    O DOM do dashboard pode ser recriado após os eventos.
    """
    def valor_foi_aplicado(_):
        try:
            campo = localizar_input_data_hora(
                painel_id=painel_id,
                input_id=input_id,
                descricao=descricao,
            )

            valor_atual = (
                campo.get_attribute("value") or ""
            ).strip()

            if valor_atual == valor_esperado:
                return campo

        except (
            TimeoutException,
            StaleElementReferenceException,
            WebDriverException,
        ):
            return False

        return False

    WebDriverWait(
        driver,
        15,
        poll_frequency=INTERVALO_VERIFICACAO,
    ).until(
        valor_foi_aplicado
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
        # 3. só então abre um novo Chrome.
        preparar_navegadores()

        atualizar_status("Abrindo o Chrome...")

        opcoes = criar_opcoes_chrome()

        driver = webdriver.Chrome(
            options=opcoes
        )

        aplicar_ajustes_apos_criar_driver()

        driver.get(
            URL
        )

        atualizar_status("Aguardando a página inicial...")
        aguardar_documento_pronto()
        time.sleep(PAUSA_GLOBAL)

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
        # na última janela do Chrome em tela cheia.
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
                "Um elemento não apareceu dentro do prazo.\n\n"
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
    """Valida as credenciais antes de iniciar o navegador."""
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

    logger.info(
        "Configuração validada: agendamento=%s | "
        "início=%s | fim=%s | fechar_telas=%s | "
        "ocultar_aviso=%s | f11_final=%s",
        NOME_AGENDAMENTO,
        HORA_INICIAL,
        HORA_FINAL,
        FECHAR_TELAS,
        OCULTAR_AVISO_AUTOMACAO,
        ATIVAR_F11_NO_FINAL,
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
                "O Chrome poderá continuar aberto."
            ),
        )

        if not fechar:
            return

    janela.destroy()


# ============================================================
# JANELA
# ============================================================

janela = tk.Tk()
janela.title("Automação Pentaho")
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