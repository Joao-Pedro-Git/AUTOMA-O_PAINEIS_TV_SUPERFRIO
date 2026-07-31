import logging
import os
import threading
import time
import tkinter as tk

from functools import wraps
from pathlib import Path
from tkinter import messagebox

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
from selenium.webdriver.support.ui import WebDriverWait


# ============================================================
# CONFIGURAÇÕES
# ============================================================

URL = "http://operationsreports.superfrio.com.br:8080/pentaho/Home"

# Opção 2, recomendada: use variáveis de ambiente.
USUARIO = os.getenv("PENTAHO_USUARIO", "JOAO.PEREIRA")
SENHA = os.getenv("PENTAHO_SENHA", "jPereira!@#")

# Tempo máximo para cada elemento aparecer.
TIMEOUT = 90

# Frequência com que o Selenium verifica a página.
INTERVALO_VERIFICACAO = 0.30

# Espera adicional após cada grande etapa.
PAUSA_GLOBAL = 3.0

# Caminhos esperados no Pentaho.
CAMINHO_PUBLIC = "/public"
CAMINHO_DASHBOARDS = "/public/dashboards"
CAMINHO_GESTAO = "/public/dashboards/gestao-operacional"

ARQUIVO_DESTINO = "acompanhamento_separacao_v01.wcdf"


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
# INTERFACE
# ============================================================

def atualizar_status(texto: str) -> None:
    """Agenda a atualização do texto na thread do Tkinter."""
    logger.info(texto)

    janela.after(
        0,
        lambda texto=texto: status_label.config(
            text=texto
        ),
    )


def exibir_erro(titulo: str, mensagem: str) -> None:
    janela.after(
        0,
        lambda titulo=titulo, mensagem=mensagem: (
            messagebox.showerror(
                titulo,
                mensagem,
            )
        ),
    )


def exibir_sucesso(mensagem: str) -> None:
    janela.after(
        0,
        lambda mensagem=mensagem: (
            messagebox.showinfo(
                "Processo concluído",
                mensagem,
            )
        ),
    )


# ============================================================
# PAUSA GLOBAL
# ============================================================

def pausar_apos_etapa(funcao):
    """
    Aplica PAUSA_GLOBAL depois de uma função de alto nível.

    As esperas explícitas continuam sendo utilizadas.
    A pausa global funciona apenas como margem adicional.
    """

    @wraps(funcao)
    def funcao_com_pausa(*args, **kwargs):
        resultado = funcao(*args, **kwargs)

        atualizar_status(
            f"Etapa concluída. Aguardando {PAUSA_GLOBAL:.1f}s..."
        )

        time.sleep(PAUSA_GLOBAL)

        return resultado

    return funcao_com_pausa


# ============================================================
# DIAGNÓSTICO
# ============================================================

def salvar_diagnostico(nome: str = "erro") -> None:
    """
    Salva screenshot e HTML quando ocorrer um erro.

    Os arquivos serão criados dentro de:
        diagnosticos/
    """
    if driver is None:
        return

    try:
        pasta = Path("diagnosticos")
        pasta.mkdir(
            parents=True,
            exist_ok=True,
        )

        timestamp = time.strftime("%Y%m%d_%H%M%S")

        screenshot = pasta / f"{nome}_{timestamp}.png"
        html = pasta / f"{nome}_{timestamp}.html"

        driver.save_screenshot(
            str(screenshot)
        )

        html.write_text(
            driver.page_source,
            encoding="utf-8",
        )

        logger.info(
            "Diagnóstico salvo em: %s",
            pasta.resolve(),
        )

    except Exception:
        logger.exception(
            "Não foi possível salvar o diagnóstico."
        )


# ============================================================
# FUNÇÕES BÁSICAS DO SELENIUM
# ============================================================

def aguardar_documento_pronto(
    timeout: int = TIMEOUT,
) -> None:
    """Espera o documento atual terminar de carregar."""

    WebDriverWait(
        driver,
        timeout,
        poll_frequency=INTERVALO_VERIFICACAO,
    ).until(
        lambda navegador: navegador.execute_script(
            "return document.readyState"
        ) == "complete"
    )


def elemento_esta_disponivel(
    elemento,
    clicavel: bool,
) -> bool:
    """Verifica se o elemento ainda existe e está visível."""
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
    localizadores: list[tuple[str, str]],
    clicavel: bool,
):
    """Procura um elemento no documento ou iframe atual."""

    for tipo, seletor in localizadores:
        try:
            elementos = driver.find_elements(
                tipo,
                seletor,
            )

        except WebDriverException:
            continue

        for elemento in elementos:
            if elemento_esta_disponivel(
                elemento,
                clicavel,
            ):
                return elemento

    return None


def procurar_recursivamente_nos_frames(
    localizadores: list[tuple[str, str]],
    clicavel: bool,
    profundidade: int = 0,
):
    """
    Procura um elemento na página e dentro de todos os frames.

    Quando encontra, mantém o driver no frame correto.
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
    """
    Aguarda um elemento na página principal ou em qualquer iframe.

    O driver permanece dentro do frame onde o elemento
    foi encontrado.
    """
    limite = time.monotonic() + timeout

    while time.monotonic() < limite:
        try:
            driver.switch_to.default_content()

            elemento = procurar_recursivamente_nos_frames(
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

        time.sleep(INTERVALO_VERIFICACAO)

    try:
        driver.switch_to.default_content()
    except WebDriverException:
        pass

    raise TimeoutException(
        f"Não foi possível encontrar: {descricao}"
    )


def elemento_existe(
    localizadores: list[tuple[str, str]],
    timeout: float = 3,
) -> bool:
    """Verifica rapidamente se um elemento existe."""
    try:
        esperar_elemento(
            localizadores,
            clicavel=False,
            timeout=int(timeout),
            descricao="verificação rápida",
        )

        return True

    except TimeoutException:
        return False


# ============================================================
# CLIQUES
# ============================================================

def rolar_ate_elemento(elemento) -> None:
    """Centraliza o elemento na tela."""
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
    """
    Tenta clicar de três formas:

    1. Selenium normal.
    2. ActionChains.
    3. JavaScript.
    """
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


def clicar_duas_vezes(elemento) -> None:
    """
    Executa duplo clique no arquivo.

    Usa ActionChains e possui JavaScript como alternativa.
    """
    rolar_ate_elemento(elemento)

    try:
        ActionChains(driver).move_to_element(
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


# ============================================================
# LOGIN
# ============================================================

@pausar_apos_etapa
def realizar_login() -> None:
    """
    Realiza o login.

    Caso a sessão já esteja autenticada, ignora esta etapa.
    """
    atualizar_status("Verificando a tela de login...")

    localizadores_usuario = [
        (By.ID, "j_username"),
        (By.NAME, "j_username"),
        (By.ID, "username"),
        (By.NAME, "username"),
        (By.CSS_SELECTOR, "input[type='text']"),
    ]

    try:
        campo_usuario = esperar_elemento(
            localizadores_usuario,
            clicavel=True,
            timeout=12,
            descricao="campo de usuário",
        )

    except TimeoutException:
        atualizar_status(
            "Tela de login não encontrada. A sessão pode estar autenticada."
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

    atualizar_status("Realizando login...")

    clicar(botao_login)

    driver.switch_to.default_content()

    atualizar_status(
        "Aguardando o Pentaho concluir o login..."
    )

    WebDriverWait(
        driver,
        TIMEOUT,
        poll_frequency=INTERVALO_VERIFICACAO,
    ).until(
        lambda navegador: (
            navegador.execute_script(
                "return document.readyState"
            ) == "complete"
        )
    )


# ============================================================
# BROWSE FILES
# ============================================================

def abrir_browse_por_javascript() -> bool:
    """
    Executa diretamente a função usada pelo botão Browse Files.
    """
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


@pausar_apos_etapa
def abrir_browse_files() -> None:
    """Abre a perspectiva Browse Files."""
    atualizar_status(
        "Aguardando o botão Browse Files..."
    )

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

        atualizar_status(
            "Clicando em Browse Files..."
        )

        clicar(botao)

    except TimeoutException:
        atualizar_status(
            "Botão não encontrado. Abrindo Browse Files por JavaScript..."
        )

        if not abrir_browse_por_javascript():
            raise TimeoutException(
                "Não foi possível abrir a perspectiva Browse Files."
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


# ============================================================
# ÁRVORE DE PASTAS
# ============================================================

def localizadores_pasta(
    nome: str,
    caminho: str,
) -> list[tuple[str, str]]:
    """
    Cria seletores para uma pasta.

    Primeiro tenta localizar pelo atributo path.
    Depois tenta localizar pelo texto visível.
    """
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


def localizar_titulo_da_pasta(
    pasta,
    nome: str,
):
    """Localiza o título direto de uma pasta."""
    localizadores_relativos = [
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
    ]

    for tipo, seletor in localizadores_relativos:
        try:
            elementos = pasta.find_elements(
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
            (
                "./div[contains(@class,'element')]"
                "/div[contains(@class,'expandCollapse')]"
            ),
        ),
        (
            By.CSS_SELECTOR,
            ":scope > .element > .expandCollapse",
        ),
    ]

    for tipo, seletor in localizadores:
        try:
            elementos = pasta.find_elements(
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


@pausar_apos_etapa
def abrir_e_selecionar_pasta(
    nome: str,
    caminho: str,
) -> None:
    """
    Expande e seleciona uma pasta da árvore.

    A pasta é localizada novamente depois da expansão porque
    o Pentaho pode recriar o elemento no DOM.
    """
    atualizar_status(
        f"Aguardando a pasta {nome}..."
    )

    pasta = esperar_elemento(
        localizadores_pasta(
            nome,
            caminho,
        ),
        clicavel=False,
        timeout=TIMEOUT,
        descricao=f"pasta {nome}",
    )

    rolar_ate_elemento(pasta)

    try:
        classes = (
            pasta.get_attribute("class") or ""
        ).split()

    except StaleElementReferenceException:
        classes = []

    if "open" not in classes:
        expansor = localizar_expansor_da_pasta(
            pasta
        )

        if expansor is not None:
            atualizar_status(
                f"Expandindo a pasta {nome}..."
            )

            clicar(expansor)

            # Aguarda o Pentaho processar a expansão.
            time.sleep(1)

    # O Pentaho pode recriar o elemento depois da expansão.
    pasta = esperar_elemento(
        localizadores_pasta(
            nome,
            caminho,
        ),
        clicavel=False,
        timeout=TIMEOUT,
        descricao=f"pasta {nome} após expansão",
    )

    titulo = localizar_titulo_da_pasta(
        pasta,
        nome,
    )

    atualizar_status(
        f"Selecionando a pasta {nome}..."
    )

    clicar(titulo)


# ============================================================
# ARQUIVO WCDF
# ============================================================

def localizadores_arquivo(
    nome_arquivo: str,
) -> list[tuple[str, str]]:
    """Seletores possíveis para o arquivo na coluna Files."""
    return [
        (
            By.XPATH,
            (
                "//*["
                f"normalize-space()='{nome_arquivo}'"
                "]"
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
    elemento,
):
    """
    Tenta usar o container do arquivo em vez de apenas
    o texto interno.
    """
    try:
        container = elemento.find_element(
            By.XPATH,
            (
                "./ancestor::*["
                "contains("
                "concat(' ', normalize-space(@class), ' '),"
                "' file '"
                ")"
                "][1]"
            ),
        )

        if container.is_displayed():
            return container

    except (
        NoSuchElementException,
        StaleElementReferenceException,
        WebDriverException,
    ):
        pass

    return elemento


@pausar_apos_etapa
def abrir_arquivo(
    nome_arquivo: str,
) -> None:
    """
    Aguarda o arquivo aparecer na coluna Files
    e executa duplo clique.
    """
    atualizar_status(
        f"Aguardando o arquivo {nome_arquivo}..."
    )

    elemento = esperar_elemento(
        localizadores_arquivo(
            nome_arquivo
        ),
        clicavel=True,
        timeout=TIMEOUT,
        descricao=f"arquivo {nome_arquivo}",
    )

    elemento_clicavel = obter_elemento_clicavel_do_arquivo(
        elemento
    )

    atualizar_status(
        f"Abrindo {nome_arquivo}..."
    )

    clicar_duas_vezes(
        elemento_clicavel
    )


# ============================================================
# PROCESSO COMPLETO
# ============================================================

def executar_processo() -> None:
    """
    Sequência:

    1. Abre o Chrome.
    2. Faz login.
    3. Abre Browse Files.
    4. Abre Public.
    5. Abre dashboards.
    6. Abre gestao-operacional.
    7. Abre acompanhamento_separacao_v01.wcdf.
    """
    global driver
    global processo_em_execucao

    try:
        atualizar_status(
            "Abrindo o Chrome..."
        )

        opcoes = webdriver.ChromeOptions()

        opcoes.add_argument(
            "--start-maximized"
        )

        opcoes.add_experimental_option(
            "detach",
            True,
        )

        driver = webdriver.Chrome(
            options=opcoes,
        )

        driver.get(URL)

        atualizar_status(
            "Aguardando a página inicial..."
        )

        aguardar_documento_pronto()

        time.sleep(PAUSA_GLOBAL)

        # 1. Login
        realizar_login()

        # 2. Browse Files
        abrir_browse_files()

        # 3. Public
        abrir_e_selecionar_pasta(
            nome="Public",
            caminho=CAMINHO_PUBLIC,
        )

        # 4. Dashboards
        abrir_e_selecionar_pasta(
            nome="dashboards",
            caminho=CAMINHO_DASHBOARDS,
        )

        # 5. Gestão operacional
        abrir_e_selecionar_pasta(
            nome="gestao-operacional",
            caminho=CAMINHO_GESTAO,
        )

        # 6. Arquivo WCDF
        abrir_arquivo(
            ARQUIVO_DESTINO
        )

        atualizar_status(
            "Processo concluído com sucesso."
        )

        exibir_sucesso(
            (
                "O arquivo foi aberto com sucesso:\n\n"
                f"{ARQUIVO_DESTINO}"
            )
        )

    except TimeoutException as erro:
        logger.exception(
            "Tempo limite excedido."
        )

        atualizar_status(
            "Tempo limite excedido."
        )

        salvar_diagnostico(
            "timeout"
        )

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
        logger.exception(
            "Erro durante a automação."
        )

        atualizar_status(
            "Erro durante o processo."
        )

        salvar_diagnostico(
            "erro"
        )

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

        janela.after(
            0,
            lambda: botao_executar.config(
                state="normal"
            ),
        )


def iniciar_processo() -> None:
    """Inicia a automação sem congelar o Tkinter."""
    global processo_em_execucao

    if processo_em_execucao:
        return

    if (
        not USUARIO
        or USUARIO == "SEU_USUARIO"
    ):
        messagebox.showwarning(
            "Credenciais",
            "Defina o usuário no início do arquivo.",
        )
        return

    if (
        not SENHA
        or SENHA == "SUA_SENHA"
    ):
        messagebox.showwarning(
            "Credenciais",
            "Defina a senha no início do arquivo.",
        )
        return

    processo_em_execucao = True

    botao_executar.config(
        state="disabled"
    )

    status_label.config(
        text="Iniciando o processo..."
    )

    threading.Thread(
        target=executar_processo,
        daemon=True,
        name="processo-pentaho",
    ).start()


# ============================================================
# JANELA
# ============================================================

janela = tk.Tk()

janela.title(
    "Automação Pentaho"
)

janela.geometry(
    "470x220"
)

janela.resizable(
    False,
    False,
)


titulo_label = tk.Label(
    janela,
    text="Processo Pentaho",
    font=(
        "Arial",
        16,
        "bold",
    ),
)

titulo_label.pack(
    pady=(30, 20),
)


botao_executar = tk.Button(
    janela,
    text="Executar o processo",
    width=28,
    height=2,
    command=iniciar_processo,
)

botao_executar.pack()


status_label = tk.Label(
    janela,
    text="Pronto para executar.",
    wraplength=430,
)

status_label.pack(
    pady=18,
)                       


janela.mainloop()