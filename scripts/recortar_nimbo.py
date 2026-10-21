"""Recorta a folha de sprites do Nimbo em tiras horizontais (uma por animação) para o painel.

Uso: python scripts/recortar_nimbo.py <folha.png> [saída=sigaa_sim/web/static/nimbo]
A folha tem fundo #020d1a; o recorte usa flood-fill a partir das bordas, então o visor escuro
do Nimbo não vira transparente. Os quadros de cada tira ficam alinhados (mesmo baseline).
Requer numpy + Pillow (fora das dependências do projeto).
Imprime [quadros, lado, centro x] de cada tira: é a tabela NIMBO de static/app.js.
"""

import sys
from collections import deque

import numpy as np
from PIL import Image

src = sys.argv[1]
outdir = sys.argv[2] if len(sys.argv) > 2 else "sigaa_sim/web/static/nimbo"
A = np.asarray(Image.open(src).convert("RGB")).astype(float)
BG = np.array([2.0, 13.0, 26.0])
D = np.abs(A - BG).max(axis=2)

# (y0, y1) das faixas de cada linha da folha (entre a linha de título e o próximo título)
# centros x de cada quadro (dos componentes detectados) e meia-largura do quadro
G = {
    "hero": ((14, 210), [362], 100),
    "idle": ((72, 200), [538, 638, 738, 838, 940], 49),
    "blink": ((72, 200), [1057, 1151, 1242], 46),
    "walk": ((265, 410), [76, 173, 267, 363, 452], 47),
    "run": ((265, 410), [573, 685, 792, 893], 54),
    "jump": ((262, 420), [1023, 1133, 1237], 52),
    "look": ((472, 605), [70, 155, 241, 328, 417], 43),
    "express": ((472, 605), [532, 614, 694, 774, 854], 40),
    "emotion": ((472, 605), [960, 1054, 1144, 1242], 46),
    "interact": ((662, 815), [94, 233, 381, 510, 643], 66),
    "hold": ((662, 815), [810, 938, 1067, 1210], 64),
    "telegram": ((862, 1000), [102, 243, 412, 560], 74),
    "sleep": ((870, 1004), [733, 866, 995], 64),
    "notify": ((870, 1004), [1154], 70),
    "effects": ((1036, 1172), [90, 221, 356, 502, 613], [66, 66, 66, 54, 50]),
}
H, W = D.shape


def cutout(x0, y0, x1, y1):
    rgb = A[y0:y1, x0:x1]
    d = D[y0:y1, x0:x1]
    h, w = d.shape
    seen = np.zeros((h, w), bool)
    q = deque()
    for y in range(h):
        for x in (0, w - 1):
            if d[y, x] <= 45:
                seen[y, x] = True
                q.append((y, x))
    for x in range(w):
        for y in (0, h - 1):
            if d[y, x] <= 45 and not seen[y, x]:
                seen[y, x] = True
                q.append((y, x))
    while q:
        cy, cx = q.popleft()
        for ny, nx in ((cy + 1, cx), (cy - 1, cx), (cy, cx + 1), (cy, cx - 1)):
            if 0 <= ny < h and 0 <= nx < w and not seen[ny, nx] and d[ny, nx] <= 45:
                seen[ny, nx] = True
                q.append((ny, nx))
    alpha = np.ones((h, w))
    ramp = np.clip((d - 26) / 30, 0, 1)
    alpha[seen] = ramp[seen]
    a3 = np.maximum(alpha, 1e-3)[..., None]
    col = np.clip(BG + (rgb - BG) / a3, 0, 255)
    col[~seen] = rgb[~seen]
    return np.dstack([col, alpha * 255]).astype(np.uint8)


meta = {}
for name, ((y0, y1), xs, hw) in G.items():
    hws = hw if isinstance(hw, list) else [hw] * len(xs)
    frames = [cutout(max(0, cx - h_), y0, min(W, cx + h_), y1) for cx, h_ in zip(xs, hws, strict=True)]
    fw = max(f.shape[1] for f in frames)
    frames = [np.pad(f, ((0, 0), (0, fw - f.shape[1]), (0, 0))) for f in frames]
    # bbox comum (união) do conteúdo visível, para os quadros continuarem alinhados
    m = np.zeros(frames[0].shape[:2], bool)
    for f in frames:
        m |= f[..., 3] > 20
    ys, xs_ = np.where(m)
    by0, by1, bx0, bx1 = ys.min(), ys.max() + 1, xs_.min(), xs_.max() + 1
    # quadro quadrado, conteúdo ancorado embaixo e centralizado
    ch, cw = by1 - by0, bx1 - bx0
    side = max(ch, cw) + 4
    strip = np.zeros((side, side * len(frames), 4), np.uint8)
    for i, f in enumerate(frames):
        c = f[by0:by1, bx0:bx1]
        oy = side - ch - 2
        ox = i * side + (side - cw) // 2
        strip[oy : oy + ch, ox : ox + cw] = c
    ax = (hws[0] - bx0 + (side - cw) // 2) / side
    Image.fromarray(strip).save(
        f"{outdir}/{name}.webp", lossless=False, quality=90, alpha_quality=100, method=6
    )
    meta[name] = {"n": len(frames), "s": int(side), "ax": round(ax, 3)}
    if name == "hero":
        Image.fromarray(strip).resize((64, 64), Image.LANCZOS).save(f"{outdir}/favicon.png", optimize=True)
for k, v in meta.items():
    print(f"{k}: [{v['n']}, {v['s']}, {v['ax']}]")
