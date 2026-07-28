"""Coletor do RSS do Concursos no Brasil (concursosnobrasil.com/feed/).

Feed WordPress geral (todos os concursos, inclusive municipais pequenos);
não há pré-filtro por assunto — quem decide é o Filtro central. O feed só
guarda ~15 itens (poucas horas de notícias), então a coleta pagina com
?paged=N até alcançar o marco da última execução.
"""

import re
import time

import feedparser
from bs4 import BeautifulSoup

from ..modelos import Achado

FEED = "https://concursosnobrasil.com/feed/"
RX_UF = re.compile(r"/concursos/([a-z]{2})/")
RX_ORGAO = re.compile(
    r"\b(Prefeitura|Câmara)(?: Municipal)? de ([A-Za-zÀ-ÿ' ]+?)[\s\-–]+([A-Z]{2})\b"
)
MAX_GUIDS = 400
MAX_PAGINAS = 12


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
    print(f"[cnb] aviso: marco não alcançado em {MAX_PAGINAS} páginas do feed")


def coletar(cfg, cursor, desde_padrao):
    ultimo_pub = cursor.get("ultimo_pub", "")
    desde_iso = f"{desde_padrao.isoformat()}T00:00:00"
    marco = max(ultimo_pub, desde_iso) if ultimo_pub else desde_iso
    guids_vistos = set(cursor.get("guids", []))

    achados, maior_pub, guids_novos = [], ultimo_pub, []
    for item, pub_iso in _entradas_ate(marco):
        guid = item.get("id") or item.get("link", "")
        if guid in guids_vistos:
            continue
        resumo = BeautifulSoup(item.get("summary", ""), "html.parser").get_text(" ", strip=True)
        titulo = item.get("title", "")
        m_uf = RX_UF.search(item.get("link", ""))
        m_org = RX_ORGAO.search(titulo)
        achados.append(
            Achado(
                fonte="cnb",
                titulo=titulo,
                url=item.get("link", ""),
                cargo_texto=f"{titulo}\n{resumo}",
                orgao=(f"{m_org.group(1)} de {m_org.group(2).strip()}" if m_org else ""),
                municipio=(m_org.group(2).strip() if m_org else ""),
                uf=(m_org.group(3) if m_org else (m_uf.group(1).upper() if m_uf else "")),
                data_publicacao=pub_iso[:10],
            )
        )
        guids_novos.append(guid)
        maior_pub = max(maior_pub, pub_iso)

    if maior_pub:
        cursor["ultimo_pub"] = maior_pub
    cursor["guids"] = (cursor.get("guids", []) + guids_novos)[-MAX_GUIDS:]
    return achados
