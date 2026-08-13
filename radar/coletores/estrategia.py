"""Coletor do RSS do blog Carreiras Jurídicas — Estratégia
(blog geral + carreira jurídica).

Feed WordPress de cursinho, focado em carreiras jurídicas (advocacia,
procuradoria, magistratura, defensoria) — a maioria dos posts é de concurso
fora do alvo do radar (auditoria de 13.8.2026: 1001 posts recentes do
sitemap, zero com "auditor fiscal"/"sefaz"/"receita" no slug). Mesmo assim
NÃO filtra por assunto aqui: quem decide é o Filtro central (mesma lição do
caso Contenda/PR em pci.py — pré-filtro por título cega o radar).

?paged=N NÃO pagina este feed (confirmado ao vivo em 13.8.2026: paged=2 e
paged=5 devolvem exatamente os mesmos 10 itens da página 1; /feed/page/2/
dá 404 e /page/2/feed/ redireciona). _entradas_ate ainda tenta paginar —
para não silenciar em código o dia em que o site passar a paginar de
verdade — mas para assim que uma página repete o guid inicial da anterior.

O ARTIGO COMPLETO é baixado para cada item novo, no mesmo espírito do cnb: o
resumo do feed é só a primeira frase (ex.: "Confira os resultados
preliminares...") e não nomeia cargo nem vagas.
"""

import re
import time

import feedparser
import requests
from bs4 import BeautifulSoup

from ..modelos import Achado

# Dois feeds do mesmo grupo: o geral (blog) cobre concurso de qualquer área e
# é de onde vêm os fiscais; o jurídico (cj) foi o primeiro implementado e ficou
# porque cobre carreiras que o geral às vezes não repete. Medição ao vivo de
# 13.8.2026: o jurídico devolveu 0 dos 10 itens no alvo do radar; o geral traz
# notícias como "Sefaz AL: 100 vagas para Auditor".
FEEDS = (
    "https://www.estrategiaconcursos.com.br/blog/feed/",
    "https://cj.estrategia.com/portal/feed/",
)
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
# "Concurso <ENTIDADE> (UF): <resto>" — a UF só é aceita quando vem
# IMEDIATAMENTE antes dos dois-pontos (a UF PRECISA ser reconhecida em
# _UFS: um [A-Z]{2} genérico casaria a sigla do próprio órgão, tipo "TJ" em
# "Concurso TJ PE Juiz:", e "Juiz" ficaria grudado no nome do órgão).
# org é preguiçoso (`?` no {0,5}) para não engolir a própria UF antes da
# checagem obrigatória do grupo de UF.
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
# domínios de venda/navegação do próprio grupo Estratégia — sem valor para o
# cartão (menu, autor, botão de assinatura). NÃO entra aqui o domínio do
# feed (cj.estrategia.com): o PDF do edital costuma estar hospedado em
# .../wp-content/uploads/ do PRÓPRIO domínio (caso PGM Rio Branco do
# Sul/PR, 13.8.2026) — por isso o próprio domínio só é descartado quando o
# link NÃO é um PDF (ver _link_util).
_DOMINIOS_PROPRIOS = ("cj.estrategia.com", "estrategiaconcursos.com.br")
_LINKS_IGNORAR_EXTRA = ("sndflw.com",)


def _link_util(href):
    if not href.startswith("http"):
        return False
    if any(r in href for r in _REDES_SOCIAIS) or any(r in href for r in _LINKS_IGNORAR_EXTRA):
        return False
    # o domínio do próprio grupo só serve quando é o PDF do edital hospedado
    # em /wp-content/uploads/ (caso PGM Rio Branco do Sul/PR, 13.8.2026)
    if any(d in href for d in _DOMINIOS_PROPRIOS) and not _RX_PDF.search(href):
        return False
    return True


def _entradas_ate(marco):
    """Itera as páginas de CADA feed, do mais novo ao mais antigo, até cruzar o
    marco (ou até uma página repetir a anterior — ver docstring do módulo).

    Um feed fora do ar não pode derrubar o outro: só é erro quando NENHUM dos
    feeds respondeu (aí o coletor inteiro falha e o Actions avisa).
    """
    algum_respondeu = False
    for feed_url in FEEDS:
        try:
            yield from _entradas_do_feed(feed_url, marco)
            algum_respondeu = True
        except RuntimeError as e:
            print(f"[estrategia] aviso: {feed_url} ilegível ({e})")
    if not algum_respondeu:
        raise RuntimeError("nenhum feed do Estratégia respondeu")


def _entradas_do_feed(FEED, marco):
    guid_inicial_anterior = None
    for pagina in range(1, MAX_PAGINAS + 1):
        url = FEED if pagina == 1 else f"{FEED}?paged={pagina}"
        feed = feedparser.parse(url)
        if feed.bozo and not feed.entries:
            if pagina == 1:
                raise RuntimeError(f"feed ilegível: {feed.bozo_exception!r}")
            return
        if not feed.entries:
            return
        guid_inicial = feed.entries[0].get("id") or feed.entries[0].get("link", "")
        if pagina > 1 and guid_inicial == guid_inicial_anterior:
            print(f"[estrategia] aviso: página {pagina} idêntica à anterior "
                  "(?paged= não pagina neste feed), parando")
            return
        guid_inicial_anterior = guid_inicial
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
    print(f"[estrategia] aviso: marco não alcançado em {MAX_PAGINAS} páginas do feed")


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
        print(f"[estrategia] aviso: artigo inacessível ({e!r}): {url}")
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
        # o conjunto acumula os guids DESTA execução também: são dois feeds do
        # mesmo grupo (blog geral e carreira jurídica) e uma notícia
        # republicada nos dois viraria dois achados do mesmo concurso
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
                fonte="estrategia",
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
        guids_vistos.add(guid)
        maior_pub = max(maior_pub, pub_iso)

    if maior_pub:
        cursor["ultimo_pub"] = maior_pub
    cursor["guids"] = (cursor.get("guids", []) + guids_novos)[-MAX_GUIDS:]
    return achados
