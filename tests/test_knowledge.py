from __future__ import annotations

from pathlib import Path

from conftest import adapter

from sigaa_sim import pipeline
from sigaa_sim.domain import Item, ItemKind
from sigaa_sim.knowledge import Knowledge, extract, host_allowed, is_login_page, sniff_kind
from sigaa_sim.knowledge.chunks import chunk_segments
from sigaa_sim.knowledge.extract import Segment
from sigaa_sim.sigaa.demo import FIXTURES_DIR
from sigaa_sim.storage import Jobs, Repo
from sigaa_sim.sync import run_sync

FILES = FIXTURES_DIR / "files"
FIS2 = "demo:default:2026.2:fis2_t01"


def _ctx(conn, settings, clock, scenario="baseline"):
    return pipeline.JobContext(
        conn=conn, settings=settings, clock=clock, adapter_factory=lambda: adapter(scenario), llm=None
    )


def _run_all(ctx):
    total = {"done": 0, "failed": 0, "deferred": 0}
    for _ in range(3):
        r = pipeline.process_jobs(ctx, limit=50)
        for k in total:
            total[k] += r[k]
        if not r["done"]:
            break
    return total


def test_sniff_and_login_detection():
    assert sniff_kind((FILES / "fis2_lista1.pdf").read_bytes()) == "pdf"
    assert sniff_kind((FILES / "calc3_integrais_multiplas.pptx").read_bytes()) == "pptx"
    assert sniff_kind((FILES / "prog2_guia_estilo.html").read_bytes()) == "html"
    assert sniff_kind((FILES / "fis2_notas_potencial.md").read_bytes(), "x.md") == "md"
    fake_pdf = (FILES / "login_page_as_pdf.pdf").read_bytes()
    assert sniff_kind(fake_pdf, "arquivo.pdf", "application/pdf") == "html"
    assert is_login_page(fake_pdf)
    assert not is_login_page((FILES / "prog2_guia_estilo.html").read_bytes())


def test_extractors_keep_refs_and_flag_no_text():
    pdf = extract((FILES / "fis2_slides_cap21_22.pdf").read_bytes(), "pdf")
    assert pdf.status == "extracted" and [s.ref for s in pdf.segments] == ["p.1", "p.2", "p.3"]
    scan = extract((FILES / "prog2_exercicios_scan.pdf").read_bytes(), "pdf")
    assert scan.status == "no_text" and "OCR" in scan.error
    pptx = extract((FILES / "calc3_integrais_multiplas.pptx").read_bytes(), "pptx")
    assert pptx.status == "extracted" and pptx.segments[0].ref == "slide 1"
    assert any("pouco texto" in w for w in pptx.warnings)
    html = extract((FILES / "prog2_guia_estilo.html").read_bytes(), "html")
    assert "não deve aparecer" not in " ".join(s.text for s in html.segments)
    assert any("Memória" in s.ref for s in html.segments)
    md = extract((FILES / "fis2_notas_potencial.md").read_bytes(), "md")
    assert any("Potencial elétrico (V)" in s.ref for s in md.segments)
    assert extract(b"\xd0\xcf\x11\xe0junk", "ole").status == "unsupported"


def test_chunker_splits_and_merges_refs():
    segs = [Segment(f"p.{i}", "palavra " * 100) for i in range(1, 6)]
    chunks = chunk_segments(segs, max_chars=1800, overlap=100)
    assert chunks[0].ref.startswith("p.1-")
    long = [Segment("slide 1", "x" * 5000)]
    parts = chunk_segments(long, max_chars=1000, overlap=100)
    assert len(parts) >= 5 and all(c.ref == "slide 1" for c in parts)


def test_download_pipeline_caches_and_invalidates_only_replaced(conn, settings, clock):
    run_sync(conn, adapter("baseline"), settings, clock)
    ctx = _ctx(conn, settings, clock)
    r = _run_all(ctx)
    assert r["failed"] == 0
    repo = Repo(conn)
    assert repo.count("materials", "status='downloaded'") == 8
    assert repo.count("documents", "status='no_text'") == 1
    docs_before = repo.count("documents")
    kb = Knowledge(conn, settings)
    # re-executar não baixa nem re-extrai (jobs concluídos; needs_fetch = recente)
    ok, why = kb.needs_fetch(f"{FIS2}:material:mat_502", clock.now_utc_iso())
    assert ok is False and why == "recente"
    assert Jobs(conn).due(clock.now_utc_iso()) == []
    # material substituído (URL nova para mat-501): só ele gera novo download/documento
    clock.advance(days=1)
    stats = run_sync(conn, adapter("changed"), settings, clock)
    assert stats.jobs == 2  # download do mat-501 novo + interpretação do aviso novo
    ctx = _ctx(conn, settings, clock, "changed")
    _run_all(ctx)
    assert repo.count("documents") == docs_before + 1
    versions = conn.execute(
        "SELECT superseded_at FROM material_versions mv JOIN materials m ON m.id=mv.material_id "
        "WHERE m.item_id=? ORDER BY mv.id",
        (f"{FIS2}:material:mat_501",),
    ).fetchall()
    assert len(versions) == 2 and versions[0]["superseded_at"] and versions[1]["superseded_at"] is None
    # os demais materiais não foram tocados
    other = conn.execute(
        "SELECT COUNT(*) c FROM material_versions mv JOIN materials m ON m.id=mv.material_id "
        "WHERE m.item_id<>?",
        (f"{FIS2}:material:mat_501",),
    ).fetchone()["c"]
    assert other == 7
    # arquivos no disco em diretório por período/disciplina com slug + id
    files = list((settings.materials_dir / "2026_2" / "fis2_t01__fisica_ii").glob("*.pdf"))
    assert len(files) == 3


def test_login_page_and_wrong_type_are_invalid(conn, settings, clock):
    run_sync(conn, adapter("baseline"), settings, clock)
    repo = Repo(conn)
    course = repo.get_course(FIS2)
    kb = Knowledge(conn, settings)
    item = Item(
        id=f"{FIS2}:material:fake",
        course_id=FIS2,
        kind=ItemKind.MATERIAL,
        title="Falso",
        file_url="demo://files/login_page_as_pdf.pdf",
        file_name="apostila.pdf",
    )
    with conn:
        repo.insert_item(item, clock.now_utc_iso(), None)
    out = kb.fetch_material(adapter("baseline"), item, course, clock.now_utc_iso())
    assert out.status == "invalid" and "login" in out.message
    assert repo.count("documents") == 0
    row = conn.execute("SELECT status FROM materials WHERE item_id=?", (item.id,)).fetchone()
    assert row["status"] == "invalid"


def test_only_simulated_sources_are_downloaded(settings):
    settings.sigaa.profile = "simulado"
    assert host_allowed("sim://arquivo/1/2", settings)
    assert not host_allowed("https://sigaa.ufrn.br/sigaa/x.pdf", settings)  # nenhum portal real
    assert not host_allowed("http://exemplo.com/x.pdf", settings)
    assert not host_allowed("demo://files/x.pdf", settings)  # demo só no perfil demo
    settings.sigaa.profile = "demo"
    assert host_allowed("demo://files/x.pdf", settings)
    assert not host_allowed("sim://arquivo/1/2", settings)


def test_size_limit_enforced(conn, settings, clock):
    settings.knowledge.max_file_mb = 0
    run_sync(conn, adapter("baseline"), settings, clock)
    ctx = _ctx(conn, settings, clock)
    _run_all(ctx)
    assert Repo(conn).count("materials", "status='too_large'") == 8
    assert Jobs(conn).counts().get("failed") == 8


def test_search_returns_source_refs(conn, settings, clock):
    run_sync(conn, adapter("baseline"), settings, clock)
    _run_all(_ctx(conn, settings, clock))
    kb = Knowledge(conn, settings)
    hits = kb.search("jacobiano polares")
    assert hits and all(h["ref"] and h["item_id"] for h in hits)
    assert any("Cálculo" in h["title"] or "fórmulas" in h["title"].lower() for h in hits)
    only = kb.search("jacobiano", course_id=FIS2)
    assert only == []


def test_atomic_write_leaves_no_temp(tmp_path: Path):
    from sigaa_sim.knowledge import atomic_write

    p = tmp_path / "a" / "b.bin"
    atomic_write(p, b"abc")
    assert p.read_bytes() == b"abc"
    assert [x.name for x in p.parent.iterdir()] == ["b.bin"]
