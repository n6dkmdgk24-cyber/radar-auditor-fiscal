"""Coletores dos portais de notícia Estratégia (radar/coletores/estrategia.py)
e Gran Cursos (radar/coletores/gran.py) — fontes usadas na newsletter do
Danilo.

Fixtures em tests/fixtures_html/, baixadas ao vivo em 13.8.2026:

- estrategia_feed_pagina1.xml: RSS completo (10 itens) de
  cj.estrategia.com/portal/feed/. ?paged=N NÃO pagina esse feed de verdade
  (paged=2 e paged=5 devolvem exatamente os mesmos 10 itens) — os testes de
  coletar() usam esse fato real em vez de simulá-lo.
- estrategia_pgm_riobranco_artigo.html: artigo real "Concurso PGM Rio Branco
  do Sul PR" (só o <article>, script/style removidos — o texto e os links
  são idênticos aos da página completa, conferido byte a byte antes do corte).
- gran_feed_pagina1_recorte.xml / _pagina2_recorte.xml: recorte de 5 + 3
  itens <item> reais, copiados verbatim das páginas 1 e 2 do RSS de
  blog.grancursosonline.com.br/feed/ (aqui ?paged=N pagina de verdade) —
  cobrem a fronteira real entre as duas páginas (mesmo timestamp de corte).
- gran_sefaz_al_artigo.html: artigo real "Concurso Sefaz AL: 100 vagas para
  Auditor! edital em breve" (mesmo corte de script/style do de cima).

Os testes de coletar() simulam falha de rede nos DEMAIS itens do feed (não
têm HTML congelado) para não inventar conteúdo de artigo — só o item com
fixture real tem corpo/links conferidos.
"""

import datetime as dt
from pathlib import Path

import feedparser
import pytest
import requests

from radar.coletores import estrategia, gran
from radar.filtro import Filtro

FIXTURES = Path(__file__).parent / "fixtures_html"


# --------------------------------------------------------------------------
# RX_ORGAO / RX_ENTIDADE — títulos reais observados nos dois feeds ao vivo
# em 13.8.2026. A dedupe por ente depende disso: prefira vazio a errar.
# --------------------------------------------------------------------------

def _extrai_ente(mod, titulo):
    m_org = mod.RX_ORGAO.search(titulo)
    m_ent = None if m_org else mod.RX_ENTIDADE.match(titulo)
    if m_org:
        orgao = f"{m_org.group(1)} de {m_org.group(2).strip()}"
        municipio = m_org.group(2).strip()
        uf = m_org.group(3)
    elif m_ent:
        orgao = m_ent.group("org").strip()
        municipio = ""
        uf = m_ent.group("uf1") or m_ent.group("uf2") or ""
    else:
        orgao, municipio, uf = "", "", ""
    return orgao, municipio, uf


CASOS_ENTE = [
    # "Concurso <ENTIDADE> <UF>:" — padrão predominante nos dois portais
    ("Concurso Sefaz AL: 100 vagas para Auditor! edital em breve",
     "Sefaz", "", "AL"),
    ("Concurso PGM Rio Branco do Sul PR: resultado final homologado!",
     "PGM Rio Branco do Sul", "", "PR"),
    ("Concurso Rio Verde de Mato Grosso MS: SAIU; 430 vagas.!",
     "Rio Verde de Mato Grosso", "", "MS"),
    ("Concurso SME Niterói RJ: edital avança. VEJA!",
     "SME Niterói", "", "RJ"),
    ("Concurso Águas Lindas GO: inscrições até 24/8!",
     "Águas Lindas", "", "GO"),
    ("Concurso Campina Grande PB: editais publicados; 995 vagas!",
     "Campina Grande", "", "PB"),
    # UF entre parênteses
    ("Concurso TRT 4 (RS): banca em breve; edital ainda em 2026!",
     "TRT 4", "", "RS"),
    # UF logo após a sigla, sem mais nada antes dos dois-pontos
    ("Concurso TRT RS: FCC poderá ser a banca organizadora!",
     "TRT", "", "RS"),
    # SEM UF reconhecível antes dos dois-pontos: fica vazio (CNU, SEDF, IASERJ
    # são conhecidos, mas nada no título aponta a UF — não é para adivinhar)
    ("Concurso CNU: acompanhe as principais atualizações e convocações!",
     "", "", ""),
    ("Concurso SEDF: 10.604 vagas ainda em 2026!", "", "", ""),
    ("Concurso IASERJ: extrato publicado! 61 vagas. Saiba mais!", "", "", ""),
    # "TJ PE Juiz:" — a UF (PE) não fica IMEDIATAMENTE antes dos dois-pontos
    # (o cargo "Juiz" fica no meio); um [A-Z]{2} genérico grudaria "Juiz" no
    # nome do órgão, então o resultado fica vazio (prefere vazio a errar)
    ("Concurso TJ PE Juiz: provas em setembro. Inicial de R$ 35,8 mil!",
     "", "", ""),
    ("Concurso TJ MS Cartório: acompanhe os resultados!", "", "", ""),
    # plural "Concursos" não casa a âncora "^Concurso " (é outro tipo de post)
    ("Concursos de Polícia: qual a idade limite para entrar na carreira?",
     "", "", ""),
]
# RX_ORGAO (padrão "Prefeitura/Câmara de X - UF") não aparece em nenhum
# título real observado nos dois feeds em 13.8.2026 — os dois portais vão
# direto ao nome do município ("Concurso Rio Verde de Mato Grosso MS"), sem
# escrever "Prefeitura de". A regex é copiada literalmente de cnb.RX_ORGAO,
# já coberta com títulos reais em tests/test_coletores.py; não há título
# real aqui para testar esse ramo sem inventar um.


@pytest.mark.parametrize("mod", [estrategia, gran], ids=["estrategia", "gran"])
@pytest.mark.parametrize(
    "titulo, orgao, municipio, uf", CASOS_ENTE,
    ids=[c[0][:45] for c in CASOS_ENTE],
)
def test_ente_no_titulo(mod, titulo, orgao, municipio, uf):
    assert _extrai_ente(mod, titulo) == (orgao, municipio, uf)


# --------------------------------------------------------------------------
# _link_util — hrefs reais extraídos dos artigos congelados
# --------------------------------------------------------------------------

def test_link_util_estrategia():
    f = estrategia._link_util
    # PDF do próprio domínio: é onde o Estratégia hospeda o edital (caso
    # PGM Rio Branco do Sul, 13.8.2026) — não pode ser descartado
    assert f("https://cj.estrategia.com/portal/wp-content/uploads/2026/02/x.pdf")
    # navegação do próprio domínio (não-PDF): sem valor para o cartão
    assert not f("https://cj.estrategia.com/portal/procuradoria/")
    assert not f("https://cj.estrategia.com/portal/author/coordenacao/")
    # redes sociais e link de venda/grupo de estudos: ruído
    assert not f("https://www.facebook.com/sharer.php?u=https://cj.estrategia.com/x")
    assert not f("https://x.com/share?text=abc")
    assert not f("https://sndflw.com/i/procuradorias-grupo")
    assert not f("https://www.estrategiaconcursos.com.br/assinaturas-ecj/")
    # banca externa: link de valor
    assert f("https://www.fundacaofafipa.org.br/informacoes/4142/")


def test_link_util_gran():
    f = gran._link_util
    # PDF do CDN do próprio grupo (subdomínio diferente do blog, mas mesmo
    # domínio-base): é onde o Gran hospeda o edital (caso Sefaz AL, 13.8.2026)
    assert f("https://blog-static.infra.grancursosonline.com.br/wp-content/uploads/2021/07/x.pdf")
    # navegação/venda do próprio grupo (não-PDF): sem valor para o cartão
    assert not f("https://blog.grancursosonline.com.br/concursos-2026/")
    assert not f("https://www.grancursosonline.com.br/assinatura-ilimitada")
    assert not f("https://questoes.grancursosonline.com.br/")
    # redes sociais: ruído
    assert not f("https://www.whatsapp.com/channel/0029Va4DxBC9RZAOHywAaQ23")
    assert not f("https://t.me/gconoticias")


# --------------------------------------------------------------------------
# _corpo_artigo — download + parsing do artigo congelado (requests.get
# trocado por um duble que serve o HTML real do disco)
# --------------------------------------------------------------------------

class _RespostaFalsa:
    def __init__(self, texto):
        self.text = texto

    def raise_for_status(self):
        pass


class _RequestsFalso:
    """Duble de `requests`: cada URL conhecida serve um HTML congelado do
    disco; qualquer URL fora do mapa simula falha de rede (sem inventar
    conteúdo de artigo que não foi baixado de verdade)."""

    def __init__(self, mapa):
        self._mapa = mapa

    def get(self, url, headers=None, timeout=None):
        if url in self._mapa:
            return _RespostaFalsa(self._mapa[url].read_text(encoding="utf-8"))
        raise requests.exceptions.ConnectionError(f"simulado: {url} indisponível no teste")


class _FeedparserFalso:
    """Duble de `feedparser`: `.parse(url)` ignora a URL de verdade e devolve
    o feed real gravado em disco, escolhido por `roteador(url)`."""

    def __init__(self, roteador):
        self._roteador = roteador

    def parse(self, url):
        return feedparser.parse(self._roteador(url))


class _TimeFalso:
    """Duble de `time`: `sleep` vira no-op (testes não esperam 0,5-1s por
    item), mas `strftime` continua real — é usado para converter a data de
    publicação do feed."""

    def sleep(self, *_a, **_k):
        pass

    def strftime(self, *args, **kwargs):
        import time as _time_real
        return _time_real.strftime(*args, **kwargs)


URL_PGM = "https://cj.estrategia.com/portal/concurso-pgm-rio-branco-do-sul-pr/"
URL_SEFAZ_AL = "https://blog.grancursosonline.com.br/concurso-sefaz-al/"


def test_corpo_artigo_estrategia_pgm_riobranco(monkeypatch):
    monkeypatch.setattr(
        estrategia, "requests",
        _RequestsFalso({URL_PGM: FIXTURES / "estrategia_pgm_riobranco_artigo.html"}),
    )
    corpo, links = estrategia._corpo_artigo(URL_PGM)

    assert "Fundação FAFIPA" in corpo
    assert "1 vaga para o cargo de Procurador" in corpo
    # os 4 primeiros PDFs do próprio domínio + a banca externa + o edital
    # original — na ordem em que aparecem no artigo (conferido em 13.8.2026)
    hrefs = [href for _texto, href in links]
    assert hrefs[0].endswith("homologacaopgmriobrancodosul.pdf")
    assert any("fundacaofafipa.org.br/informacoes/4142/" in h for h in hrefs)
    assert any(h.endswith("73ac82da-6a43-42cf-b853-da4eb2993643-1.pdf") for h in hrefs)
    assert all("facebook.com" not in h and "x.com" not in h for h in hrefs)
    assert len(links) == 8  # _LINKS_MAX


def test_corpo_artigo_gran_sefaz_al(monkeypatch):
    monkeypatch.setattr(
        gran, "requests",
        _RequestsFalso({URL_SEFAZ_AL: FIXTURES / "gran_sefaz_al_artigo.html"}),
    )
    corpo, links = gran._corpo_artigo(URL_SEFAZ_AL)

    assert "Auditor Fiscal da Administração Tributária Estadual" in corpo
    assert links == [
        ["Clique aqui para ver o último edital do concurso Sefaz AL",
         "https://blog-static.infra.grancursosonline.com.br/wp-content/uploads/2021/07/08095631/edital-sefaz-al-2021.pdf"],
        ["comissão alterada",
         "https://blog-static.infra.grancursosonline.com.br/wp-content/uploads/2026/07/08061057/concurso-sefaz-al-comissao-alterada-2026.pdf"],
    ]
    # a notícia real casa "auditor fiscal" no Filtro central (config real do
    # projeto) — prova de ponta a ponta que o corpo baixado alimenta a
    # triagem corretamente
    filtro = Filtro({
        "exclusao": ["auditor fiscal do trabalho"],
        "tributario": ["auditor fiscal"],
        "controle": [],
        "conferir": [],
    })
    categoria, termos = filtro.classificar(corpo)
    assert categoria == "tributario"
    assert "auditor fiscal" in termos


# --------------------------------------------------------------------------
# coletar() — pipeline completo com feed e artigo reais congelados
# --------------------------------------------------------------------------

def _instalar_dubles_estrategia(monkeypatch):
    monkeypatch.setattr(
        estrategia, "feedparser",
        _FeedparserFalso(lambda url: FIXTURES / "estrategia_feed_pagina1.xml"),
    )
    monkeypatch.setattr(
        estrategia, "requests",
        _RequestsFalso({URL_PGM: FIXTURES / "estrategia_pgm_riobranco_artigo.html"}),
    )
    monkeypatch.setattr(estrategia, "time", _TimeFalso())


def test_coletar_estrategia_pipeline_completo(monkeypatch, capsys):
    _instalar_dubles_estrategia(monkeypatch)
    cursor = {}
    # marco bem antigo: os 10 itens do feed (todos de 12-13.8.2026) entram
    # como novos, forçando o código a tentar a página 2 — e a real página 2
    # do Estratégia é idêntica à 1 (?paged= não pagina lá), então o teste
    # também comprova que _entradas_ate para sozinho nesse caso
    achados = estrategia.coletar({}, cursor, dt.date(2000, 1, 1))

    saida = capsys.readouterr().out
    assert "página 2 idêntica à anterior" in saida

    assert len(achados) == 10
    assert {a.fonte for a in achados} == {"estrategia"}
    # ordem do feed: mais novo primeiro
    assert achados[0].titulo.startswith("Concurso ALE RR Procurador")
    assert achados[0].orgao == "" and achados[0].uf == ""  # sem UF antes dos ':'

    pgm = next(a for a in achados if "PGM Rio Branco do Sul" in a.titulo)
    assert pgm.orgao == "PGM Rio Branco do Sul"
    assert pgm.uf == "PR"
    assert pgm.municipio == ""
    assert pgm.data_publicacao == "2026-08-13"
    assert "Fundação FAFIPA" in pgm.cargo_texto
    assert len(pgm.detalhes["links_artigo"]) == 8

    # os outros 9 itens não têm HTML congelado: o duble simula falha de
    # rede, e o item ainda entra com título+resumo (sem corpo, sem links) —
    # mesma resiliência do cnb.py. Nenhum deles deve carregar texto do
    # artigo do PGM (prova de que não houve contaminação entre itens).
    outros = [a for a in achados if a is not pgm]
    assert all(a.detalhes == {} for a in outros)
    assert all("Fundação FAFIPA" not in a.cargo_texto for a in outros)
    # resumo real do feed continua presente mesmo sem o corpo do artigo
    ale_rr = achados[0]
    assert "Confira os resultados preliminares" in ale_rr.cargo_texto

    assert cursor["ultimo_pub"] == "2026-08-13T04:03:36"
    assert len(cursor["guids"]) == 10

    # segunda execução com o cursor avançado: nada de novo, nenhum download
    # de artigo é tentado (senão o duble estouraria RuntimeError de rota
    # desconhecida para qualquer URL que não seja a do PGM)
    achados_2 = estrategia.coletar({}, cursor, dt.date(2000, 1, 1))
    assert achados_2 == []
    assert cursor["ultimo_pub"] == "2026-08-13T04:03:36"


def _instalar_dubles_gran(monkeypatch):
    def roteador(url):
        if "paged=2" in url:
            return FIXTURES / "gran_feed_pagina2_recorte.xml"
        return FIXTURES / "gran_feed_pagina1_recorte.xml"

    monkeypatch.setattr(gran, "feedparser", _FeedparserFalso(roteador))
    monkeypatch.setattr(
        gran, "requests",
        _RequestsFalso({URL_SEFAZ_AL: FIXTURES / "gran_sefaz_al_artigo.html"}),
    )
    monkeypatch.setattr(gran, "time", _TimeFalso())


def test_coletar_gran_pipeline_atravessa_paginas(monkeypatch):
    _instalar_dubles_gran(monkeypatch)
    # marco = item mais antigo do recorte da página 2 ("Banco do Nordeste",
    # 19:44:26): cruza as DUAS páginas de verdade (a página 1 real tem
    # paginação funcional aqui, ao contrário do Estratégia) — só o item
    # exatamente no marco fica de fora
    cursor = {"ultimo_pub": "2026-08-12T19:44:26", "guids": []}
    achados = gran.coletar({}, cursor, dt.date(2000, 1, 1))

    titulos = [a.titulo for a in achados]
    assert titulos == [
        "Concurso TJ MS Cartório: acompanhe os resultados!",
        "Concursos de Polícia: qual a idade limite para entrar na carreira?",
        "Concurso Sefaz AL: 100 vagas para Auditor! edital em breve",
        "Concurso Campina Grande PB: editais publicados; 995 vagas!",
        "Concurso Câmara Senador Canedo GO: edital retomado!",
        "Concurso SEDF: PGDF prevê possibilidade de edital até setembro",
        "Concurso TRT RS: FCC poderá ser a banca organizadora!",
    ]
    assert {a.fonte for a in achados} == {"gran"}

    sefaz = next(a for a in achados if "Sefaz AL" in a.titulo)
    assert sefaz.orgao == "Sefaz" and sefaz.uf == "AL" and sefaz.municipio == ""
    assert "Auditor Fiscal da Administração Tributária Estadual" in sefaz.cargo_texto
    assert len(sefaz.detalhes["links_artigo"]) == 2

    trt = next(a for a in achados if a.titulo.startswith("Concurso TRT RS"))
    assert trt.orgao == "TRT" and trt.uf == "RS"

    camara = next(a for a in achados if "Senador Canedo" in a.titulo)
    assert camara.orgao == "Câmara Senador Canedo" and camara.uf == "GO"

    # o mais novo entre as duas páginas vira o novo marco do cursor
    assert cursor["ultimo_pub"] == "2026-08-13T14:55:51"
    assert len(cursor["guids"]) == 7
