"""Extrator de dados estruturados — casos reais congelados (sem rede).

Fixtures em tests/fixtures_textos/*_artigo.txt: corpos reais de artigos do
PCI/CNB baixados em 6.8.2026, exatamente como o coletor os entrega
(texto de <article> p/li). Cada caso trava o comportamento que motivou o
extrator: datas de inscrição nos cartões, vagas reais do cargo-alvo
("1 vaga + CR" em vez do selo genérico), banca, validade e site de
inscrição — e a decisão de retificação pela data (caso Piraju × Meridiano).
"""

import datetime as dt
from pathlib import Path

import pytest

from radar import extrator
from radar.modelos import Achado

FIXTURES = Path(__file__).parent / "fixtures_textos"
HOJE = dt.date(2026, 8, 6)  # data da coleta dos artigos congelados


def _extrair(nome, termos, titulo=""):
    corpo = (FIXTURES / f"{nome}.txt").read_text(encoding="utf-8")
    achado = Achado(fonte="pci", titulo=titulo, url="https://x", cargo_texto=corpo)
    return extrator.extrair(achado, termos, hoje=HOJE)


def test_coronel_vivida_periodo_vagas_banca_validade():
    ex = _extrair("coronel_vivida_artigo", ["fiscal tributario"])
    assert ex["inscricoes_inicio"] == "2026-08-11"
    assert ex["inscricoes_fim"] == "2026-10-08"
    assert ex["inscricoes_texto"] == "11.8.2026 a 8.10.2026"
    assert ex["vagas"]["fiscal tributario"] == {"n": 1, "cr": True}
    assert ex["vagas_texto"] == "1 vaga + CR"
    assert ex["cr_somente"] is False
    assert "FAFIPA" in ex["banca"]
    assert ex["site_inscricao"] == "https://www.fundacaofafipa.org.br"
    assert ex["validade"] == "2 anos, prorrogável"
    assert ex["resumo"].startswith("A Prefeitura de Coronel Vivida")


def test_cuite_dois_cargos_alvo_uma_vaga_cada():
    ex = _extrair("cuite_mamanguape_artigo", ["auditor de tributos", "fiscal de tributos"])
    assert ex["inscricoes_inicio"] == "2026-08-12"
    assert ex["inscricoes_fim"] == "2026-09-13"
    assert ex["vagas"] == {
        "auditor de tributos": {"n": 1, "cr": False},
        "fiscal de tributos": {"n": 1, "cr": False},
    }
    # nome do cargo sai acentuado e capitalizado no cartão, não como termo interno
    assert ex["vagas_texto"] == "Auditor de Tributos: 1 vaga · Fiscal de Tributos: 1 vaga"
    assert ex["validade"] == "1 ano, prorrogável"


def test_meridiano_retificacao_inscricoes_ja_encerradas():
    ex = _extrair("meridiano_retificacao_artigo", ["fiscal municipal"])
    assert ex["inscricoes_inicio"] == "2026-07-20"
    assert ex["inscricoes_fim"] == "2026-08-03"      # encerradas antes de HOJE
    assert dt.date.fromisoformat(ex["inscricoes_fim"]) < HOJE
    assert ex["vagas"]["fiscal municipal"] == {"n": None, "cr": True}
    assert ex["cr_somente"] is True


def test_piraju_retificacao_inscricoes_ainda_abertas():
    ex = _extrair("piraju_retificacao_artigo", ["fiscal de rendas"])
    assert ex["inscricoes_inicio"] == "2026-07-29"
    assert ex["inscricoes_fim"] == "2026-08-30"      # abertas em HOJE
    assert dt.date.fromisoformat(ex["inscricoes_fim"]) >= HOJE
    # o cargo é nomeado por inteiro no artigo ("Fiscal de Rendas e Tributos")
    assert ex["vagas"] == {"Fiscal de Rendas e Tributos": {"n": 1, "cr": True}}
    assert ex["vagas_texto"] == "1 vaga + CR"


def test_tcesp_prorrogacao_soma_especialidades():
    ex = _extrair("tcesp_prorrogacao_artigo", ["auditor de controle externo"])
    # a frase completa (13.7 a 20.8) vence a frase que só repete o fim
    assert ex["inscricoes_inicio"] == "2026-07-13"
    assert ex["inscricoes_fim"] == "2026-08-20"
    # 2+11+10+10+12+5 vagas nas seis especialidades do cargo
    assert ex["vagas"]["auditor de controle externo"] == {"n": 50, "cr": False}
    assert ex["site_inscricao"] == "https://www.vunesp.com.br/TCSP2501"
    assert ex["validade"] == "24 meses, prorrogável"


def test_ipua_reabertura_cr_somente():
    ex = _extrair("ipua_reabertura_artigo", ["fiscal municipal"])
    assert ex["inscricoes_inicio"] == "2026-08-03"
    assert ex["inscricoes_fim"] == "2026-08-10"
    assert ex["vagas"]["fiscal municipal"] == {"n": None, "cr": True}
    assert ex["cr_somente"] is True
    assert ex["site_inscricao"] == "https://www.omconcursos.com.br"


def test_marialva_agente_fiscal_uma_vaga():
    ex = _extrair("marialva_artigo", ["agente fiscal"])
    assert ex["inscricoes_inicio"] == "2026-08-10"
    assert ex["inscricoes_fim"] == "2026-09-03"
    assert ex["vagas"]["agente fiscal"] == {"n": 1, "cr": False}
    assert ex["vagas_texto"] == "1 vaga"
    assert ex["cr_somente"] is False


def test_cajueiro_dia_solto_herda_mes_do_periodo():
    ex = _extrair("cajueiro_reabertura_artigo", ["auditor tributario"])
    # "reabertas no período de 6 a 30 de agosto de 2026"
    assert ex["inscricoes_inicio"] == "2026-08-06"
    assert ex["inscricoes_fim"] == "2026-08-30"
    assert ex["vagas"]["auditor tributario"] == {"n": 2, "cr": True}


def test_auriflama_cnb_ano_herdado_da_data_final():
    ex = _extrair("auriflama_cnb_artigo", ["auditor fiscal"])
    # "das 10h do dia 10 de agosto até 16h do dia 01 de setembro de 2026"
    assert ex["inscricoes_inicio"] == "2026-08-10"
    assert ex["inscricoes_fim"] == "2026-09-01"


def test_quatro_pontes_periodo_entre_os_dias():
    ex = _extrair("quatro_pontes_artigo", ["fiscal de tributos"])
    # "entre os dias 31 de julho de 2026 e 8 de setembro de 2026"
    assert ex["inscricoes_inicio"] == "2026-07-31"
    assert ex["inscricoes_fim"] == "2026-09-08"
    assert ex["site_inscricao"] == "https://concursos.unioeste.br"
    # "...Fiscal de Obras Fiscal de Tributos Fonoaudiólogo (1 vaga)": a vaga é
    # do Fonoaudiólogo. O cargo-alvo está listado SEM vagas — campo vazio.
    assert ex["vagas"] == {}
    assert ex["vagas_texto"] == ""


def test_vaga_do_cargo_vizinho_nao_e_roubada():
    achado = Achado(
        fonte="pci", titulo="", url="https://x",
        cargo_texto="Cargos: Farmacêutico Fiscal de Obras Fiscal de Tributos Fonoaudiólogo (1 vaga) Médico (2 vagas)",
    )
    ex = extrator.extrair(achado, ["fiscal de tributos"], hoje=HOJE)
    assert ex["vagas"] == {}


def test_termos_sobrepostos_no_mesmo_cargo_nao_dobram_vagas():
    """'Auditor Fiscal de Tributos Municipais (2 vagas)' casa 'auditor fiscal'
    E 'fiscal de tributos' no MESMO parêntese — conta uma vez (caso Taubaté)."""
    achado = Achado(
        fonte="pci", titulo="", url="https://x",
        cargo_texto="Segundo o edital: Auditor Fiscal de Tributos Municipais (2 vagas)",
    )
    ex = extrator.extrair(achado, ["auditor fiscal", "fiscal de tributos"], hoje=HOJE)
    assert list(ex["vagas"].values()) == [{"n": 2, "cr": False}]
    assert ex["vagas_texto"] == "2 vagas"


def test_especialidades_do_mesmo_cargo_somam():
    """TCE-SP: seis especialidades do mesmo cargo, ligadas por hífen."""
    ex = _extrair("tcesp_prorrogacao_artigo", ["auditor de controle externo"])
    assert ex["vagas"]["auditor de controle externo"] == {"n": 50, "cr": False}


def test_formato_abreviado_de_vagas_do_ibam():
    """IBAM escreve '(1 + CR)' sem a palavra 'vaga' (Balneário Piçarras/SC)."""
    achado = Achado(
        fonte="pci", titulo="", url="https://x",
        cargo_texto="Engenheiro Civil (1 + CR) Fiscal Fazendário II (1 + CR) Médico (CR)",
    )
    ex = extrator.extrair(achado, ["fiscal fazendario"], hoje=HOJE)
    assert ex["vagas"] == {"Fiscal Fazendário II": {"n": 1, "cr": True}}
    assert ex["vagas_texto"] == "1 vaga + CR"


def test_banca_real_com_ancora_de_meio_ou_realizacao():
    """'realizado pela ITAME' e 'por meio do INEPAM' são bancas verdadeiras —
    o veto barra só 'site www'/'internet'/órgão (Cezarina/GO e Auriflama/SP)."""
    for texto, esperado in (
        ("O concurso é realizado pela ITAME - Instituto de Consultoria e Concursos, para preencher vagas.",
         "ITAME - Instituto de Consultoria e Concursos"),
        ("A Prefeitura, por meio do INEPAM, divulgou a abertura das inscrições.", "INEPAM"),
        ("A Prefeitura, por meio da Secretaria Municipal de Finanças, abre concurso.", ""),
    ):
        achado = Achado(fonte="pci", titulo="", url="https://x", cargo_texto=texto)
        assert extrator.extrair(achado, ["auditor fiscal"], hoje=HOJE)["banca"] == esperado


def test_jornada_entre_parenteses_nao_vira_vaga():
    achado = Achado(
        fonte="pci", titulo="", url="https://x",
        cargo_texto="Cargos: Fiscal de Tributos (40 horas semanais) e Contador (R$ 3.500,00).",
    )
    ex = extrator.extrair(achado, ["fiscal de tributos"], hoje=HOJE)
    assert ex["vagas"] == {}


def test_periodo_cruzando_o_ano_novo():
    achado = Achado(
        fonte="pci", titulo="", url="https://x",
        cargo_texto=(
            "Fiscal de Tributos (1 vaga). As inscrições estarão abertas de 15 de "
            "dezembro de 2026 até 15 de janeiro, pelo site www.exemplo.org.br."
        ),
    )
    ex = extrator.extrair(achado, ["fiscal de tributos"], hoje=dt.date(2026, 12, 16))
    assert ex["inscricoes_inicio"] == "2026-12-15"
    assert ex["inscricoes_fim"] == "2027-01-15"


def test_data_de_prova_na_mesma_frase_nao_vira_fim_da_inscricao():
    achado = Achado(
        fonte="pci", titulo="", url="https://x",
        cargo_texto=(
            "As inscrições podem ser feitas de 1º a 30 de julho de 2026 e as provas "
            "estão previstas para 8 de novembro de 2026."
        ),
    )
    ex = extrator.extrair(achado, ["fiscal de tributos"], hoje=HOJE)
    assert ex["inscricoes_fim"] == "2026-07-30"


def test_banca_nao_captura_meio_de_inscricao():
    """'por meio do site www...' produzia banca='site www' (Rifaina/SP)."""
    achado = Achado(
        fonte="pci", titulo="", url="https://x",
        cargo_texto=(
            "As inscrições devem ser feitas por meio do site www.glconsultoria.com.br. "
            "A inscrição será realizada pela internet."
        ),
    )
    ex = extrator.extrair(achado, ["fiscal de tributos"], hoje=HOJE)
    assert ex["banca"] == ""


def test_banca_verdadeira_ainda_e_extraida():
    achado = Achado(
        fonte="pci", titulo="", url="https://x",
        cargo_texto="O concurso é organizado pela Fundação FAFIPA, com inscrições online.",
    )
    ex = extrator.extrair(achado, ["fiscal de tributos"], hoje=HOJE)
    assert ex["banca"] == "Fundação FAFIPA"


def test_site_de_inscricao_nao_chuta_o_primeiro_link():
    """Guarulhos: o artigo diz 'pelo site do IBAM' e o primeiro link do artigo
    é a prefeitura — o cartão apontava para o site errado."""
    achado = Achado(
        fonte="pci", titulo="", url="https://x",
        cargo_texto="As inscrições poderão ser realizadas exclusivamente pelo site do IBAM.",
        detalhes={"links_artigo": [
            ["Prefeitura de Guarulhos", "https://www.guarulhos.sp.gov.br/"],
            ["IBAM", "https://www.ibamsp-concursos.org.br/"],
        ]},
    )
    ex = extrator.extrair(achado, ["auditor fiscal"], hoje=HOJE)
    assert ex["site_inscricao"] == "https://www.ibamsp-concursos.org.br/"


def test_sem_nome_nem_dominio_o_site_fica_vazio():
    achado = Achado(
        fonte="pci", titulo="", url="https://x",
        cargo_texto="As inscrições serão feitas exclusivamente pela internet.",
        detalhes={"links_artigo": [["leia mais", "https://seletrix-backend.onrender.com"]]},
    )
    ex = extrator.extrair(achado, ["auditor fiscal"], hoje=HOJE)
    assert ex["site_inscricao"] == ""


def test_resumo_do_cnb_nao_repete_o_titulo():
    """cargo_texto do cnb é 'título\\nresumo\\ncorpo' e o título não termina
    em ponto — o resumo saía com o título colado."""
    achado = Achado(
        fonte="cnb",
        titulo="Prefeitura de Rio Negrinho (SC) libera edital com salário de até R$ 18,5 mil",
        url="https://x",
        cargo_texto=(
            "Prefeitura de Rio Negrinho (SC) libera edital com salário de até R$ 18,5 mil\n"
            "A Prefeitura de Rio Negrinho, no estado de Santa Catarina, publicou o edital "
            "do concurso público 001/2026 com vagas para diversos cargos.\n"
        ),
    )
    ex = extrator.extrair(achado, ["fiscal de tributos"], hoje=HOJE)
    assert ex["resumo"].startswith("A Prefeitura de Rio Negrinho")
    assert "\n" not in ex["resumo"]


def test_isencao_nao_contamina_o_periodo():
    achado = Achado(
        fonte="pci", titulo="", url="https://x",
        cargo_texto=(
            "As inscrições serão feitas das 8h do dia 27 de julho de 2026 até às "
            "23h59 do dia 25 de agosto de 2026. Haverá isenção da taxa mediante "
            "solicitação no período de 27 a 31 de julho de 2026."
        ),
    )
    ex = extrator.extrair(achado, ["fiscal tributario"], hoje=HOJE)
    assert ex["inscricoes_inicio"] == "2026-07-27"
    assert ex["inscricoes_fim"] == "2026-08-25"


def test_sem_dados_devolve_campos_vazios():
    achado = Achado(fonte="pci", titulo="Título qualquer", url="https://x",
                    cargo_texto="Texto sem nenhuma informação de concurso.")
    ex = extrator.extrair(achado, ["auditor fiscal"], hoje=HOJE)
    assert ex["inscricoes_inicio"] == "" and ex["inscricoes_fim"] == ""
    assert ex["vagas"] == {} and ex["vagas_texto"] == ""
    assert ex["cr_somente"] is False
    assert ex["banca"] == "" and ex["validade"] == ""


def test_fallback_de_link_do_artigo_quando_texto_nao_cita_dominio():
    achado = Achado(
        fonte="cnb", titulo="", url="https://concursosnobrasil.com/x",
        cargo_texto="O cadastro pode ser feito no site oficial do INEPAM.",
        detalhes={"links_artigo": [
            ["Auditor", "https://concursosnobrasil.com/cargos/auditor/"],
            ["site", "https://app.inepam.org.br/concurso/concursoPaginaInterna.do?idConcurso=1"],
        ]},
    )
    ex = extrator.extrair(achado, ["auditor fiscal"], hoje=HOJE)
    assert ex["site_inscricao"].startswith("https://app.inepam.org.br/")


# --- regressões da 2ª revisão adversarial (6.8.2026) -----------------------

def test_data_de_outro_contexto_nao_vira_prazo_em_2027():
    """Antes, QUALQUER retrocesso de data ganhava +1 ano: o artigo de
    retificação que cita o prazo antigo passava a afirmar fim em 2027 e o
    concurso morto ficava um ano no painel."""
    achado = Achado(
        fonte="pci", titulo="", url="https://x",
        cargo_texto=(
            "As inscrições poderão ser realizadas até às 23h59 do dia 20 de agosto "
            "de 2026, e não mais até 6 de agosto como previa o edital original."
        ),
    )
    ex = extrator.extrair(achado, ["fiscal de tributos"], hoje=HOJE)
    assert not ex["inscricoes_fim"].startswith("2027")
    assert ex["inscricoes_fim"] in ("", "2026-08-20")


def test_virada_de_ano_continua_funcionando():
    achado = Achado(
        fonte="pci", titulo="", url="https://x",
        cargo_texto="As inscrições vão de 15 de dezembro de 2026 até 15 de janeiro.",
    )
    ex = extrator.extrair(achado, ["fiscal de tributos"], hoje=dt.date(2026, 12, 16))
    assert (ex["inscricoes_inicio"], ex["inscricoes_fim"]) == ("2026-12-15", "2027-01-15")


def test_virgula_separa_cargos_e_nao_e_continuacao():
    achado = Achado(
        fonte="pci", titulo="", url="https://x",
        cargo_texto="Cargos: Fiscal de Tributos, Fonoaudiólogo (2 vagas), Médico (1 vaga)",
    )
    assert extrator.extrair(achado, ["fiscal de tributos"], hoje=HOJE)["vagas"] == {}


def test_qualificador_de_nivel_e_continuacao_do_cargo():
    achado = Achado(
        fonte="pci", titulo="", url="https://x",
        cargo_texto="Cargos: Auditor Fiscal Nível Superior (2 vagas)",
    )
    ex = extrator.extrair(achado, ["auditor fiscal"], hoje=HOJE)
    assert list(ex["vagas"].values()) == [{"n": 2, "cr": False}]


def test_cargos_diferentes_com_mesmo_termo_nao_somam():
    """Cosmópolis/SP: 'Agente Fiscal em Enfermagem I' e 'Agente Fiscal em
    Engenharia Sanitária I' viravam 'Agente Fiscal (2 vagas)' — cargo que não
    existe no artigo."""
    achado = Achado(
        fonte="pci", titulo="", url="https://x",
        cargo_texto=(
            "Agente Fiscal em Enfermagem I (1 vaga + CR) "
            "Agente Fiscal em Engenharia Sanitária I (1 vaga + CR)"
        ),
    )
    ex = extrator.extrair(achado, ["agente fiscal"], hoje=HOJE)
    # nome do cargo com a grafia do artigo (acentos e maiúsculas preservados)
    assert ex["vagas"] == {
        "Agente Fiscal em Enfermagem I": {"n": 1, "cr": True},
        "Agente Fiscal em Engenharia Sanitária I": {"n": 1, "cr": True},
    }
    assert ex["vagas_texto"] == (
        "Agente Fiscal em Enfermagem I: 1 vaga + CR · "
        "Agente Fiscal em Engenharia Sanitária I: 1 vaga + CR"
    )


def test_flexao_de_genero_no_nome_do_cargo():
    """Guarulhos/SP: o artigo lista 'Auditor(a) Fiscal VI (10 vagas)' e o
    cartão saía sem nenhuma informação de vagas."""
    achado = Achado(
        fonte="pci", titulo="", url="https://x",
        cargo_texto="Segundo o edital: Auditor(a) Fiscal VI (10 vagas)",
    )
    ex = extrator.extrair(achado, ["auditor fiscal"], hoje=HOJE)
    assert list(ex["vagas"].values()) == [{"n": 10, "cr": False}]
    assert ex["vagas_texto"] == "10 vagas"


def test_token_generico_da_banca_nao_casa_o_site_do_ente():
    """'Instituto Brasileiro de Administração Municipal' não pode apontar
    para o site da câmara só porque o link do ente contém 'municipal'."""
    achado = Achado(
        fonte="pci", titulo="", url="https://x",
        cargo_texto=(
            "O certame está a cargo do Instituto Brasileiro de Administração Municipal, "
            "com inscrições até 25 de agosto de 2026."
        ),
        detalhes={"links_artigo": [
            ["Câmara Municipal de Blumenau", "https://camarablu.sc.gov.br/"],
            ["IBAM", "https://www.ibam-concursos.org.br/"],
        ]},
    )
    ex = extrator.extrair(achado, ["auditor fiscal"], hoje=HOJE)
    assert ex["site_inscricao"] == "https://www.ibam-concursos.org.br/"


def test_site_no_plural_e_reconhecido():
    """Rincão/SP: 'exclusivamente pelos sites www.inepam.org.br e ...'."""
    achado = Achado(
        fonte="pci", titulo="", url="https://x",
        cargo_texto="As inscrições serão feitas exclusivamente pelos sites www.inepam.org.br e www.rincao.sp.gov.br.",
    )
    ex = extrator.extrair(achado, ["fiscal de tributos"], hoje=HOJE)
    assert ex["site_inscricao"] == "https://www.inepam.org.br"


def test_meio_de_inscricao_nunca_vira_banca():
    for texto in (
        "As inscrições serão feitas por meio da plataforma de concursos até 30 de agosto.",
        "A inscrição é feita por meio do aplicativo oficial, com taxa de R$ 80.",
    ):
        achado = Achado(fonte="pci", titulo="", url="https://x", cargo_texto=texto)
        assert extrator.extrair(achado, ["auditor fiscal"], hoje=HOJE)["banca"] == ""


def test_banca_nao_sai_cortada_quando_o_texto_tem_reticencias():
    """normalizar() expande '…' em '...' (NFKD) e os índices deixavam de
    valer no texto original, cortando o nome da banca no meio."""
    achado = Achado(
        fonte="pci", titulo="", url="https://x",
        cargo_texto="Leia mais… O concurso é organizado pela Fundação Exemplo, com provas em outubro.",
    )
    banca = extrator.extrair(achado, ["auditor fiscal"], hoje=HOJE)["banca"]
    assert banca.lower().startswith("funda"), banca
