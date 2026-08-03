"""
Gera os dois executáveis necessários:

    dist/
        LoopAtualizar.exe
        gerar_relatorio.exe
        logs_tvs.txt
        erro.txt
        diagnosticos/

Por que são dois executáveis?

LoopAtualizar.exe é o agendador.
gerar_relatorio.exe é o processo Selenium/Tkinter iniciado
pelo agendador nos horários T1, T2 e T3.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

from pathlib import Path


# ============================================================
# CAMINHOS
# ============================================================

RAIZ_PROJETO = Path(
    __file__
).resolve().parent

PASTA_TEMP_LOCAL = Path(
    "C:/Temp/pyinstaller_pentaho"
).resolve()

PASTA_BUILD = (
    PASTA_TEMP_LOCAL
    / "build"
)

PASTA_SPEC = (
    PASTA_TEMP_LOCAL
    / "spec"
)

PASTA_DIST_TEMP = (
    PASTA_TEMP_LOCAL
    / "dist"
)

PASTA_DIST_PROJETO = (
    RAIZ_PROJETO
    / "dist"
)

PYTHON_EXECUTAVEL = Path(
    sys.executable
).resolve()


# ============================================================
# FUNÇÕES AUXILIARES
# ============================================================

def remover_pasta(
    pasta: Path,
) -> None:
    """Remove uma pasta anterior quando ela existir."""
    if not pasta.exists():
        return

    shutil.rmtree(
        pasta,
        ignore_errors=False,
    )


def validar_arquivos() -> None:
    """Confirma a estrutura antes de iniciar o PyInstaller."""
    arquivos = (
        RAIZ_PROJETO / "LoopAtualizar.py",
        RAIZ_PROJETO / "utils" / "__init__.py",
        RAIZ_PROJETO / "utils" / "gerar_relatorio.py",
        RAIZ_PROJETO / "utils" / "register_logs.py",
    )

    ausentes = [
        arquivo
        for arquivo in arquivos
        if not arquivo.is_file()
    ]

    if ausentes:
        raise FileNotFoundError(
            "Arquivos obrigatórios ausentes:\n"
            + "\n".join(
                f"- {arquivo}"
                for arquivo in ausentes
            )
        )


def executar_comando(
    nome: str,
    comando: list[str],
) -> None:
    """Executa um comando e interrompe em caso de erro."""
    print(
        "\n"
        + "=" * 70
    )

    print(
        nome
    )

    print(
        "=" * 70
    )

    print(
        subprocess.list2cmdline(
            comando
        )
    )

    resultado = subprocess.run(
        comando,
        cwd=str(
            RAIZ_PROJETO
        ),
        check=False,
    )

    if resultado.returncode != 0:
        raise RuntimeError(
            f"{nome} falhou com código "
            f"{resultado.returncode}."
        )


def comando_base(
    *,
    nome: str,
    script: Path,
    windowed: bool,
    workpath: Path,
) -> list[str]:
    """Monta os argumentos comuns do PyInstaller."""
    comando = [
        str(
            PYTHON_EXECUTAVEL
        ),
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--name",
        nome,
        "--paths",
        str(
            RAIZ_PROJETO
        ),
        "--workpath",
        str(
            workpath
        ),
        "--specpath",
        str(
            PASTA_SPEC
        ),
        "--distpath",
        str(
            PASTA_DIST_TEMP
        ),
    ]

    comando.append(
        "--windowed"
        if windowed
        else "--console"
    )

    comando.append(
        str(
            script
        )
    )

    return comando


def compilar_gerar_relatorio() -> None:
    """Compila a automação Selenium/Tkinter."""
    comando = comando_base(
        nome="gerar_relatorio",
        script=(
            RAIZ_PROJETO
            / "utils"
            / "gerar_relatorio.py"
        ),
        windowed=True,
        workpath=(
            PASTA_BUILD
            / "gerar_relatorio"
        ),
    )

    # Inserir opções antes do caminho do script.
    comando[-1:-1] = [
        "--collect-all",
        "selenium",
        "--collect-all",
        "pyautogui",
    ]

    executar_comando(
        "Compilando gerar_relatorio.exe",
        comando,
    )


def compilar_loop_atualizar() -> None:
    """Compila o agendador com console visível."""
    comando = comando_base(
        nome="LoopAtualizar",
        script=(
            RAIZ_PROJETO
            / "LoopAtualizar.py"
        ),
        windowed=False,
        workpath=(
            PASTA_BUILD
            / "LoopAtualizar"
        ),
    )

    comando[-1:-1] = [
        "--hidden-import",
        "utils.register_logs",
    ]

    executar_comando(
        "Compilando LoopAtualizar.exe",
        comando,
    )


def montar_distribuicao() -> None:
    """Copia os dois executáveis para a pasta dist do projeto."""
    remover_pasta(
        PASTA_DIST_PROJETO
    )

    PASTA_DIST_PROJETO.mkdir(
        parents=True,
        exist_ok=True,
    )

    arquivos_exe = (
        "LoopAtualizar.exe",
        "gerar_relatorio.exe",
    )

    for nome_arquivo in arquivos_exe:
        origem = (
            PASTA_DIST_TEMP
            / nome_arquivo
        )

        destino = (
            PASTA_DIST_PROJETO
            / nome_arquivo
        )

        if not origem.is_file():
            raise FileNotFoundError(
                f"Executável não gerado: {origem}"
            )

        shutil.copy2(
            origem,
            destino,
        )

    # Arquivos persistentes ficam fora dos bundles.
    (
        PASTA_DIST_PROJETO
        / "logs_tvs.txt"
    ).touch(
        exist_ok=True
    )

    (
        PASTA_DIST_PROJETO
        / "erro.txt"
    ).touch(
        exist_ok=True
    )

    (
        PASTA_DIST_PROJETO
        / "diagnosticos"
    ).mkdir(
        exist_ok=True
    )

    # Copia strongData.txt somente se existir e for necessário.
    strong_data = (
        RAIZ_PROJETO
        / "strongData.txt"
    )

    if strong_data.is_file():
        shutil.copy2(
            strong_data,
            PASTA_DIST_PROJETO
            / "strongData.txt",
        )

    (
        PASTA_DIST_PROJETO
        / "LEIA-ME.txt"
    ).write_text(
        (
            "AUTOMAÇÃO PENTAHO\n\n"
            "Mantenha LoopAtualizar.exe e "
            "gerar_relatorio.exe na mesma pasta.\n"
            "Inicie somente LoopAtualizar.exe.\n\n"
            "logs_tvs.txt, erro.txt e diagnosticos "
            "permanecem nesta pasta.\n"
        ),
        encoding="utf-8",
    )


def main() -> int:
    """Limpa, compila e monta a distribuição."""
    try:
        validar_arquivos()

        remover_pasta(
            PASTA_TEMP_LOCAL
        )

        PASTA_BUILD.mkdir(
            parents=True,
            exist_ok=True,
        )

        PASTA_SPEC.mkdir(
            parents=True,
            exist_ok=True,
        )

        PASTA_DIST_TEMP.mkdir(
            parents=True,
            exist_ok=True,
        )

        compilar_gerar_relatorio()
        compilar_loop_atualizar()
        montar_distribuicao()

        print(
            "\n"
            + "=" * 70
        )

        print(
            "COMPILAÇÃO CONCLUÍDA"
        )

        print(
            "=" * 70
        )

        print(
            f"Pasta final: {PASTA_DIST_PROJETO}"
        )

        print(
            "\nCompartilhe a pasta dist inteira."
        )

        return 0

    except KeyboardInterrupt:
        print(
            "\nCompilação cancelada."
        )

        return 130

    except Exception as erro:
        print(
            f"\nFalha na compilação: "
            f"{type(erro).__name__}: {erro}"
        )

        return 1


if __name__ == "__main__":
    raise SystemExit(
        main()
    )