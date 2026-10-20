"""Gera os PDFs de entrega (Parte I e Parte II) a partir dos Markdown em docs/.

Requer: ``pip install markdown`` e Google Chrome/Chromium no PATH.
Uso: .venv/bin/python scripts/gerar_pdfs.py
Saída: docs/entrega/parte1_algoritmo_mochila.pdf e docs/entrega/parte2_documento_requisitos.pdf
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import markdown

RAIZ = Path(__file__).resolve().parents[1]
DOCS = RAIZ / "docs"
SAIDA = DOCS / "entrega"
REPO = "https://github.com/alphachief13/sigaa-simulado"

CSS = """
@page { size: A4; margin: 18mm 16mm 18mm 16mm; }
body { font-family: "DejaVu Sans", Helvetica, Arial, sans-serif; font-size: 10.5pt; color: #1b1f24;
       line-height: 1.45; }
h1 { font-size: 19pt; color: #123a6b; border-bottom: 2px solid #123a6b; padding-bottom: 4px; }
h2 { font-size: 14pt; color: #123a6b; margin-top: 22px; border-bottom: 1px solid #c9d3e0; }
h2, h3 { break-after: avoid; }
h3 { font-size: 12pt; color: #24364f; margin-top: 16px; }
table { border-collapse: collapse; width: 100%; margin: 8px 0 12px; font-size: 9.5pt;
        page-break-inside: auto; }
tr { page-break-inside: avoid; }
th, td { border: 1px solid #c3ccd8; padding: 4px 6px; vertical-align: top; text-align: left; }
th { background: #eaf0f8; }
code { font-family: "DejaVu Sans Mono", monospace; font-size: 9pt; background: #f2f4f7;
       padding: 0 2px; }
pre { background: #f6f8fa; border: 1px solid #d8dee6; padding: 8px; font-size: 8.6pt;
      line-height: 1.25; overflow: hidden; white-space: pre; page-break-inside: avoid; }
pre code { background: none; padding: 0; }
img { max-width: 100%; }
a { color: #1d4f91; text-decoration: none; }
blockquote { border-left: 3px solid #c3ccd8; margin-left: 0; padding-left: 10px; color: #444; }
.capa { height: 245mm; display: flex; flex-direction: column; justify-content: space-between;
        text-align: center; page-break-after: always; }
.capa .topo { font-size: 13pt; letter-spacing: .5px; }
.capa .titulo { font-size: 24pt; font-weight: bold; color: #123a6b; margin-top: 50mm; }
.capa .sub { font-size: 14pt; margin-top: 6mm; color: #333; }
.capa .rodape { font-size: 11pt; line-height: 1.7; }
"""


def capa(titulo: str, subtitulo: str) -> str:
    return f"""<section class="capa">
<div class="topo">UNIVERSIDADE FEDERAL DO RIO GRANDE DO NORTE<br>Programação Avançada — 2026.2<br>
Prof. Dr. Hertz Wilton de Castro Lins</div>
<div><div class="titulo">{titulo}</div><div class="sub">{subtitulo}</div></div>
<div class="rodape"><b>Equipe:</b> Victor Manuel Valentim Cordeiro<br>
<b>Algoritmo:</b> nº 5 — Mochila Booleana (Knapsack 0/1)<br>
<b>GitHub:</b> <a href="{REPO}">{REPO}</a><br>Natal/RN — outubro de 2026</div>
</section>"""


def html(
    md_path: Path, titulo: str, subtitulo: str, pular_cabecalho: bool, quebras: tuple[str, ...] = ()
) -> str:
    texto = md_path.read_text(encoding="utf-8")
    if pular_cabecalho:  # o cabeçalho do .md vira a capa
        texto = texto.split("\n---\n", 1)[1]
    texto = texto.replace("](../", f"]({REPO}/blob/main/")
    corpo = markdown.markdown(texto, extensions=["tables", "fenced_code", "sane_lists"])
    # rótulo em negrito + bloco seguinte (protótipo, lista) ficam juntos na mesma página
    corpo = re.sub(
        r"(<p><strong>[^<]*</strong></p>\s*<(pre|ul|ol)>.*?</\2>)",
        r'<div style="break-inside: avoid">\1</div>',
        corpo,
        flags=re.S,
    )
    for h in quebras:  # seções que começam em página nova (evita título órfão no rodapé)
        corpo = corpo.replace(f"<h2>{h}", f'<h2 style="break-before: page">{h}', 1)
    return f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<base href="{DOCS.as_uri()}/"><title>{titulo}</title><style>{CSS}</style></head>
<body>{capa(titulo, subtitulo)}{corpo}</body></html>"""


def chrome() -> str:
    for nome in ("google-chrome", "chromium", "chromium-browser"):
        if caminho := shutil.which(nome):
            return caminho
    sys.exit("Chrome/Chromium não encontrado no PATH")


def imprimir(conteudo: str, destino: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "doc.html"
        src.write_text(conteudo, encoding="utf-8")
        subprocess.run(
            [
                chrome(),
                "--headless=new",
                "--disable-gpu",
                "--allow-file-access-from-files",
                "--no-pdf-header-footer",
                f"--print-to-pdf={destino}",
                src.as_uri(),
            ],
            check=True,
            capture_output=True,
        )
    print(f"gerado {destino.relative_to(RAIZ)}")


def main() -> None:
    SAIDA.mkdir(parents=True, exist_ok=True)
    imprimir(
        html(
            DOCS / "ALGORITMO.md",
            "Mochila Booleana (Knapsack 0/1)",
            "Parte I — Implementação do algoritmo",
            pular_cabecalho=False,
            quebras=("4. Como foi validado",),
        ),
        SAIDA / "parte1_algoritmo_mochila.pdf",
    )
    imprimir(
        html(
            DOCS / "REQUISITOS.md",
            "SIGAA Simulado: agente de estudo",
            "Parte II — Documento de Requisitos",
            pular_cabecalho=True,
            quebras=("3. Painel MVP", "5. Protótipos", "6. Diagrama", "7. Especificação"),
        ),
        SAIDA / "parte2_documento_requisitos.pdf",
    )


if __name__ == "__main__":
    main()
