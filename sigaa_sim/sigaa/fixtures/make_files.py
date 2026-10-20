"""Gera os arquivos binários de fixture (PDF/PPTX) com conteúdo fictício.

Uso: python -m sigaa_sim.sigaa.fixtures.make_files
"""

from __future__ import annotations

import zlib
from pathlib import Path

from pptx import Presentation
from pptx.util import Inches, Pt

FILES_DIR = Path(__file__).parent / "files"


def _pdf_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_text_pdf(path: Path, pages: list[list[str]]) -> None:
    """PDF mínimo com camada de texto (fonte Helvetica, WinAnsi)."""
    objs: list[bytes] = []

    def add(obj: bytes) -> int:
        objs.append(obj)
        return len(objs)

    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    page_ids: list[int] = []
    pages_id_placeholder = len(objs) + 1 + 2 * len(pages)
    for lines in pages:
        content_lines = ["BT", "/F1 12 Tf", "50 780 Td", "14 TL"]
        for line in lines:
            content_lines.append(f"({_pdf_escape(line)}) Tj T*")
        content_lines.append("ET")
        stream = "\n".join(content_lines).encode("cp1252", errors="replace")
        comp = zlib.compress(stream)
        c_id = add(b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(comp) + comp + b"\nendstream")
        p_id = add(
            f"<< /Type /Page /Parent {pages_id_placeholder} 0 R /MediaBox [0 0 595 842] "
            f"/Contents {c_id} 0 R /Resources << /Font << /F1 {font} 0 R >> >> >>".encode()
        )
        page_ids.append(p_id)
    kids = " ".join(f"{p} 0 R" for p in page_ids)
    pages_id = add(f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode())
    assert pages_id == pages_id_placeholder
    catalog = add(f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode())

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, obj in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root {catalog} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(bytes(out))


def make_image_only_pdf(path: Path) -> None:
    """PDF 'escaneado': uma página com um XObject de imagem e nenhum texto."""
    img = bytes([0xFF] * (8 * 8))  # 8x8 cinza
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
        b"/Resources << /XObject << /Im1 5 0 R >> >> >>",
        b"<< /Length 35 >>\nstream\nq 500 0 0 700 50 70 cm /Im1 Do Q\nendstream",
        b"<< /Type /XObject /Subtype /Image /Width 8 /Height 8 /ColorSpace /DeviceGray "
        b"/BitsPerComponent 8 /Length %d >>\nstream\n" % len(img) + img + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(bytes(out))


def make_pptx(path: Path) -> None:
    prs = Presentation()
    layout = prs.slide_layouts[1]
    slides = [
        ("Integrais múltiplas", ["Material fictício de demonstração", "Cálculo III — AP1"]),
        (
            "Integral dupla",
            [
                "Soma de Riemann em retângulos",
                "Teorema de Fubini: ordem de integração",
                "Regiões do tipo I e tipo II",
            ],
        ),
        (
            "Coordenadas polares",
            [
                "x = r cos θ, y = r sin θ",
                "dA = r dr dθ (não esqueça o jacobiano!)",
                "Regiões circulares e setores",
            ],
        ),
        (
            "Integral tripla",
            [
                "Volume e massa",
                "Coordenadas cilíndricas: dV = r dz dr dθ",
                "Coordenadas esféricas: dV = ρ² sin φ dρ dφ dθ",
            ],
        ),
        (
            "Mudança de variáveis",
            ["Jacobiano |∂(x,y)/∂(u,v)|", "Escolher a transformação que simplifica a região"],
        ),
    ]
    for title, bullets in slides:
        s = prs.slides.add_slide(layout)
        s.shapes.title.text = title
        body = s.placeholders[1].text_frame
        body.text = bullets[0]
        for b in bullets[1:]:
            p = body.add_paragraph()
            p.text = b
            p.font.size = Pt(18)
    # slide só com imagem-placeholder (texto insuficiente) para exercitar o aviso
    s = prs.slides.add_slide(prs.slide_layouts[6])
    tb = s.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(1))
    tb.text_frame.text = "Fig. 3"
    prs.save(str(path))


def main() -> None:
    FILES_DIR.mkdir(parents=True, exist_ok=True)
    make_text_pdf(
        FILES_DIR / "fis2_slides_cap21_22.pdf",
        [
            [
                "Fisica II - Cap. 21: Carga eletrica (material ficticio)",
                "Quantizacao: q = n e, com e = 1,602 x 10^-19 C.",
                "Conservacao da carga em sistemas isolados.",
                "Lei de Coulomb: F = k |q1 q2| / r^2, direcao radial.",
                "Principio da superposicao: somar vetorialmente as forcas.",
            ],
            [
                "Cap. 22: Campo eletrico",
                "Definicao: E = F / q0 (campo existe independente da carga de prova).",
                "Campo de carga pontual: E = k q / r^2.",
                "Linhas de campo: saem de + e chegam em -, densidade ~ intensidade.",
                "Exemplo 22.3: campo no eixo de um anel carregado.",
                "Dipolo eletrico: momento p = q d; torque tau = p x E.",
            ],
            [
                "Distribuicoes continuas",
                "dE = k dq / r^2; integrar sobre a distribuicao.",
                "Linha infinita: E = 2 k lambda / r.",
                "Plano infinito: E = sigma / (2 epsilon0).",
            ],
        ],
    )
    make_text_pdf(
        FILES_DIR / "fis2_slides_cap21_22_v2.pdf",
        [
            [
                "Fisica II - Cap. 21: Carga eletrica (material ficticio) - v2",
                "Quantizacao: q = n e, com e = 1,602 x 10^-19 C.",
                "Conservacao da carga em sistemas isolados.",
                "Lei de Coulomb: F = k |q1 q2| / r^2, direcao radial.",
                "Principio da superposicao: somar vetorialmente as forcas.",
            ],
            [
                "Cap. 22: Campo eletrico",
                "Definicao: E = F / q0 (campo existe independente da carga de prova).",
                "Campo de carga pontual: E = k q / r^2.",
                "Linhas de campo: saem de + e chegam em -, densidade ~ intensidade.",
                "Exemplo 22.3 (CORRIGIDO): campo no eixo de um anel carregado, "
                "E = k q z / (z^2 + R^2)^(3/2).",
                "Dipolo eletrico: momento p = q d; torque tau = p x E.",
            ],
            [
                "Distribuicoes continuas",
                "dE = k dq / r^2; integrar sobre a distribuicao.",
                "Linha infinita: E = 2 k lambda / r.",
                "Plano infinito: E = sigma / (2 epsilon0).",
            ],
        ],
    )
    make_text_pdf(
        FILES_DIR / "fis2_lista1.pdf",
        [
            [
                "Lista 1 - Carga, campo e potencial (material ficticio)",
                "1. Duas cargas de 2 uC e -3 uC estao separadas por 10 cm. Calcule a forca.",
                "2. Determine o campo eletrico no ponto medio entre elas.",
                "3. Use a Lei de Gauss para obter E de uma esfera uniformemente carregada.",
                "4. Calcule o potencial no centro de um quadrado com cargas iguais nos vertices.",
                "5. Dado V(x) = 3x^2 - 2x, obtenha E_x.",
                "6. Explique a diferenca entre energia potencial eletrica e potencial eletrico.",
            ],
        ],
    )
    make_text_pdf(
        FILES_DIR / "prog2_slides_listas.pdf",
        [
            [
                "Programacao II - Listas encadeadas (material ficticio)",
                "No: struct com dado e ponteiro para o proximo.",
                "Insercao no inicio: O(1); no fim sem ponteiro de cauda: O(n).",
                "Remocao: cuidado com o no anterior e com free.",
            ],
            [
                "Pilhas e filas com listas",
                "Pilha: push/pop no inicio.",
                "Fila: enqueue no fim (cauda), dequeue no inicio.",
                "Casos de borda: estrutura vazia, um elemento.",
            ],
        ],
    )
    make_image_only_pdf(FILES_DIR / "prog2_exercicios_scan.pdf")
    make_pptx(FILES_DIR / "calc3_integrais_multiplas.pptx")
    # "PDF" que na verdade é uma página de login HTML (sessão expirada)
    (FILES_DIR / "login_page_as_pdf.pdf").write_text(
        "<!DOCTYPE html><html><head><title>SIGAA - Entrar no Sistema</title></head>"
        "<body><form action='/sigaa/logar.do' method='post'>"
        "<input name='user.login'><input name='user.senha' type='password'></form></body></html>",
        encoding="utf-8",
    )
    print("fixtures gerados em", FILES_DIR)


if __name__ == "__main__":
    main()
