# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['C:\\Users\\JOAO.PEREIRA\\OneDrive - SUPERFRIO\\Área de Trabalho\\Python\\automacao-jp-gestao-operacional-pentaho\\LoopAtualizar.py'],
    pathex=[],
    binaries=[],
    datas=[('C:\\Users\\JOAO.PEREIRA\\OneDrive - SUPERFRIO\\Área de Trabalho\\Python\\automacao-jp-gestao-operacional-pentaho\\diagnosticos', 'diagnosticos'), ('C:\\Users\\JOAO.PEREIRA\\OneDrive - SUPERFRIO\\Área de Trabalho\\Python\\automacao-jp-gestao-operacional-pentaho\\utils', 'utils'), ('C:\\Users\\JOAO.PEREIRA\\OneDrive - SUPERFRIO\\Área de Trabalho\\Python\\automacao-jp-gestao-operacional-pentaho\\logs_tvs.txt', '.'), ('C:\\Users\\JOAO.PEREIRA\\OneDrive - SUPERFRIO\\Área de Trabalho\\Python\\automacao-jp-gestao-operacional-pentaho\\strongData.txt', '.')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='LoopAtualizar',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
