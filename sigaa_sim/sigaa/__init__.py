from sigaa_sim.sigaa.base import SECTIONS, DownloadResult, RawSection, SigaaAdapter
from sigaa_sim.sigaa.demo import DemoAdapter
from sigaa_sim.sigaa.simulado import SimuladoAdapter


def make_adapter(settings, scenario: str | None = None) -> SigaaAdapter:
    """Constrói o adapter conforme ``[sigaa].profile``.

    ``simulado`` lê o banco do SIGAA Simulado; ``demo`` lê as fixtures JSON (usado nos testes).
    Não há adapter para o SIGAA real: este projeto não acessa nenhum portal.
    """
    profile = settings.sigaa.profile
    if profile == "simulado":
        return SimuladoAdapter(
            settings.sim_db_path,
            institution=settings.sigaa.institution,
            account=settings.general.account_label,
        )
    if profile == "demo":
        return DemoAdapter(
            scenario=scenario or settings.sigaa.demo_fixture,
            institution=settings.sigaa.institution,
            account=settings.general.account_label,
        )
    raise ValueError(f"perfil de adapter desconhecido: {profile} (use 'simulado' ou 'demo')")


def make_clock(settings):
    """Relógio do agente: o do SIGAA Simulado no perfil ``simulado``; o do sistema no ``demo``."""
    from sigaa_sim.clock import Clock

    if settings.sigaa.profile == "simulado" and settings.sim_db_path.exists():
        from sigaa_sim.simulador.banco import SimClock

        return SimClock(settings.tz, settings.sim_db_path)
    return Clock(settings.tz)


__all__ = [
    "SECTIONS",
    "DemoAdapter",
    "DownloadResult",
    "RawSection",
    "SigaaAdapter",
    "SimuladoAdapter",
    "make_adapter",
    "make_clock",
]
