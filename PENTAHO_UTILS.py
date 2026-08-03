"""
PENTAHO_UTILS.py

Utilitário reutilizável para:

1. Abrir o Chrome.
2. Acessar o Pentaho.
3. Efetuar login.
4. Abrir Browse Files.
5. Navegar por uma sequência dinâmica de pastas.
6. Localizar um arquivo.
7. Abrir o arquivo normalmente ou em uma nova janela.

Este módulo NÃO configura os filtros internos do dashboard.
Ele termina com o navegador aberto no relatório solicitado.

Exemplo rápido:

    from PENTAHO_UTILS import abrir_pentaho

    driver = abrir_pentaho(
        caminho_pasta="/public/dashboards/gestao-operacional",
        arquivo="acompanhamento_separacao_v01.wcdf",
        abrir_new_window=True,
    )

Também é possível informar as pastas explicitamente:

    from PENTAHO_UTILS import PastaPentaho, abrir_pentaho

    driver = abrir_pentaho(
        pastas=[
            PastaPentaho("Public", "/public"),
            PastaPentaho("dashboards", "/public/dashboards"),
            PastaPentaho(
                "gestao-operacional",
                "/public/dashboards/gestao-operacional",
            ),
        ],
        arquivo="acompanhamento_separacao_v01.wcdf",
        abrir_new_window=False,
    )
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
import traceback

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Final, TypeAlias

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
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support.ui import WebDriverWait


# ============================================================
# TIPOS
# ============================================================

StatusCallback: TypeAlias = Callable[[str], None]
PastaEntrada: TypeAlias = "PastaPentaho | tuple[str, str]"


# ============================================================
# CONSTANTES
# ============================================================

URL_PENTAHO_PADRAO: Final[str] = (
    "http://operationsreports.superfrio.com.br:8080/pentaho/Home"
)

ACAO_NOVA_JANELA_PADRAO: Final[str] = "Open in a new window"

PROCESSOS_NAVEGADORES_PADRAO: Final[tuple[str, ...]] = (
    "chrome.exe",
    "msedge.exe",
    "chromedriver.exe",
    "msedgedriver.exe",
)


# ============================================================
# MODELOS
# ============================================================

@dataclass(frozen=True, slots=True)
class PastaPentaho:
    """
    Representa uma pasta exibida na árvore do Pentaho.

    nome:
        Texto visível da pasta.

    caminho:
        Atributo path do elemento no DOM.
    """

    nome: str
    caminho: str

    def __post_init__(self) -> None:
        nome = self.nome.strip()
        caminho = normalizar_caminho(self.caminho)

        if not nome:
            raise ValueError(
                "O nome da pasta não pode ficar vazio."
            )

        if caminho == "/":
            raise ValueError(
                "O caminho da pasta não pode ser somente '/'."
            )

        object.__setattr__(
            self,
            "nome",
            nome,
        )

        object.__setattr__(
            self,
            "caminho",
            caminho,
        )


@dataclass(slots=True)
class PentahoConfig:
    """Configurações gerais da navegação no Pentaho."""

    url: str = URL_PENTAHO_PADRAO

    usuario: str = field(
        default_factory=lambda: os.getenv(
            "PENTAHO_USUARIO",
            "",
        ).strip()
    )

    senha: str = field(
        default_factory=lambda: os.getenv(
            "PENTAHO_SENHA",
            "",
        )
    )

    timeout: int = 90
    intervalo_verificacao: float = 0.30
    pausa_global: float = 3.0

    maximizar_chrome: bool = True
    manter_chrome_aberto: bool = True

    fechar_telas: bool = False
    espera_apos_fechar_telas: int = 10

    ocultar_aviso_automacao: bool = True

    acao_nova_janela: str = ACAO_NOVA_JANELA_PADRAO
    timeout_nova_janela: int = 20

    processos_navegadores: tuple[str, ...] = (
        PROCESSOS_NAVEGADORES_PADRAO
    )

    salvar_diagnostico_em_erro: bool = True

    def validar(self) -> None:
        """Valida as configurações antes de abrir o navegador."""
        if not self.url.strip():
            raise ValueError(
                "A URL do Pentaho não foi informada."
            )

        if not self.url.lower().startswith(
            (
                "http://",
                "https://",
            )
        ):
            raise ValueError(
                "A URL do Pentaho deve começar com "
                "http:// ou https://."
            )

        if not self.usuario:
            raise ValueError(
                "O usuário do Pentaho não foi configurado. "
                "Defina PENTAHO_USUARIO ou informe PentahoConfig.usuario."
            )

        if not self.senha:
            raise ValueError(
                "A senha do Pentaho não foi configurada. "
                "Defina PENTAHO_SENHA ou informe PentahoConfig.senha."
            )

        if self.timeout <= 0:
            raise ValueError(
                "timeout precisa ser maior que zero."
            )

        if self.intervalo_verificacao <= 0:
            raise ValueError(
                "intervalo_verificacao precisa ser maior que zero."
            )

        if self.pausa_global < 0:
            raise ValueError(
                "pausa_global não pode ser negativa."
            )

        if self.espera_apos_fechar_telas < 0:
            raise ValueError(
                "espera_apos_fechar_telas não pode ser negativa."
            )

        if self.timeout_nova_janela <= 0:
            raise ValueError(
                "timeout_nova_janela precisa ser maior que zero."
            )

        if not self.acao_nova_janela.strip():
            raise ValueError(
                "acao_nova_janela não pode ficar vazia."
            )


# ============================================================
# CAMINHOS DO PROJETO
# ============================================================

def obter_pasta_projeto() -> Path:
    """
    Retorna a pasta permanente do projeto.

    Quando PENTAHO_UTILS.py estiver em utils/, usa a pasta pai.
    Quando compilado, usa a pasta do executável.
    """
    if getattr(
        sys,
        "frozen",
        False,
    ):
        return Path(
            sys.executable
        ).resolve().parent

    pasta_modulo = Path(
        __file__
    ).resolve().parent

    if pasta_modulo.name.lower() == "utils":
        return pasta_modulo.parent

    return pasta_modulo


PASTA_PROJETO: Final[Path] = obter_pasta_projeto()
PASTA_DIAGNOSTICOS: Final[Path] = (
    PASTA_PROJETO / "diagnosticos"
)
ARQUIVO_ERRO: Final[Path] = (
    PASTA_PROJETO / "erro.txt"
)


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format=(
        "%(asctime)s | %(levelname)s | "
        "%(name)s | %(message)s"
    ),
    datefmt="%d/%m/%Y %H:%M:%S",
)

logger = logging.getLogger(
    "pentaho-utils"
)

_erro_lock = threading.Lock()


def registrar_erro_txt(
    *,
    etapa: str,
    erro: BaseException | str,
    traceback_texto: str | None = None,
) -> None:
    """Acrescenta uma ocorrência ao arquivo erro.txt."""
    momento = datetime.now().strftime(
        "%d/%m/%Y %H:%M:%S"
    )

    if isinstance(
        erro,
        BaseException,
    ):
        tipo = type(
            erro
        ).__name__

        mensagem = str(
            erro
        )
    else:
        tipo = "Erro"
        mensagem = str(
            erro
        )

    if traceback_texto is None:
        traceback_texto = traceback.format_exc()

        if traceback_texto.strip() == "NoneType: None":
            traceback_texto = ""

    bloco = (
        "\n"
        + "=" * 80
        + "\n"
        + f"DATA/HORA: {momento}\n"
        + "ORIGEM: PENTAHO_UTILS.py\n"
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

    bloco += (
        "=" * 80
        + "\n"
    )

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
                arquivo.write(
                    bloco
                )

                arquivo.flush()

                try:
                    os.fsync(
                        arquivo.fileno()
                    )
                except OSError:
                    pass

    except OSError:
        logger.exception(
            "Não foi possível gravar %s.",
            ARQUIVO_ERRO,
        )


# ============================================================
# FUNÇÕES DE APOIO PARA PASTAS
# ============================================================

def normalizar_caminho(
    caminho: str,
) -> str:
    """Normaliza um caminho do repositório Pentaho."""
    partes = [
        parte.strip()
        for parte in caminho.replace(
            "\\",
            "/",
        ).split("/")
        if parte.strip()
    ]

    return (
        "/"
        + "/".join(
            partes
        )
        if partes
        else "/"
    )


def nome_visivel_da_pasta(
    parte: str,
    indice: int,
) -> str:
    """
    Converte uma parte do caminho no nome visível esperado.

    A pasta /public costuma aparecer como Public.
    """
    if indice == 0 and parte.lower() == "public":
        return "Public"

    return parte


def criar_pastas_por_caminho(
    caminho_pasta: str,
) -> list[PastaPentaho]:
    """
    Transforma:

        /public/dashboards/gestao-operacional

    em:

        PastaPentaho("Public", "/public")
        PastaPentaho("dashboards", "/public/dashboards")
        PastaPentaho(
            "gestao-operacional",
            "/public/dashboards/gestao-operacional",
        )
    """
    caminho_normalizado = normalizar_caminho(
        caminho_pasta
    )

    partes = [
        parte
        for parte in caminho_normalizado.split("/")
        if parte
    ]

    if not partes:
        raise ValueError(
            "O caminho da pasta não contém nenhuma pasta."
        )

    resultado: list[PastaPentaho] = []
    caminho_acumulado = ""

    for indice, parte in enumerate(
        partes
    ):
        caminho_acumulado += (
            "/"
            + parte
        )

        resultado.append(
            PastaPentaho(
                nome=nome_visivel_da_pasta(
                    parte,
                    indice,
                ),
                caminho=caminho_acumulado,
            )
        )

    return resultado


def normalizar_pastas(
    *,
    caminho_pasta: str | None,
    pastas: Sequence[PastaEntrada] | None,
) -> list[PastaPentaho]:
    """Valida e normaliza as duas formas de informar as pastas."""
    if caminho_pasta and pastas:
        raise ValueError(
            "Informe somente caminho_pasta ou pastas, não os dois."
        )

    if caminho_pasta:
        return criar_pastas_por_caminho(
            caminho_pasta
        )

    if not pastas:
        raise ValueError(
            "Informe caminho_pasta ou uma lista em pastas."
        )

    resultado: list[PastaPentaho] = []

    for item in pastas:
        if isinstance(
            item,
            PastaPentaho,
        ):
            resultado.append(
                item
            )
            continue

        if (
            isinstance(
                item,
                tuple,
            )
            and len(
                item
            ) == 2
        ):
            nome, caminho = item

            resultado.append(
                PastaPentaho(
                    str(
                        nome
                    ),
                    str(
                        caminho
                    ),
                )
            )
            continue

        raise TypeError(
            "Cada item de pastas deve ser PastaPentaho "
            "ou uma tupla (nome, caminho)."
        )

    return resultado


# ============================================================
# NAVEGADOR
# ============================================================

class PentahoNavigator:
    """
    Controla uma sessão Selenium voltada à navegação no Pentaho.

    O objeto pode receber um driver existente ou criar um novo.
    """

    def __init__(
        self,
        config: PentahoConfig | None = None,
        *,
        driver: WebDriver | None = None,
        status_callback: StatusCallback | None = None,
    ) -> None:
        self.config = (
            config
            if config is not None
            else PentahoConfig()
        )

        self.config.validar()

        self.driver = driver
        self.status_callback = status_callback
        self._driver_criado_pelo_utilitario = (
            driver is None
        )

    # ========================================================
    # STATUS
    # ========================================================

    def status(
        self,
        texto: str,
    ) -> None:
        """Registra e encaminha o status para uma interface externa."""
        logger.info(
            texto
        )

        if self.status_callback is None:
            return

        try:
            self.status_callback(
                texto
            )
        except Exception:
            logger.exception(
                "O status_callback apresentou uma falha."
            )

    def pausa(
        self,
        descricao: str,
    ) -> None:
        """Aplica a pausa adicional configurada."""
        if self.config.pausa_global <= 0:
            return

        self.status(
            f"{descricao} concluído. "
            f"Aguardando {self.config.pausa_global:.1f}s..."
        )

        time.sleep(
            self.config.pausa_global
        )

    # ========================================================
    # CHROME E PROCESSOS
    # ========================================================

    def processo_windows_esta_ativo(
        self,
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

            return (
                nome_processo.lower()
                in (
                    resultado.stdout
                    or ""
                ).lower()
            )

        except (
            OSError,
            subprocess.SubprocessError,
        ) as erro:
            registrar_erro_txt(
                etapa=(
                    "Verificar processo "
                    f"{nome_processo}"
                ),
                erro=erro,
            )
            return False

    def encerrar_processo_windows(
        self,
        nome_processo: str,
    ) -> bool:
        """Encerra um processo e seus filhos no Windows."""
        if os.name != "nt":
            return True

        if not self.processo_windows_esta_ativo(
            nome_processo
        ):
            return True

        try:
            subprocess.run(
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

        except (
            OSError,
            subprocess.SubprocessError,
        ) as erro:
            registrar_erro_txt(
                etapa=(
                    "Encerrar processo "
                    f"{nome_processo}"
                ),
                erro=erro,
            )
            return False

        time.sleep(
            0.5
        )

        return not self.processo_windows_esta_ativo(
            nome_processo
        )

    def preparar_navegadores(
        self,
    ) -> None:
        """
        Fecha Chrome/Edge somente quando fechar_telas=True.

        Quando False, preserva as sessões existentes e cria
        uma nova janela do Chrome.
        """
        if not self.config.fechar_telas:
            self.status(
                "Navegadores existentes serão preservados."
            )
            return

        self.status(
            "Fechando Chrome e Edge..."
        )

        for nome_processo in (
            self.config.processos_navegadores
        ):
            self.encerrar_processo_windows(
                nome_processo
            )

        for restante in range(
            self.config.espera_apos_fechar_telas,
            0,
            -1,
        ):
            self.status(
                "Chrome e Edge fechados. "
                f"Iniciando em {restante}s..."
            )

            time.sleep(
                1
            )

    def criar_opcoes_chrome(
        self,
    ) -> webdriver.ChromeOptions:
        """Cria as opções usadas pelo ChromeDriver."""
        opcoes = webdriver.ChromeOptions()

        if self.config.maximizar_chrome:
            opcoes.add_argument(
                "--start-maximized"
            )

        if self.config.manter_chrome_aberto:
            opcoes.add_experimental_option(
                "detach",
                True,
            )

        if self.config.ocultar_aviso_automacao:
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

    def iniciar_driver(
        self,
    ) -> WebDriver:
        """Cria o ChromeDriver quando ainda não existir."""
        if self.driver is not None:
            try:
                self.driver.current_url
                return self.driver
            except WebDriverException:
                self.driver = None

        self.preparar_navegadores()

        self.status(
            "Abrindo o Chrome..."
        )

        self.driver = webdriver.Chrome(
            options=self.criar_opcoes_chrome()
        )

        if self.config.ocultar_aviso_automacao:
            try:
                self.driver.execute_cdp_cmd(
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
            except WebDriverException:
                logger.warning(
                    "O Chrome ignorou um ajuste de automação."
                )

        return self.driver

    # ========================================================
    # DIAGNÓSTICOS
    # ========================================================

    def salvar_diagnostico(
        self,
        nome: str = "erro",
    ) -> None:
        """Salva screenshot e HTML da página atual."""
        if self.driver is None:
            return

        try:
            PASTA_DIAGNOSTICOS.mkdir(
                parents=True,
                exist_ok=True,
            )

            timestamp = datetime.now().strftime(
                "%Y%m%d_%H%M%S"
            )

            screenshot = (
                PASTA_DIAGNOSTICOS
                / f"{nome}_{timestamp}.png"
            )

            html = (
                PASTA_DIAGNOSTICOS
                / f"{nome}_{timestamp}.html"
            )

            self.driver.save_screenshot(
                str(
                    screenshot
                )
            )

            html.write_text(
                self.driver.page_source,
                encoding="utf-8",
            )

            logger.info(
                "Diagnóstico salvo em %s.",
                PASTA_DIAGNOSTICOS,
            )

        except Exception as erro:
            registrar_erro_txt(
                etapa="Salvar diagnóstico",
                erro=erro,
            )

    # ========================================================
    # ESPERAS E FRAMES
    # ========================================================

    def aguardar_documento_pronto(
        self,
        timeout: int | None = None,
    ) -> None:
        """Aguarda document.readyState=complete."""
        driver = self._driver_obrigatorio()

        WebDriverWait(
            driver,
            timeout or self.config.timeout,
            poll_frequency=(
                self.config.intervalo_verificacao
            ),
        ).until(
            lambda navegador: navegador.execute_script(
                "return document.readyState"
            ) == "complete"
        )

    def elemento_esta_disponivel(
        self,
        elemento: WebElement,
        clicavel: bool,
    ) -> bool:
        """Verifica se um elemento está visível e habilitado."""
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

    def procurar_no_contexto_atual(
        self,
        localizadores: Sequence[tuple[str, str]],
        clicavel: bool,
    ) -> WebElement | None:
        """Procura no documento ou frame atual."""
        driver = self._driver_obrigatorio()

        for tipo, seletor in localizadores:
            try:
                elementos = driver.find_elements(
                    tipo,
                    seletor,
                )
            except WebDriverException:
                continue

            for elemento in elementos:
                if self.elemento_esta_disponivel(
                    elemento,
                    clicavel,
                ):
                    return elemento

        return None

    def procurar_recursivamente_nos_frames(
        self,
        localizadores: Sequence[tuple[str, str]],
        clicavel: bool,
        profundidade: int = 0,
    ) -> WebElement | None:
        """Procura um elemento na página e em todos os iframes."""
        driver = self._driver_obrigatorio()

        if profundidade > 10:
            return None

        elemento = self.procurar_no_contexto_atual(
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
            entrou = False

            try:
                driver.switch_to.frame(
                    frame
                )

                entrou = True

                elemento = self.procurar_recursivamente_nos_frames(
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

            if entrou:
                try:
                    driver.switch_to.parent_frame()
                except WebDriverException:
                    driver.switch_to.default_content()

        return None

    def esperar_elemento(
        self,
        localizadores: Sequence[tuple[str, str]],
        *,
        clicavel: bool = True,
        timeout: int | None = None,
        descricao: str = "elemento",
    ) -> WebElement:
        """Aguarda um elemento na página ou em qualquer iframe."""
        driver = self._driver_obrigatorio()

        limite = (
            time.monotonic()
            + (
                timeout
                if timeout is not None
                else self.config.timeout
            )
        )

        while time.monotonic() < limite:
            try:
                driver.switch_to.default_content()

                elemento = self.procurar_recursivamente_nos_frames(
                    localizadores,
                    clicavel,
                )

                if elemento is not None:
                    logger.info(
                        "Elemento encontrado: %s",
                        descricao,
                    )
                    return elemento

            except WebDriverException:
                pass

            time.sleep(
                self.config.intervalo_verificacao
            )

        try:
            driver.switch_to.default_content()
        except WebDriverException:
            pass

        raise TimeoutException(
            f"Não foi possível encontrar: {descricao}"
        )

    # ========================================================
    # CLIQUES
    # ========================================================

    def rolar_ate_elemento(
        self,
        elemento: WebElement,
    ) -> None:
        """Centraliza o elemento na janela."""
        driver = self._driver_obrigatorio()

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

        time.sleep(
            0.4
        )

    def clicar(
        self,
        elemento: WebElement,
    ) -> None:
        """Tenta clique normal, ActionChains e JavaScript."""
        driver = self._driver_obrigatorio()

        self.rolar_ate_elemento(
            elemento
        )

        try:
            elemento.click()
            return
        except (
            StaleElementReferenceException,
            WebDriverException,
        ):
            pass

        try:
            ActionChains(
                driver
            ).move_to_element(
                elemento
            ).pause(
                0.3
            ).click().perform()

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

    def clicar_duas_vezes(
        self,
        elemento: WebElement,
    ) -> None:
        """Executa duplo clique com fallback JavaScript."""
        driver = self._driver_obrigatorio()

        self.rolar_ate_elemento(
            elemento
        )

        try:
            ActionChains(
                driver
            ).move_to_element(
                elemento
            ).pause(
                0.3
            ).double_click().perform()

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

    def clicar_com_botao_direito(
        self,
        elemento: WebElement,
    ) -> None:
        """Abre o menu de contexto do arquivo."""
        driver = self._driver_obrigatorio()

        self.rolar_ate_elemento(
            elemento
        )

        try:
            ActionChains(
                driver
            ).move_to_element(
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

    # ========================================================
    # LOGIN
    # ========================================================

    def realizar_login(
        self,
    ) -> None:
        """Realiza o login ou continua quando já autenticado."""
        driver = self._driver_obrigatorio()

        self.status(
            "Verificando a tela de login..."
        )

        try:
            campo_usuario = self.esperar_elemento(
                [
                    (
                        By.ID,
                        "j_username",
                    ),
                    (
                        By.NAME,
                        "j_username",
                    ),
                    (
                        By.ID,
                        "username",
                    ),
                    (
                        By.NAME,
                        "username",
                    ),
                    (
                        By.CSS_SELECTOR,
                        "input[type='text']",
                    ),
                ],
                clicavel=True,
                timeout=12,
                descricao="campo de usuário",
            )

        except TimeoutException:
            self.status(
                "Tela de login não encontrada; "
                "a sessão pode estar autenticada."
            )
            return

        campo_senha = self.esperar_elemento(
            [
                (
                    By.ID,
                    "j_password",
                ),
                (
                    By.NAME,
                    "j_password",
                ),
                (
                    By.ID,
                    "password",
                ),
                (
                    By.NAME,
                    "password",
                ),
                (
                    By.CSS_SELECTOR,
                    "input[type='password']",
                ),
            ],
            descricao="campo de senha",
        )

        campo_usuario.clear()

        campo_usuario.send_keys(
            self.config.usuario
        )

        campo_senha.clear()

        campo_senha.send_keys(
            self.config.senha
        )

        botao_login = self.esperar_elemento(
            [
                (
                    By.ID,
                    "loginbtn",
                ),
                (
                    By.NAME,
                    "loginbtn",
                ),
                (
                    By.CSS_SELECTOR,
                    "button[type='submit']",
                ),
                (
                    By.CSS_SELECTOR,
                    "input[type='submit']",
                ),
            ],
            descricao="botão de login",
        )

        self.status(
            "Realizando login..."
        )

        self.clicar(
            botao_login
        )

        driver.switch_to.default_content()

        self.aguardar_documento_pronto()

        self.pausa(
            "Login"
        )

    # ========================================================
    # BROWSE FILES
    # ========================================================

    def abrir_browse_por_javascript(
        self,
    ) -> bool:
        """Executa mantle_setPerspective diretamente."""
        driver = self._driver_obrigatorio()

        driver.switch_to.default_content()

        try:
            return bool(
                driver.execute_script(
                    """
                    if (
                        typeof window.mantle_setPerspective
                        === "function"
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

    def abrir_browse_files(
        self,
        primeira_pasta: PastaPentaho,
    ) -> None:
        """Abre a perspectiva Browse Files."""
        self.status(
            "Aguardando Browse Files..."
        )

        try:
            botao = self.esperar_elemento(
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
                        (
                            "//*[contains("
                            "normalize-space(), "
                            "'Browse Files'"
                            ")]"
                        ),
                    ),
                ],
                timeout=30,
                descricao="botão Browse Files",
            )

            self.clicar(
                botao
            )

        except TimeoutException:
            if not self.abrir_browse_por_javascript():
                raise TimeoutException(
                    "Não foi possível abrir Browse Files."
                )

        self.status(
            "Aguardando a árvore de pastas..."
        )

        self.esperar_elemento(
            self.localizadores_pasta(
                primeira_pasta
            ),
            clicavel=False,
            descricao=(
                f"pasta {primeira_pasta.nome}"
            ),
        )

        self.pausa(
            "Abertura do Browse Files"
        )

    # ========================================================
    # PASTAS
    # ========================================================

    def localizadores_pasta(
        self,
        pasta: PastaPentaho,
    ) -> list[tuple[str, str]]:
        """Cria seletores para uma pasta."""
        return [
            (
                By.CSS_SELECTOR,
                (
                    "div.folder"
                    f"[path='{pasta.caminho}']"
                ),
            ),
            (
                By.CSS_SELECTOR,
                f"[path='{pasta.caminho}']",
            ),
            (
                By.XPATH,
                (
                    "//div["
                    "contains("
                    "concat(' ', normalize-space(@class), ' '),"
                    "' folder '"
                    ") "
                    f"and @path='{pasta.caminho}'"
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
                    f"and normalize-space()='{pasta.nome}'"
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

    def localizar_titulo_da_pasta(
        self,
        elemento_pasta: WebElement,
        nome: str,
    ) -> WebElement:
        """Localiza o texto clicável direto de uma pasta."""
        for tipo, seletor in (
            (
                By.XPATH,
                (
                    "./div[contains(@class,'element')]"
                    "/div[contains(@class,'title')]"
                ),
            ),
            (
                By.CSS_SELECTOR,
                ":scope > .element > .title",
            ),
        ):
            try:
                elementos = elemento_pasta.find_elements(
                    tipo,
                    seletor,
                )

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
            return elemento_pasta.find_element(
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

    def localizar_expansor_da_pasta(
        self,
        elemento_pasta: WebElement,
    ) -> WebElement | None:
        """Localiza a seta usada para expandir uma pasta."""
        for tipo, seletor in (
            (
                By.XPATH,
                (
                    "./div[contains(@class,'element')]"
                    "/div[contains(@class,'expandCollapse')]"
                ),
            ),
            (
                By.CSS_SELECTOR,
                ":scope > .element > .expandCollapse",
            ),
        ):
            try:
                elementos = elemento_pasta.find_elements(
                    tipo,
                    seletor,
                )

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
        self,
        pasta: PastaPentaho,
    ) -> None:
        """Expande e seleciona uma pasta."""
        self.status(
            f"Aguardando a pasta {pasta.nome}..."
        )

        elemento_pasta = self.esperar_elemento(
            self.localizadores_pasta(
                pasta
            ),
            clicavel=False,
            descricao=f"pasta {pasta.nome}",
        )

        self.rolar_ate_elemento(
            elemento_pasta
        )

        try:
            classes = (
                elemento_pasta.get_attribute(
                    "class"
                )
                or ""
            ).split()

        except StaleElementReferenceException:
            classes = []

        if "open" not in classes:
            expansor = self.localizar_expansor_da_pasta(
                elemento_pasta
            )

            if expansor is not None:
                self.status(
                    f"Expandindo a pasta {pasta.nome}..."
                )

                self.clicar(
                    expansor
                )

                time.sleep(
                    1
                )

        elemento_pasta = self.esperar_elemento(
            self.localizadores_pasta(
                pasta
            ),
            clicavel=False,
            descricao=(
                f"pasta {pasta.nome} após expansão"
            ),
        )

        titulo = self.localizar_titulo_da_pasta(
            elemento_pasta,
            pasta.nome,
        )

        self.status(
            f"Selecionando a pasta {pasta.nome}..."
        )

        self.clicar(
            titulo
        )

        self.pausa(
            f"Seleção da pasta {pasta.nome}"
        )

    # ========================================================
    # ARQUIVOS
    # ========================================================

    def localizadores_arquivo(
        self,
        nome_arquivo: str,
    ) -> list[tuple[str, str]]:
        """Cria seletores para o arquivo na coluna Files."""
        return [
            (
                By.XPATH,
                (
                    "//*[normalize-space()="
                    f"'{nome_arquivo}']"
                ),
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

    def obter_elemento_clicavel_do_arquivo(
        self,
        elemento: WebElement,
    ) -> WebElement:
        """Retorna o container clicável do arquivo."""
        for tipo, seletor in (
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
        ):
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

    def localizar_arquivo_clicavel(
        self,
        nome_arquivo: str,
    ) -> WebElement:
        """Localiza novamente o arquivo e seu container."""
        elemento = self.esperar_elemento(
            self.localizadores_arquivo(
                nome_arquivo
            ),
            clicavel=True,
            descricao=f"arquivo {nome_arquivo}",
        )

        return self.obter_elemento_clicavel_do_arquivo(
            elemento
        )

    def selecionar_arquivo(
        self,
        nome_arquivo: str,
    ) -> None:
        """Seleciona o arquivo com somente um clique."""
        self.status(
            f"Selecionando {nome_arquivo}..."
        )

        arquivo = self.localizar_arquivo_clicavel(
            nome_arquivo
        )

        self.clicar(
            arquivo
        )

        time.sleep(
            0.8
        )

        self.pausa(
            "Seleção do arquivo"
        )

    def abrir_arquivo_normalmente(
        self,
        nome_arquivo: str,
    ) -> None:
        """
        Abre o arquivo com duplo clique na própria guia.

        Usado quando abrir_new_window=False.
        """
        self.status(
            f"Abrindo {nome_arquivo} na guia atual..."
        )

        arquivo = self.localizar_arquivo_clicavel(
            nome_arquivo
        )

        self.clicar_duas_vezes(
            arquivo
        )

        try:
            self.aguardar_documento_pronto()
        except TimeoutException:
            logger.warning(
                "A página não confirmou readyState=complete."
            )

        self.pausa(
            "Abertura do arquivo"
        )

    def localizadores_acao_arquivo(
        self,
        nome_acao: str,
    ) -> list[tuple[str, str]]:
        """Cria seletores para uma ação do arquivo."""
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
        ]

    def aguardar_nova_janela(
        self,
        janelas_anteriores: set[str],
    ) -> str | None:
        """Aguarda e retorna o identificador de uma nova janela."""
        driver = self._driver_obrigatorio()

        try:
            WebDriverWait(
                driver,
                self.config.timeout_nova_janela,
                poll_frequency=(
                    self.config.intervalo_verificacao
                ),
            ).until(
                lambda navegador: bool(
                    set(
                        navegador.window_handles
                    )
                    - janelas_anteriores
                )
            )

        except TimeoutException:
            return None

        novas_janelas = (
            set(
                driver.window_handles
            )
            - janelas_anteriores
        )

        return (
            novas_janelas.pop()
            if novas_janelas
            else None
        )

    def abrir_arquivo_em_nova_janela(
        self,
        nome_arquivo: str,
    ) -> None:
        """
        Seleciona o arquivo e aciona Open in a new window.

        Usado quando abrir_new_window=True.
        """
        driver = self._driver_obrigatorio()

        self.selecionar_arquivo(
            nome_arquivo
        )

        janelas_anteriores = set(
            driver.window_handles
        )

        nome_acao = (
            self.config.acao_nova_janela
        )

        self.status(
            f"Procurando a opção {nome_acao}..."
        )

        try:
            opcao = self.esperar_elemento(
                self.localizadores_acao_arquivo(
                    nome_acao
                ),
                clicavel=True,
                timeout=5,
                descricao=f"opção {nome_acao}",
            )

        except TimeoutException:
            self.status(
                "Abrindo o menu de contexto do arquivo..."
            )

            arquivo = self.localizar_arquivo_clicavel(
                nome_arquivo
            )

            self.clicar_com_botao_direito(
                arquivo
            )

            opcao = self.esperar_elemento(
                self.localizadores_acao_arquivo(
                    nome_acao
                ),
                clicavel=True,
                descricao=f"opção {nome_acao}",
            )

        self.clicar(
            opcao
        )

        nova_janela = self.aguardar_nova_janela(
            janelas_anteriores
        )

        if nova_janela is not None:
            driver.switch_to.window(
                nova_janela
            )

            self.status(
                "Arquivo aberto em uma nova janela."
            )

            try:
                self.aguardar_documento_pronto()
            except TimeoutException:
                logger.warning(
                    "A nova janela não confirmou "
                    "readyState=complete."
                )

        else:
            logger.warning(
                "A ação foi clicada, mas nenhuma nova "
                "janela foi detectada."
            )

        self.pausa(
            "Abertura em nova janela"
        )

    # ========================================================
    # PROCESSO PÚBLICO
    # ========================================================

    def abrir_relatorio(
        self,
        *,
        arquivo: str,
        abrir_new_window: bool,
        caminho_pasta: str | None = None,
        pastas: Sequence[PastaEntrada] | None = None,
    ) -> WebDriver:
        """
        Abre um relatório do Pentaho.

        abrir_new_window=True:
            seleciona o arquivo e usa Open in a new window.

        abrir_new_window=False:
            abre o arquivo diretamente com duplo clique.

        Retorna:
            O driver posicionado no relatório aberto.
        """
        nome_arquivo = arquivo.strip()

        if not nome_arquivo:
            raise ValueError(
                "O nome do arquivo não pode ficar vazio."
            )

        pastas_normalizadas = normalizar_pastas(
            caminho_pasta=caminho_pasta,
            pastas=pastas,
        )

        etapa = "Inicialização"

        try:
            etapa = "Criar ChromeDriver"
            driver = self.iniciar_driver()

            etapa = "Acessar Pentaho"
            self.status(
                f"Acessando {self.config.url}..."
            )

            driver.get(
                self.config.url
            )

            self.aguardar_documento_pronto()
            self.pausa(
                "Carregamento inicial"
            )

            etapa = "Login"
            self.realizar_login()

            etapa = "Abrir Browse Files"
            self.abrir_browse_files(
                pastas_normalizadas[0]
            )

            for pasta in pastas_normalizadas:
                etapa = (
                    "Navegar para a pasta "
                    f"{pasta.caminho}"
                )

                self.abrir_e_selecionar_pasta(
                    pasta
                )

            if abrir_new_window:
                etapa = (
                    "Abrir arquivo em nova janela"
                )

                self.abrir_arquivo_em_nova_janela(
                    nome_arquivo
                )
            else:
                etapa = (
                    "Abrir arquivo normalmente"
                )

                self.abrir_arquivo_normalmente(
                    nome_arquivo
                )

            self.status(
                "Relatório aberto com sucesso."
            )

            return driver

        except Exception as erro:
            logger.exception(
                "Erro na etapa: %s",
                etapa,
            )

            registrar_erro_txt(
                etapa=etapa,
                erro=erro,
            )

            if self.config.salvar_diagnostico_em_erro:
                self.salvar_diagnostico(
                    "pentaho_utils"
                )

            raise

    def fechar(
        self,
    ) -> None:
        """Fecha o driver controlado pelo utilitário."""
        if self.driver is None:
            return

        try:
            self.driver.quit()
        except WebDriverException:
            pass
        finally:
            self.driver = None

    def _driver_obrigatorio(
        self,
    ) -> WebDriver:
        """Retorna o driver ou lança erro de estado."""
        if self.driver is None:
            raise RuntimeError(
                "O ChromeDriver ainda não foi iniciado."
            )

        return self.driver


# ============================================================
# FUNÇÃO SIMPLES PARA IMPORTAÇÃO
# ============================================================

def abrir_pentaho(
    *,
    arquivo: str,
    abrir_new_window: bool = True,
    caminho_pasta: str | None = None,
    pastas: Sequence[PastaEntrada] | None = None,
    config: PentahoConfig | None = None,
    driver: WebDriver | None = None,
    status_callback: StatusCallback | None = None,
) -> WebDriver:
    """
    Função principal simplificada.

    Parâmetros:
        arquivo:
            Nome do arquivo no Pentaho.

        abrir_new_window:
            True  -> seleciona e abre em nova janela.
            False -> abre normalmente com duplo clique.

        caminho_pasta:
            Caminho completo, por exemplo:
            /public/dashboards/gestao-operacional

        pastas:
            Alternativa ao caminho_pasta. Recebe PastaPentaho
            ou tuplas (nome, caminho).

        config:
            Configurações opcionais.

        driver:
            Driver Selenium existente, quando desejar reutilizar
            a mesma sessão.

        status_callback:
            Função opcional chamada a cada atualização de status.

    Retorna:
        O WebDriver posicionado no relatório.
    """
    navegador = PentahoNavigator(
        config=config,
        driver=driver,
        status_callback=status_callback,
    )

    return navegador.abrir_relatorio(
        arquivo=arquivo,
        abrir_new_window=abrir_new_window,
        caminho_pasta=caminho_pasta,
        pastas=pastas,
    )


# ============================================================
# EXEMPLO DE EXECUÇÃO DIRETA
# ============================================================

def main() -> int:
    """
    Teste manual deste módulo.

    Antes de executar, configure no PowerShell:

        $env:PENTAHO_USUARIO="seu_usuario"
        $env:PENTAHO_SENHA="sua_senha"
    """
    try:
        abrir_pentaho(
            caminho_pasta=(
                "/public/dashboards/"
                "gestao-operacional"
            ),
            arquivo=(
                "acompanhamento_separacao_v01.wcdf"
            ),
            abrir_new_window=True,
            config=PentahoConfig(
                fechar_telas=False,
            ),
        )

        return 0

    except KeyboardInterrupt:
        logger.warning(
            "Execução interrompida pelo usuário."
        )
        return 130

    except Exception:
        logger.exception(
            "Não foi possível abrir o relatório."
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(
        main()
    )