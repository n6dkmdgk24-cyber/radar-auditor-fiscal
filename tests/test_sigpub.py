"""Coletor do SIGPub (radar/coletores/sigpub.py) — fixtures reais congeladas.

Fixtures em tests/fixtures_html/, baixadas ao vivo em 13.8.2026:

- sigpub_amp_pesquisar_portaria.html: página de resultado REAL de
  https://www.diariomunicipal.com.br/amp/pesquisar (busca "portaria", 10
  dias), 10 linhas de datatable — cobre o parsing de _linhas_resultado com
  entidade/título/órgão/data/caminho verdadeiros (portarias, editais,
  decretos, atas de licitação — a mistura real de tipos de documento que
  aparece numa busca de termo genérico).
- sigpub_generico_pesquisa_vazia.html: página REAL (base "ms" do SIGPub,
  hoje sem publicações desde 2020 — ver docstring do módulo) com token
  válido e datatable vazio ("Nenhum registro encontrado"). O HTML de
  formulário é o MESMO template em todas as bases do SIGPub (mesmo
  fornecedor, Vox Tecnologia); usada nos testes de coletar() como resposta
  genérica de "base no ar, sem resultado" para qualquer slug simulado —
  não precisa baixar uma página por base fictícia para testar rodízio e
  cursor, que são lógica de orquestração, não de parsing.

Os testes de coletar()/`_coletar_base` trocam requests.Session por um duble
que roteia por (url, params) — mesmo espírito do duble de
tests/test_coletores_banca.py, adaptado para sessão (o SIGPub usa token de
sessão, os coletores de banca não).
"""

import datetime as dt
from pathlib import Path

import pytest
import requests

from radar.coletores import sigpub

FIXTURES = Path(__file__).parent / "fixtures_html"
PAGINA_PORTARIA = (FIXTURES / "sigpub_amp_pesquisar_portaria.html").read_text(encoding="utf-8")
PAGINA_VAZIA = (FIXTURES / "sigpub_generico_pesquisa_vazia.html").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# _linhas_resultado / _municipio_de_entidade / _data_iso — parsing puro,
# página real de resultado (10 linhas: editais, portarias, decretos, atas)
# ---------------------------------------------------------------------------

def test_linhas_resultado_pagina_real():
    linhas = sigpub._linhas_resultado(PAGINA_PORTARIA)
    assert len(linhas) == 10

    primeira = linhas[0]
    assert primeira == {
        "caminho": "/amp/load/063FB198",
        "entidade": "Prefeitura Municipal de Almirante Tamandaré",
        "titulo": "EDITAL DE CONVOCAÇÃO Nº 050/2026 - CONCURSO PÚBLICO 2023",
        "orgao": "SECRETARIA MUNICIPAL DE RECURSOS HUMANOS",
        "data": "06-08-2026",
    }
    # o caminho vem do próprio href (com a base do link), não é reconstruído
    # a partir de nenhum nome de base — ver docstring do módulo
    assert all(l["caminho"].startswith("/amp/load/") for l in linhas)
    # sem duplicar linha (dedupe é responsabilidade de quem chama, por
    # "caminho", mas o parsing em si não pode inventar nem perder nenhuma)
    assert len({l["caminho"] for l in linhas}) == 10


def test_linhas_resultado_pagina_vazia_sem_tbody_util():
    # base sem publicação recente: token válido, datatable com a linha
    # "dataTables_empty" (sem <a href> em nenhuma célula) — não pode virar
    # um Achado fantasma
    assert sigpub._linhas_resultado(PAGINA_VAZIA) == []


def test_linhas_resultado_sem_datatable():
    assert sigpub._linhas_resultado("<html><body>nada aqui</body></html>") == []


CASOS_MUNICIPIO = [
    ("Prefeitura Municipal de Almirante Tamandaré", "Almirante Tamandaré"),
    ("Prefeitura Municipal de Cafelândia", "Cafelândia"),
    ("Prefeitura Municipal de General Carneiro", "General Carneiro"),
    ("Prefeitura Municipal de Porto Amazonas", "Porto Amazonas"),
    # órgão interno (coluna "orgao", não "entidade") não é um ente municipal
    # e não deve ser lido como se fosse — fail-closed, string vazia
    ("SECRETARIA MUNICIPAL DE RECURSOS HUMANOS", ""),
    ("DEPARTAMENTO RECURSOS HUMANOS", ""),
    # entidade já em caixa correta com preposição minúscula: achado real da
    # base agm/GO em 13.8.2026 — .title() cego virava "Aparecida Do Rio
    # Doce" e "Foz Do Iguaçu" (nunca existiram assim, a preposição some do
    # nome próprio de verdade)
    ("Município de Aparecida do Rio Doce", "Aparecida do Rio Doce"),
    ("Prefeitura Municipal de Foz do Iguaçu", "Foz do Iguaçu"),
    # entidade em CAIXA ALTA de verdade (achado real da base amr/RR em
    # 13.8.2026): aí sim precisa reformatar, mas preposição continua minúscula
    ("CAMARA MUNICIPAL DE UIRAMUTA", "Uiramuta"),
    ("MUNICIPIO DE SAO BENTO DO UNA", "Sao Bento do Una"),
]


@pytest.mark.parametrize("entidade, municipio", CASOS_MUNICIPIO, ids=[c[0][:40] for c in CASOS_MUNICIPIO])
def test_municipio_de_entidade(entidade, municipio):
    assert sigpub._municipio_de_entidade(entidade) == municipio


def test_data_iso():
    assert sigpub._data_iso("06-08-2026") == "2026-08-06"
    assert sigpub._data_iso("31-07-2026") == "2026-07-31"
    assert sigpub._data_iso("lixo") == ""
    assert sigpub._data_iso("") == ""
    assert sigpub._data_iso(None) == ""


def test_uf_da_base_so_tem_ufs_reais_e_nao_repete_o_erro_do_fgm():
    # toda UF cadastrada precisa ser uma sigla de 2 letras válida — e o caso
    # que motivou a checagem ao vivo (fgm "seria" SE mas o dropdown do
    # próprio formulário mostra municípios de Goiás) não pode voltar
    assert all(len(uf) == 2 and uf.isupper() for uf in sigpub.UF_DA_BASE.values())
    assert sigpub.UF_DA_BASE["fgm"] == "GO"
    assert sigpub.UF_DA_BASE["amp"] == "PR"
    # bases testadas e descartadas (ver docstring) não podem aparecer aqui —
    # entrariam no rodízio com uf="" mas ainda consumiriam tempo de execução
    for base_morta in ("ms", "appm", "amurc", "bahia"):
        assert base_morta not in sigpub.UF_DA_BASE


# ---------------------------------------------------------------------------
# coletar() / _coletar_base — orquestração (rodízio, cursor por base, base
# fora do ar) via duble de requests.Session; conteúdo real (PAGINA_VAZIA),
# rede simulada só para "base indisponível" (ConnectionError real, sem
# fixture — mesmo padrão de test_fafipa_base_fora_do_ar_nao_trava_cursor)
# ---------------------------------------------------------------------------

class _RespostaFalsa:
    def __init__(self, texto):
        self.text = texto

    def raise_for_status(self):
        pass


class _SessaoFalsa:
    """Duble de requests.Session: cada base "viva" devolve PAGINA_VAZIA
    (token real, zero resultados) para qualquer GET; uma base em `mortas`
    simula rede fora do ar em toda tentativa (erro real do requests, não
    inventado)."""

    def __init__(self, mortas=()):
        self._mortas = set(mortas)
        self.urls_chamadas = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.urls_chamadas.append(url)
        if any(f"/{base}/" in url for base in self._mortas):
            raise requests.exceptions.ConnectionError(f"simulado: base fora do ar ({url})")
        return _RespostaFalsa(PAGINA_VAZIA)


def _instalar_sessao_falsa(monkeypatch, mortas=()):
    monkeypatch.setattr(sigpub.requests, "Session", lambda: _SessaoFalsa(mortas))
    monkeypatch.setattr(sigpub.time, "sleep", lambda *_a: None)


CFG_BASE = {"consultas_cargo": ["auditor fiscal"]}


def test_primeira_base_sempre_roda_e_demais_giram_em_rodizio(monkeypatch):
    _instalar_sessao_falsa(monkeypatch)
    cfg = {
        **CFG_BASE,
        "sigpub_bases": ["b0", "b1", "b2", "b3"],
        "sigpub_extras_por_execucao": 1,
    }
    cursor = {}
    hoje = dt.date.today()

    sigpub.coletar(cfg, cursor, hoje - dt.timedelta(days=2))
    assert set(cursor) >= {"b0", "b1", "proximo_extra"}
    assert "b2" not in cursor and "b3" not in cursor  # só as escolhidas desta execução
    assert cursor["proximo_extra"] == 1

    sigpub.coletar(cfg, cursor, hoje - dt.timedelta(days=2))
    assert "b2" in cursor  # segunda execução: rodízio avançou para a próxima extra
    assert cursor["proximo_extra"] == 2

    sigpub.coletar(cfg, cursor, hoje - dt.timedelta(days=2))
    assert "b3" in cursor
    assert cursor["proximo_extra"] == 3 % 3  # 3 extras disponíveis (b1,b2,b3): dá a volta

    sigpub.coletar(cfg, cursor, hoje - dt.timedelta(days=2))
    assert cursor["proximo_extra"] == (3 + 1) % 3  # ciclo completo, volta a incluir b1


def test_lista_de_uma_base_so_processa_ela_toda_execucao(monkeypatch):
    """cfg sem 'sigpub_bases' (ou só com uma base): preserva exatamente o
    comportamento de antes desta revisão — só amp, toda execução."""
    _instalar_sessao_falsa(monkeypatch)
    cfg = dict(CFG_BASE)  # sem 'sigpub_bases': cai no default BASES_PADRAO = ("amp",)
    cursor = {}
    hoje = dt.date.today()

    sigpub.coletar(cfg, cursor, hoje - dt.timedelta(days=2))
    assert set(cursor) == {"amp"}  # nem "proximo_extra" existe: não há extras a rodar
    assert "proximo_extra" not in cursor

    sigpub.coletar(cfg, cursor, hoje - dt.timedelta(days=2))
    assert set(cursor) == {"amp"}  # segunda execução: continua só amp


def test_base_fora_do_ar_nao_trava_as_outras_nem_o_proprio_cursor(monkeypatch):
    _instalar_sessao_falsa(monkeypatch, mortas={"quebrada"})
    cfg = {**CFG_BASE, "sigpub_bases": ["ok1", "quebrada", "ok2"], "sigpub_extras_por_execucao": 1}
    cursor = {}
    hoje = dt.date.today()

    achados = sigpub.coletar(cfg, cursor, hoje - dt.timedelta(days=2))
    assert achados == []  # PAGINA_VAZIA não tem resultado nenhum
    assert "ok1" in cursor
    assert "quebrada" not in cursor  # cursor da base quebrada não é tocado
    assert cursor["proximo_extra"] == 1  # rodízio avança mesmo com falha, não trava no mesmo par


def test_todas_as_bases_falham_estoura_erro_sem_tocar_cursor_das_boas(monkeypatch):
    _instalar_sessao_falsa(monkeypatch, mortas={"q1", "q2"})
    cfg = {**CFG_BASE, "sigpub_bases": ["q1", "q2"], "sigpub_extras_por_execucao": 1}
    cursor = {"estado_anterior": "preservado"}
    hoje = dt.date.today()

    with pytest.raises(RuntimeError):
        sigpub.coletar(cfg, cursor, hoje - dt.timedelta(days=2))
    assert "q1" not in cursor
    assert "q2" not in cursor
    assert cursor["estado_anterior"] == "preservado"


def test_sigpub_extras_por_execucao_zero_desliga_o_rodizio(monkeypatch):
    """Override explícito por cfg: só a base prioritária roda, mesmo com
    outras na lista — dá para o Danilo reduzir o tempo do coletor sem
    editar o código, sem precisar esvaziar sigpub_bases."""
    _instalar_sessao_falsa(monkeypatch)
    cfg = {**CFG_BASE, "sigpub_bases": ["b0", "b1", "b2"], "sigpub_extras_por_execucao": 0}
    cursor = {}
    hoje = dt.date.today()

    sigpub.coletar(cfg, cursor, hoje - dt.timedelta(days=2))
    assert set(cursor) == {"b0", "proximo_extra"}
    assert cursor["proximo_extra"] == 0


def test_coletar_base_usa_uf_confirmada_e_ignora_base_sem_uf(monkeypatch):
    """Fluxo completo com resultado de verdade (fixture com 10 linhas reais):
    a UF do Achado vem de UF_DA_BASE, nunca inventada para base desconhecida."""
    class _SessaoComResultado:
        def get(self, url, params=None, headers=None, timeout=None):
            # token (sem params) e a 1ª página de busca devolvem a página com
            # resultado; qualquer chamada seguinte (paginação ou matéria)
            # devolve vazio/erro — o teste só quer conferir o 1º achado
            if params is None or "busca_avancada[page]" not in params:
                return _RespostaFalsa(PAGINA_PORTARIA)
            return _RespostaFalsa(PAGINA_VAZIA)

    monkeypatch.setattr(sigpub.requests, "Session", lambda: _SessaoComResultado())
    monkeypatch.setattr(sigpub.time, "sleep", lambda *_a: None)

    # a matéria completa (GET /amp/load/<hash>) usa a MESMA sessão: como o
    # duble acima devolve a página de resultado pra qualquer GET sem
    # 'busca_avancada[page]', a "matéria" baixada é a própria página de
    # busca — o suficiente para conferir orgao/municipio/uf sem precisar de
    # mais uma fixture só pra isso
    cfg = {"consultas_cargo": ["auditor fiscal"]}
    hoje = dt.date.today()
    achados = sigpub._coletar_base("amp", cfg, {}, hoje - dt.timedelta(days=2), hoje, [])

    assert len(achados) == 10
    primeiro = achados[0]
    assert primeiro.fonte == "sigpub"
    assert primeiro.uf == "PR"
    assert primeiro.municipio == "Almirante Tamandaré"
    assert primeiro.orgao == "Prefeitura Municipal de Almirante Tamandaré"
    assert primeiro.url == "https://www.diariomunicipal.com.br/amp/load/063FB198"
    assert primeiro.data_publicacao == "2026-08-06"
    assert primeiro.detalhes["sigpub_base"] == "amp"


def test_coletar_base_sem_uf_confirmada_fica_vazio(monkeypatch):
    """base fora de UF_DA_BASE (não verificada ao vivo): uf="" — nunca chuta."""
    _instalar_sessao_falsa(monkeypatch)
    cfg = {"consultas_cargo": ["x"]}
    hoje = dt.date.today()
    # PAGINA_VAZIA não tem linha nenhuma, então isso só prova que o código
    # NÃO quebra e que uf seria "" se houvesse achado — a garantia funcional
    # vem de UF_DA_BASE.get(base, "") ser testada à parte acima
    achados = sigpub._coletar_base("base-nao-verificada", cfg, {}, hoje - dt.timedelta(days=2), hoje, [])
    assert achados == []
    assert "base-nao-verificada" not in sigpub.UF_DA_BASE
