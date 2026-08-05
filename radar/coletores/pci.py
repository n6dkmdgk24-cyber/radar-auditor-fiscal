"""Coletor do PCI Concursos via sitemap de notícias.

O PCI não tem RSS; o sitemap https://www.pciconcursos.com.br/smap_noticias.xml
é atualizado diariamente com as notícias recentes (robots.txt permissivo).

TODAS as notícias novas da janela são baixadas — o corpo é quem lista os
cargos, e o filtro fino é do Filtro central. Até 4.8.2026 havia um
pré-filtro por slug (título) que só baixava notícia "da área": a auditoria
de 5.8.2026 provou que ele cegou o radar para 16 aberturas em 5 dias úteis
("Prefeitura de X abre concurso com diversas vagas" não diz o cargo no
título — o Auditor Fiscal de Limeira/SP estava no corpo). O volume é baixo
(~25-40 notícias/dia útil) e o cursor reparte o custo entre execuções.
"""

import datetime as dt
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
LIMITE_PAGINAS = 80
PAUSA = 0.6
RX_UF_TITULO = re.compile(r"[-–(/]\s*([A-Z]{2})\b")
RX_ORGAO = re.compile(r"\b(Prefeitura(?: Municipal)? de|Câmara(?: Municipal)? de)\s+([A-ZÁÉÍÓÚÂÊÔÃÕÇ][^,–(]*?)(?:\s*[-–(,]|$)")


def _pagina(url):
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    sopa = BeautifulSoup(resp.text, "html.parser")
    titulo = sopa.title.get_text(strip=True) if sopa.title else url
    titulo = re.sub(r"\s*[-|]\s*PCI Concursos\s*$", "", titulo)
    # extrai SÓ o <article> (o corpo da notícia, com os cargos em <ul><li>);
    # a página inteira tem ~14k chars de menu/rodapé com links de outros
    # concursos, que contaminavam o texto com cargos alheios
    raiz = sopa.find("article") or sopa
    corpo = " ".join(el.get_text(" ", strip=True) for el in raiz.find_all(["p", "li"]))
    if not corpo:
        corpo = raiz.get_text(" ", strip=True)
    return titulo, corpo[:6000]


def coletar(cfg, cursor, desde_padrao):
    resp = requests.get(SITEMAP, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    raiz = ET.fromstring(resp.content)
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

    marco = max(cursor.get("lastmod_max", ""), desde_padrao.isoformat())
    # lastmod defeituoso (futuro) não pode empurrar o cursor e silenciar dias
    teto_lastmod = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    entradas = []
    for url_el in raiz.findall("sm:url", ns):
        loc = url_el.findtext("sm:loc", default="", namespaces=ns)
        lastmod = url_el.findtext("sm:lastmod", default="", namespaces=ns)[:10]
        if not loc or "/noticias/" not in loc:
            continue
        if not loc.rstrip("/").rsplit("/", 1)[-1] or loc.rstrip("/").endswith("/noticias"):
            continue  # página-índice de notícias, não uma notícia
        if lastmod and (lastmod < marco or lastmod > teto_lastmod):
            continue
        entradas.append((loc, lastmod))

    # mais antigas primeiro: o cursor avança só pelo que foi processado, então
    # um corte por limite adia o excedente para a próxima execução (sem perda)
    entradas.sort(key=lambda e: e[1])
    if len(entradas) > LIMITE_PAGINAS:
        print(
            f"[pci] aviso: {len(entradas)} páginas na janela, processando as "
            f"{LIMITE_PAGINAS} mais antigas (o resto fica para a próxima execução)"
        )
        entradas = entradas[:LIMITE_PAGINAS]

    achados = []
    maior_lastmod = cursor.get("lastmod_max", "")
    for loc, lastmod in entradas:
        try:
            titulo, corpo = _pagina(loc)
        except Exception as e:  # noqa: BLE001 — página fora do ar não pode travar o cursor
            print(f"[pci] aviso: página inacessível ({e!r}): {loc}")
            maior_lastmod = max(maior_lastmod, lastmod)
            continue
        maior_lastmod = max(maior_lastmod, lastmod)
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
        time.sleep(PAUSA)

    if maior_lastmod:
        cursor["lastmod_max"] = maior_lastmod
    return achados
