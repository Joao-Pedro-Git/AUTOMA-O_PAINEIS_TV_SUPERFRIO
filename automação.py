import logging
import os
import threading
import time
import tkinter as tk
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

USUARIO = os.getenv("PENTAHO_USUARIO", "JOAO.PEREIRA")
SENHA = os.getenv("PENTAHO_SENHA", "jPereira!@#")

TIMEOUT = 90
INTERVALO_VERIFICACAO = 0.30
PAUSA_GLOBAL = 3.0
CONTAGEM_INICIAL = 5

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
    """Mostra a conclusão e fecha a pequena janela automaticamente."""
    if not janela.winfo_exists():
        return

    status_variavel.set("Processo concluído com sucesso.")
    contador_variavel.set("Concluído")
    janela.after(2500, janela.destroy)


# ============================================================
# DIAGNÓSTICO
# ============================================================

def salvar_diagnostico(nome: str = "erro") -> None:
    """Salva screenshot e HTML quando ocorre um erro."""
    if driver is None:
        return

    try:
        pasta = Path("diagnosticos")
        pasta.mkdir(parents=True, exist_ok=True)

        timestamp = time.strftime("%Y%m%d_%H%M%S")
        screenshot = pasta / f"{nome}_{timestamp}.png"
        html = pasta / f"{nome}_{timestamp}.html"

        driver.save_screenshot(str(screenshot))
        html.write_text(driver.page_source, encoding="utf-8")

        logger.info("Diagnóstico salvo em: %s", pasta.resolve())

    except Exception:
        logger.exception("Não foi possível salvar o diagnóstico.")


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
    """Seletores possíveis para o arquivo na coluna Files."""
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
    """Tenta retornar o container clicável do arquivo."""
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


def abrir_arquivo(nome_arquivo: str) -> None:
    """Aguarda o arquivo e executa duplo clique."""
    atualizar_status(f"Aguardando o arquivo {nome_arquivo}...")

    elemento = esperar_elemento(
        localizadores_arquivo(nome_arquivo),
        clicavel=True,
        timeout=TIMEOUT,
        descricao=f"arquivo {nome_arquivo}",
    )

    elemento_clicavel = obter_elemento_clicavel_do_arquivo(
        elemento
    )

    atualizar_status(f"Abrindo {nome_arquivo}...")
    clicar_duas_vezes(elemento_clicavel)
    pausa_adicional("Abertura do relatório")


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
        atualizar_status("Abrindo o Chrome...")

        opcoes = webdriver.ChromeOptions()
        opcoes.add_argument("--start-maximized")
        opcoes.add_experimental_option("detach", True)

        driver = webdriver.Chrome(options=opcoes)
        driver.get(URL)

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

        abrir_arquivo(ARQUIVO_DESTINO)

        logger.info("Processo concluído com sucesso.")

        if janela.winfo_exists():
            janela.after(0, concluir_interface)

    except TimeoutException as erro:
        logger.exception("Tempo limite excedido.")
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
    """Valida as credenciais antes de abrir o navegador."""
    if not USUARIO or USUARIO == "SEU_USUARIO":
        messagebox.showwarning(
            "Credenciais",
            "Defina PENTAHO_USUARIO antes de executar.",
        )
        return False

    if not SENHA or SENHA == "SUA_SENHA":
        messagebox.showwarning(
            "Credenciais",
            "Defina PENTAHO_SENHA antes de executar.",
        )
        return False

    return True


def iniciar_processo_automaticamente() -> None:
    """Inicia o Selenium em outra thread."""
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
    """
    Exibe 5, 4, 3, 2, 1 sem congelar a janela.

    Não utiliza time.sleep na thread do Tkinter.
    """
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
    """Confirma o fechamento quando a automação está rodando."""
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
# JANELA DE AVISO
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

# Começa automaticamente logo após a janela aparecer.
janela.after(
    250,
    executar_contagem_regressiva,
    CONTAGEM_INICIAL,
)

janela.mainloop()