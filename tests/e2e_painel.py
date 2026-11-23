"""Teste ponta a ponta do painel com o banco e a configuracao reais deste PC.

Sobe o painel numa porta livre, abre num Chromium sem tela (playwright), confere que a pagina
carregou sem erro de JavaScript e salva um print da pagina inteira.

    pip install playwright
    python tests/e2e_painel.py [print.png] [--chromium CAMINHO] [--escuro] [--mapa]

--mapa escolhe no primeiro grafico o mapa com mais tentativas, em vez de "Todos os mapas".
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.sync_api import sync_playwright  # noqa: E402

from osu_tracker import painel  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("saida", nargs="?", default="painel.png")
    parser.add_argument("--chromium", help="executavel do Chromium (padrao: o do playwright)")
    parser.add_argument("--escuro", action="store_true", help="print no modo escuro")
    parser.add_argument("--mapa", action="store_true", help="mostra o mapa com mais tentativas no primeiro grafico")
    parser.add_argument("--largura", type=int, default=1280)
    args = parser.parse_args()

    servidor = painel.Servidor()
    url = servidor.iniciar()
    erros: list[str] = []
    try:
        with sync_playwright() as p:
            navegador = p.chromium.launch(executable_path=args.chromium)
            pagina = navegador.new_page(viewport={"width": args.largura, "height": 900}, device_scale_factor=1,
                                        color_scheme="dark" if args.escuro else "light", locale="pt-BR")
            pagina.on("pageerror", lambda e: erros.append(str(e)))
            pagina.on("console", lambda m: erros.append(m.text) if m.type == "error" else None)
            pagina.goto(url)
            pagina.wait_for_selector("body[data-carregado]")
            if args.mapa:
                opcoes = pagina.locator("#mapa option")
                if opcoes.count() > 1:
                    pagina.select_option("#mapa", index=1)
            pagina.wait_for_timeout(500)
            pagina.screenshot(path=args.saida, full_page=True)
            navegador.close()
    finally:
        servidor.parar()
    for e in erros:
        print("Erro na pagina:", e, file=sys.stderr)
    print(f"Print em {Path(args.saida).resolve()}")
    return 1 if erros else 0


if __name__ == "__main__":
    sys.exit(main())
