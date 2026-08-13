"""Coletor das bancas na plataforma white-label selecao.net.br (ProSeleta).

Várias bancas rodam o mesmo software: listagem de concursos abertos em
<base>/index/abertos/ (cards com link para /informacoes/<ID>/) e detalhe da
página em <base>/informacoes/<ID>/, com blocos fixos "Informações Gerais" e
"Vagas" (tabela cargo/qtde). O layout do card varia um pouco entre bancas
(título em <h3><a> ou em <a><p><strong>), por isso a extração tenta o <h3>
primeiro e cai para qualquer link de /informacoes/ com texto não-genérico.

Bases confirmadas por acesso direto em 29.7.2026 (todas responderam 200 em
/index/abertos/, embora idecan estivesse sem nenhum concurso aberto no
momento). Override opcional via cfg['bancas_selecao'] (lista de URLs-base).
"""

import re
import time

import requests
from bs4 import BeautifulSoup

from ..modelos import Achado

# A FAFIPA saiu daqui em 13.8.2026: ganhou coletor próprio (radar/coletores/
# fafipa.py), que extrai o PDF do edital de abertura. Se as duas fontes
# raspassem o mesmo site, o mesmo concurso viria duas vezes na mesma execução
# e a versão SEM o link do edital poderia vencer a corrida da deduplicação.
BASES_PADRAO = (
    "https://ameosc.selecao.net.br",
    "https://institutoaocp.selecao.net.br",
    "https://idecan.selecao.net.br",
    "https://fundep.selecao.net.br",
    "https://access.selecao.net.br",
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9",
}

LIMITE_DETALHES = 30  # páginas de detalhe por execução, somando todas as bases
MAX_VISTOS = 500

RX_ID = re.compile(r"/informacoes/(\d+)/")
RX_BOTAO = re.compile(r"mais informa|inscri|quantidade de vagas|^\d+$", re.IGNORECASE)
RX_GENERICO = re.compile(r"sistema de gerenciamento", re.IGNORECASE)
RX_ORGAO = re.compile(
    r"\b(Prefeitura(?: Municipal)? de|Câmara(?: Municipal)? de|Município de)\s+"
    r"([A-ZÁÉÍÓÚÂÊÔÃÕÇ][^,\-–(]*?)(?:\s*[-–(,]|$)"
)
RX_UF = re.compile(r"[-–(/]\s*([A-Z]{2})\b")


def _get(url, contexto):
    """GET com retry (3 tentativas, backoff); estoura na 3ª falha."""
    for tentativa in range(1, 4):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            if tentativa == 3:
                raise
            print(f"[selecao] aviso: {contexto} falhou ({e!r}), tentativa {tentativa}/3")
            time.sleep(5 * tentativa)


def _texto(resp):
    """Decodifica a resposta sem confiar no charset declarado.

    O header/meta oscila entre iso-8859-1 e utf-8 conforme a página (e mente
    às vezes: fundacaofafipa.org.br declara iso-8859-1 no header, mas a
    listagem /index/abertos/ vem em utf-8 de verdade, enquanto o detalhe
    /informacoes/<id>/ é iso-8859-1 legítimo). UTF-8 estrito serve de teste:
    texto acentuado em latin-1 quase nunca valida como utf-8 por acidente.
    """
    try:
        return resp.content.decode("utf-8")
    except UnicodeDecodeError:
        return resp.content.decode("iso-8859-1")


def _extrair_cards(html):
    """Extrai (id, título) dos cards de /index/abertos/, tolerando os dois layouts vistos."""
    sopa = BeautifulSoup(html, "html.parser")
    cards = {}
    for h3 in sopa.find_all("h3"):
        a = h3.find("a", href=RX_ID)
        if not a:
            continue
        m = RX_ID.search(a["href"])
        texto = a.get_text(" ", strip=True)
        if m and texto:
            cards[m.group(1)] = texto
    # variante sem <h3> (ex.: FUNDEP usa <a><p><strong>título</strong></p></a>):
    # aceita o primeiro link de /informacoes/<id>/ com texto substancial que
    # não seja um dos botões fixos do card (Mais Informações, Inscrições
    # Abertas!, Quantidade de Vagas, ou um número solto de vagas).
    for a in sopa.find_all("a", href=RX_ID):
        m = RX_ID.search(a["href"])
        if not m or m.group(1) in cards:
            continue
        texto = a.get_text(" ", strip=True)
        if texto and len(texto) > 8 and not RX_BOTAO.search(texto):
            cards[m.group(1)] = texto
    return list(cards.items())


def _nome_banca(sopa, base):
    candidatos = []
    dados = sopa.find(id="DadosEmpresa")
    if dados:
        b = dados.find("b")
        if b:
            candidatos.append(b.get_text(strip=True))
    if sopa.title:
        candidatos.append(sopa.title.get_text(strip=True))
    for c in candidatos:
        c = re.split(r"\s*\|\s*CNPJ", c, flags=re.IGNORECASE)[0].strip()
        if c and not RX_GENERICO.search(c):
            return c
    host = re.sub(r"^https?://", "", base).split("/")[0]
    return host.split(".")[0].upper()


def _texto_detalhe(html):
    """Texto útil da página de detalhe: tipo, título, informações gerais e vagas."""
    sopa = BeautifulSoup(html, "html.parser")
    tipo, titulo_detalhe = "", ""
    topo = sopa.find(id="TopoInformacoes")
    if topo:
        p_tipo = topo.find("p", class_="tipo")
        tipo = p_tipo.get_text(strip=True) if p_tipo else ""
        h2 = topo.find("h2")
        titulo_detalhe = h2.get_text(strip=True) if h2 else ""
    gerais = sopa.find(id="blocoInformacoesGerais")
    texto_gerais = gerais.get_text(" ", strip=True) if gerais else ""
    cargos = []
    vagas = sopa.find(id="blocoListaVagas")
    if vagas:
        for td in vagas.find_all("td", class_="cargo"):
            nome = " ".join(td.get_text(" ", strip=True).split())
            if nome and nome not in cargos:
                cargos.append(nome)
    partes = [tipo, titulo_detalhe, texto_gerais]
    if cargos:
        partes.append("Vagas: " + "; ".join(cargos))
    return " ".join(p for p in partes if p)[:3000]


def coletar(cfg, cursor, desde_padrao):
    # a listagem de abertos é sempre o estado atual (sem histórico por data);
    # desde_padrao não se aplica aqui — o corte é por ID já visto (cursor).
    bases = [b.rstrip("/") for b in (cfg.get("bancas_selecao") or BASES_PADRAO)]
    vistos = set(cursor.get("vistos", []))
    novos_vistos = []
    achados = []
    bases_ok = 0
    detalhes_processados = 0
    limite_avisado = False
    limite_atingido = False

    for base in bases:
        if limite_atingido:
            break
        try:
            resp = _get(f"{base}/index/abertos/", f"listagem de {base}")
        except requests.RequestException as e:
            print(f"[selecao] aviso: base {base} falhou ao listar abertos ({e!r}), pulando")
            continue
        bases_ok += 1
        html = _texto(resp)
        sopa = BeautifulSoup(html, "html.parser")
        banca = _nome_banca(sopa, base)
        cards = _extrair_cards(html)

        for cid, titulo_card in cards:
            chave = f"{base}|{cid}"
            if chave in vistos:
                continue
            if detalhes_processados >= LIMITE_DETALHES:
                if not limite_avisado:
                    print(
                        f"[selecao] aviso: limite de {LIMITE_DETALHES} páginas de "
                        "detalhe atingido, restante fica para a próxima execução"
                    )
                    limite_avisado = True
                limite_atingido = True
                break

            url_detalhe = f"{base}/informacoes/{cid}/"
            try:
                resp_d = _get(url_detalhe, f"detalhe {base}/{cid}")
            except requests.RequestException as e:
                print(f"[selecao] aviso: {url_detalhe} inacessível ({e!r}), tentando na próxima execução")
                continue
            detalhes_processados += 1
            time.sleep(1)

            texto = _texto_detalhe(_texto(resp_d))
            m_org = RX_ORGAO.search(titulo_card)
            m_uf = RX_UF.search(titulo_card)
            achados.append(
                Achado(
                    fonte="selecao",
                    titulo=f"{titulo_card} — {banca}",
                    url=url_detalhe,
                    cargo_texto=texto,
                    orgao=(f"{m_org.group(1)} {m_org.group(2)}".strip() if m_org else ""),
                    municipio=(m_org.group(2).strip() if m_org else ""),
                    uf=(m_uf.group(1) if m_uf else ""),
                    detalhes={"banca": banca, "id": cid, "base": base},
                )
            )
            novos_vistos.append(chave)
        time.sleep(1)

    if bases and bases_ok == 0:
        raise RuntimeError(f"todas as {len(bases)} bases selecao.net.br falharam")

    cursor["vistos"] = (list(cursor.get("vistos", [])) + novos_vistos)[-MAX_VISTOS:]
    return achados
