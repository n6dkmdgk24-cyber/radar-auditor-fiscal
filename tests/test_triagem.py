"""Regressão da triagem sobre o corpus real de 28.7–4.8.2026.

O corpus (tests/fixtures_corpus.json) congela os 70 achados do incidente:
- bloco "bom": 18 itens de 28-29.7 conferidos um a um pelo Danilo (corretos);
- bloco "ruim": 52 itens de 30.7–4.8 publicados sem veredito após a
  aposentadoria do GitHub Models — todos falsos positivos.

Invariantes que NUNCA podem regredir:
1. Nenhum item "ruim" sai das regras como "abertura".
2. Nenhum item "bom" é descartado pelas regras.
3. Sem IA, item incerto vai para a fila de pendentes — jamais para o painel.
"""

import json
from pathlib import Path

import pytest

from radar import regras, triagem, verificador
from radar.modelos import Achado

CORPUS = json.loads(
    (Path(__file__).parent / "fixtures_corpus.json").read_text(encoding="utf-8")
)


def _achado(caso):
    return Achado(
        fonte=caso["fonte"],
        titulo=caso["titulo"],
        url="https://exemplo/teste",
        detalhes={"trecho": caso["trecho"]},
    )


@pytest.mark.parametrize(
    "caso", [c for c in CORPUS if c["bloco"] == "ruim"], ids=lambda c: c["titulo"][:60]
)
def test_ruim_nunca_vira_abertura(caso):
    veredito, motivo = regras.triar(_achado(caso), caso["categoria"], caso["termos"])
    assert veredito != "abertura", f"falso positivo ressuscitou: {motivo}"


@pytest.mark.parametrize(
    "caso", [c for c in CORPUS if c["bloco"] == "bom"], ids=lambda c: c["titulo"][:60]
)
def test_bom_nunca_e_descartado(caso):
    veredito, motivo = regras.triar(_achado(caso), caso["categoria"], caso["termos"])
    assert veredito != "descarte", f"achado real descartado: {motivo}"


def test_noticia_de_abertura_publica_por_regras():
    caso = next(c for c in CORPUS if "Manaus" in c["titulo"])
    veredito, _ = regras.triar(_achado(caso), caso["categoria"], caso["termos"])
    assert veredito == "abertura"


def test_suspensao_detectada_por_regras():
    caso = next(c for c in CORPUS if "suspenso" in c["titulo"])
    veredito, _ = regras.triar(_achado(caso), caso["categoria"], caso["termos"])
    assert veredito == "suspensao"


def _candidato(titulo, trecho, categoria="conferir", termos=("agente fiscal",)):
    a = Achado(fonte="teste", titulo=titulo, url="https://exemplo/x", detalhes={"trecho": trecho})
    return (a, categoria, list(termos))


def _sem_rede(monkeypatch, veredito=("incerto", "sem rede no teste", {})):
    monkeypatch.setattr(verificador, "verificar", lambda _a, _t: veredito)


def test_incerto_vai_para_pendentes(monkeypatch):
    _sem_rede(monkeypatch)
    r = triagem.triar([_candidato("Comunicado sobre agente fiscal", "texto neutro")], [], {})
    assert not r.publicar and not r.descartar
    assert len(r.pendentes) == 1 and r.novos_pendentes == 1


def test_falha_do_verificador_e_fail_closed(monkeypatch):
    _sem_rede(monkeypatch, ("incerto", "falha ao obter texto integral (rede)", {}))
    r = triagem.triar([_candidato("Comunicado sobre agente fiscal", "texto neutro")], [], {})
    assert not r.publicar, "falha de verificação jamais pode publicar (era o bug de 30.7-4.8)"
    assert len(r.pendentes) == 1


def test_verificador_abertura_publica_e_mapeia_area(monkeypatch):
    _sem_rede(monkeypatch, ("abertura", "anatomia de edital", {"inscricoes": "1/9 a 30/9"}))
    cand = _candidato("Edital novo", "texto", categoria="conferir", termos=("controlador interno",))
    r = triagem.triar([cand], [], {})
    assert len(r.publicar) == 1
    achado, categoria, _termos, _ = r.publicar[0]
    assert categoria == "controle"  # termo ambíguo mapeado para a área
    assert achado.detalhes["ia"]["origem"] == "verificador"
    assert achado.detalhes["ia"]["inscricoes"] == "1/9 a 30/9"


def test_verificador_descarte_descarta(monkeypatch):
    _sem_rede(monkeypatch, ("descarte", "texto integral sem anatomia", {}))
    r = triagem.triar([_candidato("Comunicado sobre agente fiscal", "texto neutro")], [], {})
    assert not r.publicar and not r.pendentes
    assert len(r.descartar) == 1
    assert r.descartar[0][1]["origem"] == "verificador"


def test_pendente_expira_para_descarte(monkeypatch):
    _sem_rede(monkeypatch)
    a, cat, termos = _candidato("Comunicado sobre agente fiscal", "texto neutro")
    meta = {"enfileirado_em": "2026-01-01", "tentativas": 5}
    r = triagem.triar([], [(a, cat, termos, meta)], {"pendentes_expira_dias": 30})
    assert not r.pendentes and len(r.descartar) == 1 and r.expirados == 1


def test_abertura_por_regras_publica_sem_verificador(monkeypatch):
    _sem_rede(monkeypatch)
    cand = _candidato(
        "Prefeitura de Exemplo abre concurso público com 5 vagas para Fiscal de Tributos",
        "inscrições de 10/08 a 10/09/2026",
        categoria="tributario",
        termos=("fiscal de tributos",),
    )
    r = triagem.triar([cand], [], {})
    assert len(r.publicar) == 1
    achado = r.publicar[0][0]
    assert achado.detalhes["ia"]["classe"] == "abertura"
    assert achado.detalhes["ia"]["origem"] == "regras"


def test_manchete_de_noticia_publica_termo_ambiguo_mapeado(monkeypatch):
    _sem_rede(monkeypatch)
    a = Achado(
        fonte="pci",
        titulo="Câmara de Exemplo - SP abre concurso público com salários de até R$ 7.000,00",
        url="https://exemplo/noticia",
        cargo_texto="As oportunidades são para os cargos de: Controlador Interno (1 vaga) Advogado (1 vaga)",
    )
    r = triagem.triar([(a, "conferir", ["controlador interno"])], [], {})
    assert len(r.publicar) == 1
    _, categoria, _, _ = r.publicar[0]
    assert categoria == "controle"
    assert a.detalhes["ia"]["origem"] == "regras"


def test_manchete_em_diario_bruto_nao_publica_termo_ambiguo(monkeypatch):
    _sem_rede(monkeypatch)
    a = Achado(
        fonte="sigpub",
        titulo="Prefeitura Municipal de Exemplo — ABRE CONCURSO (2026-08-05)",
        url="https://exemplo/diario",
        cargo_texto="texto de diário citando agente fiscal",
    )
    r = triagem.triar([(a, "conferir", ["agente fiscal"])], [], {})
    assert not r.publicar, "diário bruto com termo ambíguo precisa de verificação, não de manchete"


def test_retificacao_com_prorrogacao_de_inscricao_e_abertura(monkeypatch):
    _sem_rede(monkeypatch)
    a = Achado(
        fonte="pci",
        titulo="Prefeitura de Exemplo - SP publica retificação em concurso público",
        url="https://exemplo/n1",
        cargo_texto="A retificação prorroga as inscrições até 30/09/2026 para Fiscal de Tributos.",
    )
    r = triagem.triar([(a, "tributario", ["fiscal de tributos"])], [], {})
    assert len(r.publicar) == 1


def test_retificacao_com_boilerplate_de_validade_nao_avisa(monkeypatch):
    _sem_rede(monkeypatch)
    a = Achado(
        fonte="pci",
        titulo="Prefeitura de Exemplo - SP retifica edital de concurso público",
        url="https://exemplo/n2",
        cargo_texto=(
            "Retificação de requisitos do cargo Fiscal de Tributos. O concurso terá "
            "validade de 2 anos, podendo ser prorrogado por igual período."
        ),
    )
    r = triagem.triar([(a, "tributario", ["fiscal de tributos"])], [], {})
    assert not r.publicar, "prorrogação de VALIDADE não é novo prazo de inscrição"
    # sem data de inscrição legível, retificação é fail-closed: fila, nunca
    # descarte silencioso (era descarte até 6.8.2026 — caso Piraju provou o risco)
    assert len(r.pendentes) == 1 and not r.descartar


def test_retificacao_com_inscricoes_ainda_abertas_publica(monkeypatch):
    """Caso real Piraju/SP (7.8.2026): retificação descartada pela regra de
    palavra-chave, mas o artigo dizia inscrições abertas até 30.8.2026."""
    _sem_rede(monkeypatch)
    a = Achado(
        fonte="pci",
        titulo="Prefeitura da Estância Turística de Exemplo - SP retifica edital de concurso público",
        url="https://exemplo/n3",
        cargo_texto=(
            "Foram alterados os requisitos de alguns cargos. Fiscal de Rendas e "
            "Tributos (1 vaga + CR). As inscrições estarão abertas das 00h01 do dia "
            "29 de julho de 2026 às 23h59 do dia 30 de agosto de 2026."
        ),
    )
    import datetime as dt

    r = triagem.triar([(a, "tributario", ["fiscal de rendas"])], [], {}, hoje=dt.date(2026, 8, 6))
    assert len(r.publicar) == 1
    ia = a.detalhes["ia"]
    assert ia["vagas"] == "1 vaga + CR"
    assert a.detalhes["inscricoes_fim"] == "2026-08-30"


def test_retificacao_com_inscricoes_encerradas_descarta(monkeypatch):
    """Caso real Meridiano/SP: retificação com inscrições até 3.8.2026 —
    descarte pelo motivo certo (prazo vencido), não por palavra-chave."""
    _sem_rede(monkeypatch)
    a = Achado(
        fonte="pci",
        titulo="Prefeitura de Exemplo - SP publica retificação em concurso público",
        url="https://exemplo/n4",
        cargo_texto=(
            "Fiscal de Tributos (1 vaga). As inscrições estarão abertas das 13h do "
            "dia 20 de julho de 2026 até às 13h do dia 3 de agosto de 2026."
        ),
    )
    import datetime as dt

    r = triagem.triar([(a, "tributario", ["fiscal de tributos"])], [], {}, hoje=dt.date(2026, 8, 6))
    assert not r.publicar
    assert len(r.descartar) == 1
    veredito = r.descartar[0][1]
    assert "2026-08-03" in veredito["criterio"]


def test_manchete_de_abertura_com_prazo_vencido_nao_avisa(monkeypatch):
    """Guarda transversal: 'reabre inscrições' descoberto tarde, com o prazo
    extraído já vencido, não vira aviso."""
    _sem_rede(monkeypatch)
    a = Achado(
        fonte="pci",
        titulo="Prefeitura de Exemplo - CE reabre inscrições de concurso público",
        url="https://exemplo/n5",
        cargo_texto=(
            "Fiscal de Tributos (2 vagas). As inscrições serão reabertas no período "
            "de 1 a 20 de julho de 2026, pelo site www.exemplo.org."
        ),
    )
    import datetime as dt

    r = triagem.triar([(a, "tributario", ["fiscal de tributos"])], [], {}, hoje=dt.date(2026, 8, 6))
    assert not r.publicar
    assert len(r.descartar) == 1


def test_fiscal_generico_sem_evidencia_tributaria_vira_conferir(monkeypatch):
    """'Agente fiscal' sem atribuição tributária no artigo não ganha selo
    Tributário — sai como 'conferir área' (feedback do Danilo, 6.8.2026)."""
    _sem_rede(monkeypatch)
    a = Achado(
        fonte="pci",
        titulo="Prefeitura de Exemplo - PR abre concurso público com salários de até R$ 6.768",
        url="https://exemplo/n6",
        cargo_texto="As oportunidades são para os cargos de: Agente Fiscal (1 vaga), Motorista (3 vagas).",
    )
    r = triagem.triar([(a, "conferir", ["agente fiscal"])], [], {})
    assert len(r.publicar) == 1
    _achado_pub, categoria_final, _termos, _ = r.publicar[0]
    assert categoria_final == "conferir"
    assert a.detalhes["ia"]["vagas"] == "1 vaga"


def test_fiscal_generico_com_evidencia_tributaria_vira_tributario(monkeypatch):
    """'Fiscal municipal' com atribuição tributária perto do cargo ganha o
    selo Tributário normalmente."""
    _sem_rede(monkeypatch)
    a = Achado(
        fonte="pci",
        titulo="Prefeitura de Exemplo - SP abre concurso público",
        url="https://exemplo/n7",
        cargo_texto=(
            "Cargos: Fiscal Municipal (1 vaga), responsável pela fiscalização "
            "tributária e arrecadação de tributos municipais."
        ),
    )
    r = triagem.triar([(a, "conferir", ["fiscal municipal"])], [], {})
    assert len(r.publicar) == 1
    _achado_pub, categoria_final, _termos, _ = r.publicar[0]
    assert categoria_final == "tributario"


def test_resumo_do_cartao_nunca_e_o_regex_da_regra(monkeypatch):
    """O painel mostra texto humano; o critério técnico fica em ia.criterio
    (feedback do Danilo, 6.8.2026: cartão exibia '\\babre (o )?concursos?\\b')."""
    _sem_rede(monkeypatch)
    a = Achado(
        fonte="pci",
        titulo="Prefeitura de Exemplo - PR abre concurso público com salários de até R$ 21 mil",
        url="https://exemplo/n8",
        cargo_texto=(
            "A Prefeitura de Exemplo, no Paraná, publicou o edital do Concurso "
            "Público 001/2026 com vagas para Fiscal Tributário (1 vaga + CR)."
        ),
    )
    r = triagem.triar([(a, "tributario", ["fiscal tributario"])], [], {})
    assert len(r.publicar) == 1
    ia = a.detalhes["ia"]
    assert "\\b" not in ia["resumo"], "regex vazou para o resumo do cartão"
    assert ia["resumo"].startswith("A Prefeitura de Exemplo")
    assert "manchete de abertura" in ia["criterio"]
