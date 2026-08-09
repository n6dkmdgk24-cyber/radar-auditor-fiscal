"""Atualização de prazo em cartão existente (caso real TCE-SP, 7.8.2026:
a notícia de prorrogação criava um SEGUNDO cartão do mesmo concurso)."""

import json

import pytest

from radar import regras
from radar.estado import Estado
from radar.modelos import Achado


@pytest.fixture
def estado(tmp_path):
    (tmp_path / "concursos.json").write_text(json.dumps([
        {
            "descoberto_em": "2026-07-28",
            "categoria": "controle",
            "termos": ["auditor de controle externo"],
            "fonte": "pci",
            "titulo": "TCE-SP abre concurso com vagas para Auditor de Controle Externo",
            "url": "https://exemplo/abre",
            "orgao": "TCE-SP",
            "municipio": "",
            "uf": "SP",
            "data_publicacao": "2026-07-28",
            "detalhes": {"inscricoes_fim": "2026-08-11", "ia": {"inscricoes": "até 11.8.2026"}},
        }
    ]), encoding="utf-8")
    return Estado(tmp_path)


def _prorrogacao(fim="2026-08-20"):
    return Achado(
        fonte="pci",
        titulo="TCE-SP prorroga inscrições do concurso público para Auditor de Controle Externo",
        url="https://exemplo/prorroga",
        orgao="TCE-SP",
        uf="SP",
        data_publicacao="2026-08-07",
        detalhes={"inscricoes_fim": fim, "ia": {"inscricoes": "13.7.2026 a 20.8.2026"}},
    )


def test_prorrogacao_atualiza_prazo_do_cartao(estado):
    assert estado.atualizar_concurso(_prorrogacao(), "controle") is True
    det = estado.concursos[0]["detalhes"]
    assert det["inscricoes_fim"] == "2026-08-20"
    assert det["ia"]["inscricoes"] == "13.7.2026 a 20.8.2026"
    assert det["ia"]["prazo_atualizado_em"]


def test_prazo_mais_antigo_nao_regride(estado):
    assert estado.atualizar_concurso(_prorrogacao(fim="2026-08-01"), "controle") is False
    assert estado.concursos[0]["detalhes"]["inscricoes_fim"] == "2026-08-11"


def test_sem_prazo_extraido_nao_atualiza(estado):
    a = _prorrogacao()
    a.detalhes = {}
    assert estado.atualizar_concurso(a, "controle") is False


def test_ente_diferente_nao_atualiza(estado):
    a = _prorrogacao()
    a.orgao = "TCE-MG"
    a.uf = "MG"
    assert estado.atualizar_concurso(a, "controle") is False
    assert estado.concursos[0]["detalhes"]["inscricoes_fim"] == "2026-08-11"


def test_periodo_que_comeca_depois_do_prazo_antigo_e_certame_novo(estado):
    """Reabertura meses depois (ou segundo certame do ano): o cartão antigo
    não pode receber esse prazo, e o item precisa virar cartão próprio em vez
    de sumir no `continue` do main."""
    estado.concursos[0]["detalhes"]["inscricoes_fim"] = "2026-04-01"
    a = _prorrogacao(fim="2026-10-30")
    a.detalhes["inscricoes_inicio"] = "2026-10-01"   # começa depois do fim antigo
    assert estado.atualizar_concurso(a, "controle") is False
    assert estado.concursos[0]["detalhes"]["inscricoes_fim"] == "2026-04-01"
    assert estado.eh_certame_novo(a, "controle") is True


def test_prorrogacao_do_mesmo_certame_nao_e_certame_novo(estado):
    a = _prorrogacao(fim="2026-08-20")
    a.detalhes["inscricoes_inicio"] = "2026-07-13"   # dentro do período antigo
    assert estado.eh_certame_novo(a, "controle") is False
    assert estado.atualizar_concurso(a, "controle") is True


def test_gate_de_manchete_para_atualizacao():
    assert regras.manchete_de_prazo(
        "TCE-SP prorroga inscrições do concurso público para Auditor de Controle Externo"
    )
    assert regras.manchete_de_prazo("Prefeitura de X abre concurso público")
    # retificação passa no gate: é assim que a prorrogação costuma ser
    # noticiada; quem decide é a data extraída do artigo (caso Piraju)
    assert regras.manchete_de_prazo("Prefeitura de X - SP retifica edital de concurso público")
    # fase posterior não mexe em prazo de cartão publicado
    assert not regras.manchete_de_prazo("TCE-SP divulga resultado do concurso público")
    assert not regras.manchete_de_prazo("TCE-SP convoca aprovados do concurso")


def test_categoria_conferir_e_tributario_deduplicam_como_o_mesmo_ente(estado):
    """O mesmo concurso pode sair 'conferir' por um artigo e 'tributario' por
    outro (cargo fiscal genérico) — a chave de ente usa a família."""
    a = Achado(fonte="pci", titulo="x", url="https://exemplo/y",
               municipio="Marialva", uf="PR", data_publicacao="2026-08-05")
    estado.marcar(a, "conferir")
    b = Achado(fonte="cnb", titulo="x", url="https://exemplo/z",
               municipio="Marialva", uf="PR", data_publicacao="2026-08-06")
    assert estado.ja_visto_ente(b, "tributario") is True
    # controle é outra família: não colide
    assert estado.ja_visto_ente(b, "controle") is False
