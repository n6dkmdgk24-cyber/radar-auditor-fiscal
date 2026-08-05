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

from radar import classificador, regras, triagem
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


def test_incerto_sem_ia_vai_para_pendentes(monkeypatch):
    monkeypatch.setattr(classificador, "disponivel", lambda: False)
    r = triagem.triar([_candidato("Comunicado sobre agente fiscal", "texto neutro")], [], {})
    assert not r.publicar and not r.descartar
    assert len(r.pendentes) == 1 and r.novos_pendentes == 1


def test_erro_de_ia_e_fail_closed(monkeypatch):
    monkeypatch.setattr(classificador, "disponivel", lambda: True)

    def explode(_texto):
        raise RuntimeError("api fora do ar")

    monkeypatch.setattr(classificador, "classificar", explode)
    r = triagem.triar([_candidato("Comunicado sobre agente fiscal", "texto neutro")], [], {})
    assert not r.publicar, "erro de IA jamais pode publicar (era o bug de 30.7-4.8)"
    assert len(r.pendentes) == 1 and r.erros_ia == 1


def test_pendente_expira_para_descarte(monkeypatch):
    monkeypatch.setattr(classificador, "disponivel", lambda: False)
    a, cat, termos = _candidato("Comunicado sobre agente fiscal", "texto neutro")
    meta = {"enfileirado_em": "2026-01-01", "tentativas": 5}
    r = triagem.triar([], [(a, cat, termos, meta)], {"pendentes_expira_dias": 30})
    assert not r.pendentes and len(r.descartar) == 1 and r.expirados == 1


def test_abertura_por_regras_publica_sem_ia(monkeypatch):
    monkeypatch.setattr(classificador, "disponivel", lambda: False)
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
