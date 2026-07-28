"""Coletor do PCI Concursos via sitemap de notícias.

O PCI não tem RSS; o sitemap https://www.pciconcursos.com.br/smap_noticias.xml
é atualizado diariamente com as notícias recentes (robots.txt permissivo).
Para não baixar centenas de páginas, um pré-filtro FOLGADO no slug seleciona
o que pode ser da área fiscal/controle; o filtro fino é do Filtro central.
"""

import re
import time
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup

from ..modelos import Achado

SITEMAP = "https://www.pciconcursos.com.br/smap_noticias.xml"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9",
}
LIMITE_PAGINAS = 60
PREFIXOS = ("fisca", "auditor", "tribut", "sefaz", "receita", "fazend", "control", "rendas")
TOKENS_EXATOS = {"iss", "tce", "tcu", "cgu", "tcm", "cge"}
RX_UF_TITULO = re.compile(r"[-–(/]\s*([A-Z]{2})\b")
RX_ORGAO = re.compile(r"\b(Prefeitura(?: Municipal)? de|Câmara(?: Municipal)? de)\s+([A-ZÁÉÍÓÚÂÊÔÃÕÇ][^,–(]*?)(?:\s*[-–(,]|$)")


def _slug_relevante(url):
    slug = url.rstrip("/").rsplit("/", 1)[-1]
    for token in slug.split("-"):
        if token in TOKENS_EXATOS or token.startswith(PREFIXOS):
            return True
    return False


def _pagina(url):
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    sopa = BeautifulSoup(resp.text, "html.parser")
    titulo = sopa.title.get_text(strip=True) if sopa.title else url
    titulo = re.sub(r"\s*[-|]\s*PCI Concursos\s*$", "", titulo)
    corpo = " ".join(p.get_text(" ", strip=True) for p in sopa.find_all("p"))
    return titulo, corpo[:3000]


def coletar(cfg, cursor, desde_padrao):
    resp = requests.get(SITEMAP, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    raiz = ET.fromstring(resp.content)
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

    marco = max(cursor.get("lastmod_max", ""), desde_padrao.isoformat())
    entradas = []
    maior_lastmod = cursor.get("lastmod_max", "")
    for url_el in raiz.findall("sm:url", ns):
        loc = url_el.findtext("sm:loc", default="", namespaces=ns)
        lastmod = url_el.findtext("sm:lastmod", default="", namespaces=ns)[:10]
        if not loc or "/noticias/" not in loc:
            continue
        maior_lastmod = max(maior_lastmod, lastmod)
        if lastmod and lastmod < marco:
            continue
        if _slug_relevante(loc):
            entradas.append((loc, lastmod))

    if len(entradas) > LIMITE_PAGINAS:
        print(f"[pci] aviso: {len(entradas)} páginas candidatas, limitando a {LIMITE_PAGINAS}")
        entradas = entradas[:LIMITE_PAGINAS]

    achados = []
    for loc, lastmod in entradas:
        titulo, corpo = _pagina(loc)
        m_uf = RX_UF_TITULO.search(titulo)
        m_org = RX_ORGAO.search(titulo) or RX_ORGAO.search(corpo[:600])
        achados.append(
            Achado(
                fonte="pci",
                titulo=titulo,
                url=loc,
                cargo_texto=corpo,
                orgao=(f"{m_org.group(1)} {m_org.group(2)}".strip() if m_org else ""),
                municipio=(m_org.group(2).strip() if m_org else ""),
                uf=(m_uf.group(1) if m_uf else ""),
                data_publicacao=lastmod,
            )
        )
        time.sleep(1)

    if maior_lastmod:
        cursor["lastmod_max"] = maior_lastmod
    return achados
