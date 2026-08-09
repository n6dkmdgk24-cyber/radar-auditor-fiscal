"""Coletor do RSS do Concursos no Brasil (concursosnobrasil.com/feed/).

Feed WordPress geral (todos os concursos, inclusive municipais pequenos);
não há pré-filtro por assunto — quem decide é o Filtro central. O feed só
guarda ~15 itens por página, então a coleta pagina com ?paged=N até
alcançar o marco da última execução.

O ARTIGO COMPLETO é baixado para cada item novo: o resumo do feed é só a
primeira frase do post e quase nunca nomeia os cargos (auditoria de
5.8.2026 — caso Contenda/PR: resumo dizia "11 vagas em oito ocupações"
e o "auditor fiscal de tributos" só existia no corpo do artigo).
"""

import re
import time

import feedparser
import requests
from bs4 import BeautifulSoup

from ..modelos import Achado

FEED = "https://concursosnobrasil.com/feed/"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9",
}
PAUSA_ARTIGO = 0.5
RX_UF = re.compile(r"/concursos/([a-z]{2})/")
# aceita "Prefeitura de X - PR", "Prefeitura de X (PR)" e variantes
RX_ORGAO = re.compile(
    r"\b(Prefeitura|Câmara)(?: Municipal)? de ([A-Za-zÀ-ÿ'’ -]+?)[\s\-–(]+([A-Z]{2})\b"
)
# entidade sem "Prefeitura de" na manchete do cnb ("Concurso CaraguaPrev (SP)
# abre vagas", "Concurso TCE-SP prorroga..."): sem ela a mesma notícia vinda
# do pci e do cnb vira dois cartões
RX_ENTIDADE = re.compile(
    r"^(?:Concurso|Seleção|Processo Seletivo)\s+"
    r"([A-ZÀ-Ú][\wÀ-ÿ.'’-]{1,24}(?:\s+[A-ZÀ-Ú][\wÀ-ÿ.'’-]{1,24}){0,2})"
    r"\s*[\(\-–]\s*([A-Z]{2})\b"
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


_LINKS_MAX = 8
# links de glossário/serviço do próprio portal, sem valor para o cartão
_LINKS_IGNORAR = ("concursosnobrasil.com", "/cdn-cgi/", "facebook.com", "twitter.com",
                  "whatsapp.com", "t.me/", "instagram.com")


def _corpo_artigo(url):
    """(corpo, links externos) do post; ("", []) se a página falhar —
    o item ainda entra com título+resumo, como antes da correção."""
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
            if not href.startswith("http") or any(x in href for x in _LINKS_IGNORAR):
                continue
            links.append([a_el.get_text(" ", strip=True)[:80], href])
            if len(links) >= _LINKS_MAX:
                break
        time.sleep(PAUSA_ARTIGO)
        return corpo[:6000], links
    except Exception as e:  # noqa: BLE001 — artigo fora do ar não derruba o feed
        print(f"[cnb] aviso: artigo inacessível ({e!r}): {url}")
        return "", []


def coletar(cfg, cursor, desde_padrao):
    ultimo_pub = cursor.get("ultimo_pub", "")
    desde_iso = f"{desde_padrao.isoformat()}T00:00:00"
    # cursor existente manda (recupera intervalo perdido após parada);
    # a janela padrão só inicializa a primeira execução
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
        m_uf = RX_UF.search(item.get("link", ""))
        m_org = RX_ORGAO.search(titulo)
        m_ent = None if m_org else RX_ENTIDADE.match(titulo)
        achados.append(
            Achado(
                fonte="cnb",
                titulo=titulo,
                url=item.get("link", ""),
                cargo_texto=f"{titulo}.\n{resumo}\n{corpo}",
                orgao=(
                    f"{m_org.group(1)} de {m_org.group(2).strip()}" if m_org
                    else (m_ent.group(1).strip() if m_ent else "")
                ),
                municipio=(m_org.group(2).strip() if m_org else ""),
                uf=(
                    m_org.group(3) if m_org
                    else (m_ent.group(2) if m_ent else (m_uf.group(1).upper() if m_uf else ""))
                ),
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
