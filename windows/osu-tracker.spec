# Receita do PyInstaller para o osu-tracker.exe (arquivo unico, sem console).
# Gerar, a partir da pasta do projeto:  pyinstaller windows/osu-tracker.spec
# O resultado fica em dist/osu-tracker.exe (ou dist/osu-tracker fora do Windows).

import os

projeto = os.path.dirname(SPECPATH)

a = Analysis(
    [os.path.join(SPECPATH, "executavel.py")],
    pathex=[projeto],
    datas=[
        # Os scripts de inicio com o Windows vao junto: a janela os usa.
        (os.path.join(SPECPATH, "instalar.ps1"), "windows"),
        (os.path.join(SPECPATH, "desinstalar.ps1"), "windows"),
        # Pagina do painel e a biblioteca de graficos (plotly.js): o painel funciona sem internet.
        (os.path.join(projeto, "osu_tracker", "web"), os.path.join("osu_tracker", "web")),
    ],
    # Importados so quando a janela ou o painel abrem. O uvicorn inteiro vem pelo hook do PyInstaller.
    hiddenimports=["osu_tracker.janela", "osu_tracker.painel"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name="osu-tracker",
    console=False,  # duplo clique abre so a janela; o detector roda sem console
    upx=False,
)
