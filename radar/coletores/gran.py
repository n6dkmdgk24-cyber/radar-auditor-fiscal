"""Coletor do RSS do Blog Gran Cursos Online
(blog.grancursosonline.com.br/feed/).

Feed WordPress geral de concursos públicos (não só carreiras jurídicas) —
cobre com frequência Receita Federal, SEFAZ estaduais e controle interno
(confirmado ao vivo em 13.8.2026: sitemap recente tem posts como
"receita-federal-inscricoes-abertas-278-vagas-de-auditor-fiscal" e
"publicado-edital-para-auditor-de-controle-interno"), mas também traz muito
concurso fora do alvo (polícia, magistratura, educação). Não filtra por
assunto aqui: quem decide é o Filtro central (mesma lição do caso
Contenda/PR em pci.py — pré-filtro por título cega o radar).

Ao contrário do feed do Estratégia (radar/coletores/estrategia.py),
?paged=N pagina de verdade aqui (confirmado ao vivo: paged=1/5/10/15
devolvem 30 itens cada, com datas decrescentes) — a paginação segue o
mesmo modelo do cnb.

O ARTIGO COMPLETO é baixado para cada item novo: o resumo do feed é a
legenda da imagem de capa, não o corpo da notícia.
"""

import re
import time

import feedparser
import requests
from bs4 import BeautifulSoup

from ..modelos import Achado

FEED = "https://blog.grancursosonline.com.br/feed/"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9",
}
PAUSA_ARTIGO = 0.5
MAX_GUIDS = 400
MAX_PAGINAS = 12

_UFS = ("AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS",
        "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC",
        "SP", "SE", "TO")
_UF_ALT = "|".join(_UFS)
# partículas que continuam um nome de município sem quebrar a captura
# ("Rio Verde de Mato Grosso", "Conceição do Mato Dentro")
_PARTICULA = r"(?:de|da|do|dos|das)"

# padrão "Prefeitura/Câmara/Município de X - UF" — raro aqui (o site quase
# nunca escreve "Prefeitura de", vai direto ao nome do município: caso
# "Concurso Rio Verde de Mato Grosso MS"), mas checado primeiro por ser
# inequívoco quando aparece. Espelha cnb.RX_ORGAO (3 grupos: prefixo,
# município, UF).
RX_ORGAO = re.compile(
    r"\b(Prefeitura|Câmara)(?: Municipal)? de ([A-Za-zÀ-ÿ'’ -]+?)[\s\-–(]+([A-Z]{2})\b"
)
# título padrão do site: "Concurso <ENTIDADE> [UF]: <resto>" ou
# "Concurso <ENTIDADE> (UF): <resto>" (casos reais: "Concurso Sefaz AL: 100
# vagas...", "Concurso SME Niterói RJ: edital avança", "Concurso TRT 4
# (RS): banca em breve") — a UF só é aceita quando vem IMEDIATAMENTE antes
# dos dois-pontos e é reconhecida em _UFS: um [A-Z]{2} genérico casaria a
# sigla do próprio órgão, tipo "PE" em "Concurso TJ PE Juiz:", e "Juiz"
# ficaria grudado no nome do órgão. org é preguiçoso (`?` no {0,5}) para
# não engolir a própria UF antes da checagem obrigatória do grupo de UF.
RX_ENTIDADE = re.compile(
    rf"^Concurso\s+(?P<org>[A-ZÀ-Ú][\wÀ-ÿ.'-]*"
    rf"(?:\s+(?:{_PARTICULA}|[A-ZÀ-Ú0-9][\wÀ-ÿ.'-]*)){{0,5}}?)"
    rf"\s*(?:\(\s*(?P<uf1>{_UF_ALT})\b\s*\)|\s+(?P<uf2>{_UF_ALT})\b)"
    rf"\s*:"
)

_LINKS_MAX = 8
_RX_PDF = re.compile(r"\.pdf(?:[?#]|$)", re.I)
_REDES_SOCIAIS = ("facebook.com", "twitter.com", "x.com/", "linkedin.com",
                   "whatsapp.com", "t.me/", "instagram.com", "youtube.com",
                   "/cdn-cgi/")
# domínio (e subdomínios) do próprio grupo Gran — sem valor para o cartão
# quando é menu/tag/venda de curso, MAS o PDF do edital costuma estar
# hospedado no CDN do próprio grupo (blog-static.infra.grancursosonline.com.br,
# caso Sefaz AL, 13.8.2026: link "Clique aqui para ver o último edital..."
# aponta para lá) — por isso o próprio domínio só é descartado quando o
# link NÃO é um PDF (ver _link_util). A substring cobre blog., www.,
# concursos., questoes. e blog-static.infra. de uma vez.
_DOMINIO_PROPRIO = "grancursosonline.com.br"


def _link_util(href):
    if not href.startswith("http"):
        return False
    if any(r in href for r in _REDES_SOCIAIS):
        return False
    if _DOMINIO_PROPRIO in href and not _RX_PDF.search(href):
        return False
    return True


def _entradas_ate(marco):
    """Itera as páginas do feed do mais novo ao mais antigo até cruzar o marco."""
    for pagina in range(1, MAX_PAGINAS + 1):
        url = FEED if pagina == 1 else f"{FEED}?paged={pagina}"
        feed = feedparser.parse(url)
        if feed.bozo and not feed.entries:
            if pagina == 1:
                raise RuntimeError(f"feed ilegível: {feed.bozo_exception!r}")
            return
        if not feed.entries:
            return
        alcancou_marco = False
        for item in feed.entries:
            pub = item.get("published_parsed")
            pub_iso = time.strftime("%Y-%m-%dT%H:%M:%S", pub) if pub else ""
            if pub_iso and pub_iso <= marco:
                alcancou_marco = True
                continue
            yield item, pub_iso
        if alcancou_marco:
            return
        time.sleep(1)
    print(f"[gran] aviso: marco não alcançado em {MAX_PAGINAS} páginas do feed")


def _corpo_artigo(url):
    """(corpo, links externos) do post; ("", []) se a página falhar — o item
    ainda entra com título+resumo, como no cnb."""
    if not url:
        return "", []
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        sopa = BeautifulSoup(resp.text, "html.parser")
        raiz = sopa.find("article") or sopa
        corpo = " ".join(el.get_text(" ", strip=True) for el in raiz.find_all(["p", "li"]))
        links = []
        for a_el in raiz.find_all("a", href=True):
            href = a_el["href"]
            if not _link_util(href):
                continue
            links.append([a_el.get_text(" ", strip=True)[:80], href])
            if len(links) >= _LINKS_MAX:
                break
        time.sleep(PAUSA_ARTIGO)
        return corpo[:6000], links
    except Exception as e:  # noqa: BLE001 — artigo fora do ar não derruba o feed
        print(f"[gran] aviso: artigo inacessível ({e!r}): {url}")
        return "", []


def coletar(cfg, cursor, desde_padrao):
    ultimo_pub = cursor.get("ultimo_pub", "")
    desde_iso = f"{desde_padrao.isoformat()}T00:00:00"
    # cursor existente manda (recupera intervalo perdido após parada); a
    # janela padrão só inicializa a primeira execução
    marco = ultimo_pub or desde_iso
    guids_vistos = set(cursor.get("guids", []))

    achados, maior_pub, guids_novos = [], ultimo_pub, []
    for item, pub_iso in _entradas_ate(marco):
        guid = item.get("id") or item.get("link", "")
        if guid in guids_vistos:
            continue
        resumo = BeautifulSoup(item.get("summary", ""), "html.parser").get_text(" ", strip=True)
        titulo = item.get("title", "")
        corpo, links = _corpo_artigo(item.get("link", ""))
        m_org = RX_ORGAO.search(titulo)
        m_ent = None if m_org else RX_ENTIDADE.match(titulo)
        uf_ent = (m_ent.group("uf1") or m_ent.group("uf2")) if m_ent else ""
        achados.append(
            Achado(
                fonte="gran",
                titulo=titulo,
                url=item.get("link", ""),
                cargo_texto=f"{titulo}.\n{resumo}\n{corpo}",
                orgao=(
                    f"{m_org.group(1)} de {m_org.group(2).strip()}" if m_org
                    else (m_ent.group("org").strip() if m_ent else "")
                ),
                municipio=(m_org.group(2).strip() if m_org else ""),
                uf=(m_org.group(3) if m_org else uf_ent),
                data_publicacao=pub_iso[:10],
                detalhes={"links_artigo": links} if links else {},
            )
        )
        guids_novos.append(guid)
        maior_pub = max(maior_pub, pub_iso)

    if maior_pub:
        cursor["ultimo_pub"] = maior_pub
    cursor["guids"] = (cursor.get("guids", []) + guids_novos)[-MAX_GUIDS:]
    return achados
