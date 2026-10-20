"""Divisão em chunks por estrutura (segmento) e tamanho, preservando a referência de origem."""

from __future__ import annotations

from dataclasses import dataclass

from sigaa_sim.knowledge.extract import Segment

CHUNKER_VERSION = "1"


@dataclass
class Chunk:
    seq: int
    ref: str
    text: str


def chunk_segments(segments: list[Segment], max_chars: int = 1800, overlap: int = 200) -> list[Chunk]:
    chunks: list[Chunk] = []
    seq = 0
    buf_ref: list[str] = []
    buf = ""

    def flush():
        nonlocal buf, buf_ref, seq
        if buf.strip():
            chunks.append(Chunk(seq=seq, ref=_merge_refs(buf_ref), text=buf.strip()))
            seq += 1
        buf, buf_ref = "", []

    for seg in segments:
        if len(seg.text) > max_chars:
            flush()
            for piece in _split_long(seg.text, max_chars, overlap):
                chunks.append(Chunk(seq=seq, ref=seg.ref, text=piece))
                seq += 1
            continue
        if buf and len(buf) + len(seg.text) + 2 > max_chars:
            flush()
        buf = (buf + "\n\n" + seg.text) if buf else seg.text
        buf_ref.append(seg.ref)
    flush()
    return chunks


def _split_long(text: str, max_chars: int, overlap: int) -> list[str]:
    out = []
    start = 0
    while start < len(text):
        end = min(len(text), start + max_chars)
        if end < len(text):
            cut = text.rfind("\n", start + max_chars // 2, end)
            if cut == -1:
                cut = text.rfind(". ", start + max_chars // 2, end)
            if cut != -1:
                end = cut + 1
        out.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return [o for o in out if o]


def _merge_refs(refs: list[str]) -> str:
    if not refs:
        return ""
    if len(refs) == 1:
        return refs[0]
    if all(r.startswith("p.") for r in refs):
        return f"p.{refs[0][2:]}-{refs[-1][2:]}"
    if all(r.startswith("slide ") for r in refs):
        return f"slides {refs[0][6:]}-{refs[-1][6:]}"
    return refs[0] + f" (+{len(refs) - 1})"
