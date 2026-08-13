"""Coletores de banca (FAFIPA e IBAM) — fixtures reais congeladas.

Fixtures em tests/fixtures_textos/ (recortes de páginas baixadas ao vivo em
13.8.2026, prefixo fafipa_/ibam_/ibamsp_):
- fafipa_abertos.html / fafipa_4181_detalhe.html: Água Comprida-MG (4181),
  edital de abertura "limpo" (sem RETIFICADO) — o caso de aceitar;
- fafipa_4205_detalhe.html: Coronel Vivida-PR (4205), o único link de
  "edital de abertura" da página vem RETIFICADO — o caso de rejeitar;
- ibamsp_abertos.html / ibamsp_184_detalhe.html: Mauá-SP (184) no
  ibamsp-concursos.org.br (mesmo software ProSeleta da FAFIPA); o card na
  listagem usa o layout invertido <a><h3> (em vez de <h3><a>), e o edital é
  rotulado só "01- Edital de Abertura" (sem número de edital explícito);
- ibam_status1.html: dois cards do site Bootstrap ibam-concursos.org.br —
  355 (Lages/SC, "Edital e Anexos em breve", só tem NOTA EXPLICATIVA nos
  documentos) e 364 (Caruaru/PE, cargos "Auditor Fiscal Municipal" e
  "Analista Fiscal Municipal" de verdade, edital de abertura limpo).

Os textos de link usados em test_eh_edital_abertura vêm igualmente de
páginas reais (citadas em cada caso), no mesmo espírito de
tests/test_coletores.py (manchetes reais como parâmetro, sem arquivo).
"""

from pathlib import Path

import pytest
import requests

from radar.coletores import fafipa, ibam

FIXTURES = Path(__file__).parent / "fixtures_textos"

MAPA = {
    f"{fafipa.BASE_PADRAO}/index/abertos/": "fafipa_abertos.html",
    f"{fafipa.BASE_PADRAO}/informacoes/4181/": "fafipa_4181_detalhe.html",
    f"{fafipa.BASE_PADRAO}/informacoes/4205/": "fafipa_4205_detalhe.html",
    f"{ibam.BASE_RJ_PADRAO}/?status=1": "ibam_status1.html",
    f"{ibam.BASE_SP_PADRAO}/index/abertos/": "ibamsp_abertos.html",
    f"{ibam.BASE_SP_PADRAO}/informacoes/184/": "ibamsp_184_detalhe.html",
}


class _FakeResp:
    def __init__(self, caminho):
        self.content = (FIXTURES / caminho).read_bytes()

    def raise_for_status(self):
        pass


def _fake_get_ok(url, headers=None, timeout=None):
    if url not in MAPA:
        raise AssertionError(f"URL sem fixture mapeada: {url}")
    return _FakeResp(MAPA[url])


@pytest.fixture(autouse=True)
def _sem_rede_de_verdade(monkeypatch):
    """Todo teste deste módulo usa o mapa de fixtures por padrão; testes que
    querem simular falha sobrescrevem requests.get de novo. time.sleep é
    neutralizado (o coletor pausa entre requisições reais; no teste isso só
    deixaria a suíte lenta sem testar nada)."""
    monkeypatch.setattr(fafipa.requests, "get", _fake_get_ok)
    monkeypatch.setattr(fafipa.time, "sleep", lambda segundos: None)


# ---------------------------------------------------------------------------
# fafipa.py — fluxo completo via coletar()
# ---------------------------------------------------------------------------


def test_fafipa_coleta_edital_limpo_e_rejeita_retificado():
    cursor = {}
    achados = fafipa.coletar({}, cursor, desde_padrao=None)
    por_id = {a.detalhes["id"]: a for a in achados}

    assert set(por_id) == {"4181", "4205"}

    agua_comprida = por_id["4181"]
    assert agua_comprida.fonte == "fafipa"
    assert agua_comprida.orgao == "Município de Água Comprida"
    assert agua_comprida.municipio == "Água Comprida"
    assert agua_comprida.uf == "MG"
    assert agua_comprida.detalhes["banca"] == "Fundação FAFIPA"
    assert agua_comprida.detalhes["edital_url"] == (
        "https://anexos-r2.selecao.net.br/uploads/281/concursos/4181/anexos/"
        "81cc52f7-add9-4c83-a15b-9f4ce8bd24fa.pdf"
    )
    assert agua_comprida.detalhes["site_inscricao"] == (
        f"{fafipa.BASE_PADRAO}/login/sair/?redir=%2Ftermo%2F4181%2F"
    )
    assert "Auxiliar Administrativo" in agua_comprida.cargo_texto

    coronel_vivida = por_id["4205"]
    assert coronel_vivida.uf == "PR"
    # único link de "edital de abertura" da página vem RETIFICADO: fail-closed
    assert "edital_url" not in coronel_vivida.detalhes
    assert coronel_vivida.detalhes["site_inscricao"] == (
        f"{fafipa.BASE_PADRAO}/login/sair/?redir=%2Ftermo%2F4205%2F"
    )


def test_fafipa_cursor_evita_recoleta():
    cursor = {}
    primeira = fafipa.coletar({}, cursor, desde_padrao=None)
    assert len(primeira) == 2
    assert len(cursor["vistos"]) == 2

    segunda = fafipa.coletar({}, cursor, desde_padrao=None)
    assert segunda == []


def test_fafipa_base_fora_do_ar_nao_trava_cursor(monkeypatch):
    def fake_get_falha(url, headers=None, timeout=None):
        raise requests.exceptions.ConnectionError("rede fora")

    monkeypatch.setattr(fafipa.requests, "get", fake_get_falha)
    cursor = {"vistos": ["estado-antigo"]}
    with pytest.raises(RuntimeError):
        fafipa.coletar({}, cursor, desde_padrao=None)
    # cursor não pode ser sobrescrito por uma execução que não coletou nada
    assert cursor["vistos"] == ["estado-antigo"]


# ---------------------------------------------------------------------------
# ibam.py — fluxo completo via coletar() (RJ Bootstrap + SP ProSeleta)
# ---------------------------------------------------------------------------


def test_ibam_coleta_rj_e_sp_juntos():
    cursor = {}
    achados = ibam.coletar({}, cursor, desde_padrao=None)
    por_id = {a.detalhes["id"]: a for a in achados}

    assert set(por_id) == {"355", "364", "184"}
    assert {a.fonte for a in achados} == {"ibam"}

    lages = por_id["355"]
    assert lages.detalhes["banca"] == "IBAM"
    assert "edital_url" not in lages.detalhes  # só tem NOTA EXPLICATIVA nos documentos

    caruaru = por_id["364"]
    assert "Auditor Fiscal Municipal" in caruaru.cargo_texto
    assert caruaru.detalhes["edital_url"] == f"{ibam.BASE_RJ_PADRAO}/documento/ed-pmc0126f.pdf"
    assert caruaru.detalhes["site_inscricao"] == (
        f"{ibam.BASE_RJ_PADRAO}/inscricao.asp?task=novo&cod=364&car=1642"
    )
    assert caruaru.orgao == "Municipio de Caruaru"  # título-fonte já vem sem acento (ver comentário acima)
    assert caruaru.uf == "PE"

    maua = por_id["184"]
    assert maua.detalhes["banca"] == "IBAM-SP"
    assert maua.uf == "SP"
    assert maua.orgao == "Mauá"
    assert maua.detalhes["edital_url"] == (
        "https://anexos-r2.selecao.net.br/uploads/810/concursos/184/anexos/"
        "bc0071a4-578f-4562-b3bd-96756e87414f.pdf"
    )


def test_ibam_cursor_evita_recoleta():
    cursor = {}
    primeira = ibam.coletar({}, cursor, desde_padrao=None)
    assert len(primeira) == 3

    segunda = ibam.coletar({}, cursor, desde_padrao=None)
    assert segunda == []


def test_ibam_uma_plataforma_fora_do_ar_nao_derruba_a_outra(monkeypatch):
    original = fafipa.requests.get

    def fake_get_rj_falha(url, headers=None, timeout=None):
        if url == f"{ibam.BASE_RJ_PADRAO}/?status=1":
            raise requests.exceptions.ConnectionError("IBAM RJ fora do ar")
        return original(url, headers=headers, timeout=timeout)

    monkeypatch.setattr(fafipa.requests, "get", fake_get_rj_falha)
    cursor = {}
    achados = ibam.coletar({}, cursor, desde_padrao=None)
    # SP seguiu funcionando mesmo com RJ fora do ar
    assert {a.detalhes["id"] for a in achados} == {"184"}


def test_ibam_ambas_plataformas_fora_do_ar_propaga_erro(monkeypatch):
    def fake_get_falha(url, headers=None, timeout=None):
        raise requests.exceptions.ConnectionError("rede fora")

    monkeypatch.setattr(fafipa.requests, "get", fake_get_falha)
    with pytest.raises(RuntimeError):
        ibam.coletar({}, {}, desde_padrao=None)


# ---------------------------------------------------------------------------
# eh_edital_abertura — textos reais de link, aceitos e rejeitados
# ---------------------------------------------------------------------------

CASOS_EDITAL = [
    # aceita: "abertura" no texto
    ("Edital de Abertura n.º 01.001/2026 - PM Foz do Iguaçu - PR", True),  # FAFIPA 4170
    ("Edital de Abertura n.º 01.001/2026 - CP PM Água Comprida - MG", True),  # FAFIPA 4181
    ("EDITAL DE ABERTURA DO CONCURSO PÚBLICO 01/2026", True),  # IBAM/Caruaru
    ("EDITAL DE ABERTURA DO PROCESSO SELETIVO Nº 01/2026", True),  # IBAM/Arraial do Cabo
    ("01- Edital de Abertura", True),  # IBAM-SP/Mauá
    # aceita: número solto, sem qualificador de fase depois
    ("EDITAL DE CONCURSO PÚBLICO 01/2026 - PROCURADOR MUNICIPAL", True),  # IBAM/Casimiro de Abreu
    ("EDITAL DE PROCESSO SELETIVO Nº 01/2026", True),  # IBAM/Casimiro de Abreu PS
    # rejeita: "abertura" aparece, mas junto de RETIFICADO
    ("Edital de abertura n.º 001/2026 - CP PM Coronel Vivida - PR - RETIFICADO", False),  # FAFIPA 4205
    ("Edital de abertura n.º 01.001/2026 - PSP PM Goioerê - PR - RETIFICADO", False),  # FAFIPA 4184
    # "rerratificação" não contém "retifica" nem "errata" como substring
    # literal (o "r" duplicado quebra as duas) — achado na validação ao vivo
    ("03- Rerratificação do Edital de Abertura.", False),  # IBAM-SP/Santos, 185
    # rejeita: fase posterior explícita
    ("Edital n.º 02.001/2026 - Deferimento das solicitações de isenção da taxa de inscrição", False),
    ("EDITAL DE PROCESSO SELETIVO Nº 01/2026 - RETIFICADO", False),
    ("Retificação n.º 01 ao edital 01/2026 - Processo Seletivo", False),
    ("Edital n.º 09.001/2026 - Resultado definitivo da prova objetiva", False),
    ("02- Edital de Divulgação do Resultado das Solicitações de Isenção", False),  # IBAM-SP/Mauá
    ("ANEXO I - CARGOS, VAGAS, REQUISITOS, JORNADA DE TRABALHO, VENCIMENTOS E ATRIBUIÇÕES TÍPICAS", False),
    ("NOTA EXPLICATIVA", False),  # IBAM/Lages
    ("", False),
]


@pytest.mark.parametrize("texto, esperado", CASOS_EDITAL, ids=[c[0][:45] or "vazio" for c in CASOS_EDITAL])
def test_eh_edital_abertura(texto, esperado):
    assert fafipa.eh_edital_abertura(texto) is esperado


# ---------------------------------------------------------------------------
# orgao_de_titulo (FAFIPA / bancas ProSeleta) e _orgao_de_titulo_rj (IBAM)
# ---------------------------------------------------------------------------

CASOS_ORGAO_PROSELETA = [
    ("Concurso Público do Município de Coronel Vivida - PR", "",
     ("Município de Coronel Vivida", "Coronel Vivida", "PR")),
    ("Concurso para Emprego Público do Município de Água Comprida - MG", "",
     ("Município de Água Comprida", "Água Comprida", "MG")),
    # sem "Prefeitura/Câmara/Município de": fail-closed, não inventa o órgão
    ("Concurso Público do Instituto de Previdência dos Servidores Públicos de Matinhos - PR", "",
     ("", "", "PR")),
    ("MOGI MIRIM - CONCURSO PÚBLICO - 02/2026 - EDITAL 01", "SP", ("Mogi Mirim", "Mogi Mirim", "SP")),
    ("BEBEDOURO - PROCESSO SELETIVO - 03/2026", "SP", ("Bebedouro", "Bebedouro", "SP")),
    ("MAUÁ - CONCURSO PÚBLICO Nº 01/2026", "SP", ("Mauá", "Mauá", "SP")),
    # não é concurso municipal: 1º segmento não antecede CONCURSO/PROCESSO
    ("VESTIBULAR - FACULDADE DE CIÊNCIAS MÉDICAS DA SANTA CASA DE SÃO PAULO - 1º SEMESTRE 2027", "SP",
     ("", "", "SP")),
]


@pytest.mark.parametrize(
    "titulo, uf_fixa, esperado", CASOS_ORGAO_PROSELETA, ids=[c[0][:40] for c in CASOS_ORGAO_PROSELETA]
)
def test_orgao_de_titulo(titulo, uf_fixa, esperado):
    assert fafipa.orgao_de_titulo(titulo, uf_fixa) == esperado


CASOS_ORGAO_RJ = [
    # o título já vem sem acento em vários casos reais ("Municipio", "Camara")
    # — o extrator ecoa verbatim, não "corrige" ortografia (fail-closed)
    ("Município de Lages/SC - PS 02/2026", ("Município de Lages", "Lages", "SC")),
    ("Municipio de Caruaru/PE - 01/2026", ("Municipio de Caruaru", "Caruaru", "PE")),
    ("Camara Municipal de Penha/SC Edit. 01/26", ("Camara Municipal de Penha", "Penha", "SC")),
    # sem "/UF" no título: fail-closed, UF fica vazia (não adivinha o estado)
    ("Municipio de Casimiro de Abreu - Ed. 01/2026 PS", ("Municipio de Casimiro de Abreu", "Casimiro de Abreu", "")),
    ("Camara Municipal de Blumenau", ("Camara Municipal de Blumenau", "Blumenau", "")),
]


@pytest.mark.parametrize("titulo, esperado", CASOS_ORGAO_RJ, ids=[c[0][:40] for c in CASOS_ORGAO_RJ])
def test_orgao_de_titulo_rj(titulo, esperado):
    assert ibam._orgao_de_titulo_rj(titulo) == esperado
