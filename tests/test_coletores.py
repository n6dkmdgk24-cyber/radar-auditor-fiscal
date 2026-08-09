"""Identificação do ente nos coletores de notícia.

A chave de deduplicação por ente depende de município/órgão + UF: quando o
coletor erra o nome, a mesma notícia vinda do pci e do cnb vira dois cartões,
e a prorrogação não encontra o cartão da abertura (caso TCE-SP, 7.8.2026).
Os casos abaixo são manchetes reais.
"""

import pytest

from radar.coletores import cnb, pci


def _pci(titulo, corpo=""):
    m_sigla = None if pci._PREFIXO_ORGAO.match(titulo) else pci.RX_SIGLA.match(titulo)
    m_org = None if m_sigla else (pci.RX_ORGAO.search(titulo) or pci.RX_ORGAO.search(corpo[:600]))
    m_uf = pci.RX_UF_TITULO.search(titulo)
    orgao = m_sigla.group(1) if m_sigla else (f"{m_org.group(1)} {m_org.group(2)}".strip() if m_org else "")
    municipio = m_org.group(2).strip() if m_org else ""
    uf = m_sigla.group(2) if m_sigla else (m_uf.group(1) if m_uf else "")
    return orgao, municipio, uf


CASOS_PCI = [
    ("Prefeitura de Coronel Vivida - PR abre concurso público com salários de até R$ 21.525",
     "Prefeitura de Coronel Vivida", "Coronel Vivida", "PR"),
    # município com partícula: era truncado em "Conceição"
    ("Prefeitura de Conceição do Mato Dentro - MG abre concurso público com salários de até R$ 21.138,26",
     "Prefeitura de Conceição do Mato Dentro", "Conceição do Mato Dentro", "MG"),
    # município com hífen: era truncado em "Ji"
    ("Prefeitura de Ji-Paraná - RO abre concurso público", "Prefeitura de Ji-Paraná", "Ji-Paraná", "RO"),
    # sem " - UF" no título: o verbo encerra o nome
    ("Prefeitura de Manaus abre concurso público com 20 vagas para Auditor-Fiscal",
     "Prefeitura de Manaus", "Manaus", ""),
    # entidades identificadas pela sigla/nome antes da UF
    ("TCE-SP prorroga inscrições do concurso público para Auditor de Controle Externo", "TCE", "", "SP"),
    ("SEFAZ - MT: Concurso para Fiscal de Tributos Estaduais é suspenso", "SEFAZ", "", "MT"),
    ("CREMERS - RS retifica edital de concurso público para Médico Fiscal e Agente Fiscal",
     "CREMERS", "", "RS"),
    ("CaraguaPrev - SP abre concurso público com salários de até R$ 7.104,30", "CaraguaPrev", "", "SP"),
]


@pytest.mark.parametrize("titulo, orgao, municipio, uf", CASOS_PCI, ids=[c[0][:45] for c in CASOS_PCI])
def test_ente_no_pci(titulo, orgao, municipio, uf):
    assert _pci(titulo) == (orgao, municipio, uf)


def test_orgao_do_corpo_nao_sequestra_noticia_de_entidade():
    """Notícia de tribunal costuma citar municípios no primeiro parágrafo."""
    corpo = "O TCE-PR realizou auditoria no Município de Londrina antes de abrir o certame."
    assert _pci("TCE-PR prorroga inscrições do concurso", corpo)[0] == "TCE"


def _cnb(titulo):
    m_org = cnb.RX_ORGAO.search(titulo)
    m_ent = None if m_org else cnb.RX_ENTIDADE.match(titulo)
    orgao = (
        f"{m_org.group(1)} de {m_org.group(2).strip()}" if m_org
        else (m_ent.group(1).strip() if m_ent else "")
    )
    uf = m_org.group(3) if m_org else (m_ent.group(2) if m_ent else "")
    return orgao, uf


def test_ente_no_cnb():
    assert _cnb("Prefeitura de Auriflama (SP) abre concurso com salário de até R$ 6,5 mil") == (
        "Prefeitura de Auriflama", "SP")
    # entidade sem "Prefeitura de": mesma notícia do pci, precisa deduplicar
    assert _cnb("Concurso CaraguaPrev (SP) abre vagas com salários até R$ 7.104") == (
        "CaraguaPrev", "SP")
    assert _cnb("Prefeitura de Ji-Paraná (RO) abre concurso") == ("Prefeitura de Ji-Paraná", "RO")
