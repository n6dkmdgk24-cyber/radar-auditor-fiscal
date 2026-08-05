"""Verificador de texto integral — casos reais congelados (sem rede).

Fixtures em tests/fixtures_textos/ (recortes de documentos reais):
- santos_abertura / contenda_abertura: editais de abertura verdadeiros que o
  radar publicou (validados pelo Danilo) — anatomia rica de edital;
- querencia_enquadramento: decreto de enquadramento (plano de carreira);
- inocencia_ferias / macae_junta_medica: diários com atos de pessoal que
  causaram falsos positivos no incidente de 30.7–4.8.2026.
"""

from pathlib import Path

import pytest

from radar import verificador

FIXTURES = Path(__file__).parent / "fixtures_textos"

CASOS = [
    ("santos_abertura", ["auditor fiscal"], "abertura"),
    ("contenda_abertura", ["auditor fiscal"], "abertura"),
    ("querencia_enquadramento", ["fiscal de tributos"], "descarte"),
    ("inocencia_ferias", ["auditor fiscal"], "descarte"),
    ("macae_junta_medica", ["fiscal de tributos"], "descarte"),
]


@pytest.mark.parametrize("nome, termos, esperado", CASOS, ids=[c[0] for c in CASOS])
def test_texto_real(nome, termos, esperado):
    texto = (FIXTURES / f"{nome}.txt").read_text(encoding="utf-8")
    veredito, motivo, _extras = verificador.analisar(texto, termos)
    assert veredito == esperado, motivo


def test_edital_sintetico_com_periodo_extrai_inscricoes():
    texto = """
    EDITAL DE ABERTURA DE CONCURSO PÚBLICO N. 01/2026. O Município torna
    pública a abertura de concurso público. DAS VAGAS: Fiscal de Tributos,
    2 (duas) vagas, vencimento R$ 4.500,00, carga horária 40h, requisitos:
    nível superior. DAS INSCRIÇÕES: as inscrições serão recebidas no período
    de 10/08/2026 a 10/09/2026 no site da organizadora. Taxa de inscrição:
    R$ 80,00. DAS PROVAS: prova objetiva em 05/10/2026. CRONOGRAMA anexo.
    """
    veredito, motivo, extras = verificador.analisar(texto, ["fiscal de tributos"])
    assert veredito == "abertura", motivo
    assert extras.get("inscricoes") == "10/08/2026 a 10/09/2026"


def test_ato_de_pessoal_sintetico_e_descartado():
    texto = """
    PORTARIA N. 100/2026. CONCEDER férias regulamentares ao servidor João
    da Silva, matrícula 1234, ocupante do cargo efetivo de Fiscal de
    Tributos, no período de 10/08/2026 a 20/08/2026.
    """
    veredito, motivo, _ = verificador.analisar(texto, ["fiscal de tributos"])
    assert veredito == "descarte", motivo


def test_cargo_ausente_no_texto_e_incerto():
    veredito, _, _ = verificador.analisar("texto qualquer sem o cargo", ["auditor fiscal"])
    assert veredito == "incerto"


def test_falha_de_rede_nao_decide_merito(monkeypatch):
    from radar.modelos import Achado

    def explode(_achado):
        raise RuntimeError("rede fora")

    monkeypatch.setattr(verificador, "obter_texto", explode)
    a = Achado(fonte="qd", titulo="Diário Oficial de X", url="https://exemplo")
    veredito, motivo, _ = verificador.verificar(a, ["auditor fiscal"])
    assert veredito == "incerto"
    assert "falha ao obter" in motivo
