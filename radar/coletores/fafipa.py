"""Coletor da banca Fundação FAFIPA (https://fundacaofafipa.org.br/).

Fundação FAFIPA roda o mesmo software white-label ProSeleta usado por outras
bancas em radar/coletores/selecao.py (fundacaofafipa.org.br é, aliás, uma das
bases padrão de selecao.py) — e, confirmado ao vivo em 13.8.2026, também por
ibamsp-concursos.org.br (a "banca" IBAM-SP, ver radar/coletores/ibam.py). A
frente aqui é ESPECÍFICA da banca: o ganho principal, pedido explicitamente,
é publicar o achado já com o link direto do PDF do EDITAL DE ABERTURA — o
coletor genérico selecao.py só extrai texto solto, sem esse link.

Para não duplicar a raspagem de listagem/detalhe (idêntica nas duas bancas
ProSeleta observadas), `coletar_proseleta` abaixo é o núcleo comum: recebe a
lista de bases e devolve os achados, com edital_url e site_inscricao já
extraídos. radar/coletores/ibam.py importa esse núcleo para a base SP do
IBAM; não importa nada de selecao.py (que não é deste projeto) para evitar
acoplar a uma API privada de outro coletor.

Fluxo por base: GET <base>/index/abertos/ lista os concursos abertos (cards
com link para /informacoes/<id>/, confirmado por acesso direto que o layout
do card varia — <h3><a> ou <a><h3>, daí a extração dupla em `_extrair_cards`,
mesma estratégia de selecao.py). Cada id novo (fora do cursor) tem a página
de detalhe /informacoes/<id>/ visitada, de onde saem:
- cargo_texto: tipo + título + "Informações Gerais" + tabela de vagas;
- edital_url: PDF do "Edital de Abertura" dentro de #blocoPublicacoes, só
  quando NENHUM outro link concorrente casa o mesmo critério (ver
  `eh_edital_abertura` — fail-closed: na dúvida, vazio);
- site_inscricao: botão "Inscrição Online" de #TopoInformacoes .botoes,
  ausente quando as inscrições ainda não abriram.
"""

import re
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ..modelos import Achado

BASE_PADRAO = "https://fundacaofafipa.org.br"

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

# Mesmos padrões de selecao.py para o formato "Prefeitura/Câmara/Município
# de X - UF" — reimplementados aqui (não importados) porque selecao.py não é
# um arquivo desta frente; cobre FAFIPA integralmente. Títulos que não citam
# um desses três prefixos (ex.: "Instituto de Previdência ... de Matinhos -
# PR") ficam sem órgão/município — fail-closed, não é o caso de inventar.
RX_ORGAO = re.compile(
    r"\b(Prefeitura(?: Municipal)? de|Câmara(?: Municipal)? de|Município de)\s+"
    r"([A-ZÁÉÍÓÚÂÊÔÃÕÇ][^,\-–(]*?)(?:\s*[-–(,]|$)"
)
RX_UF = re.compile(r"[-–(/]\s*([A-Z]{2})\b")

# Títulos do IBAM-SP não usam "Prefeitura/Município de": o card já nasce com
# o nome do ente em CAIXA ALTA seguido de " - CONCURSO/PROCESSO..." (ex.:
# "MOGI MIRIM - CONCURSO PÚBLICO - 02/2026", confirmado ao vivo em
# 13.8.2026). Só entra quando a UF do site é fixa (site estadual) e o 1º
# segmento realmente antecede "CONCURSO"/"PROCESSO" — isso já descarta
# "VESTIBULAR - FACULDADE ..." (o 2º segmento é "FACULDADE", não casa).
RX_ENTE_CAIXA_ALTA = re.compile(r"^([A-ZÀ-Ú][A-ZÀ-Ú '\-]{1,40}?)\s*[-–]\s*(?:CONCURSO|PROCESSO)\b")

# Fail-closed na escolha do PDF do edital de abertura (regra nº 1 do
# produto): qualquer sinal de fase posterior rejeita, MESMO que "abertura"
# também apareça — caso real 13.8.2026, FAFIPA/Coronel Vivida (4205) e
# Foz do Iguaçu (4184): "Edital de abertura n.º 001/2026 (...) RETIFICADO"
# é a versão corrigida, não o edital de abertura original. "rerratific"
# cobre a variante "Rerratificação" (caso real: IBAM-SP/Santos, 185,
# "03- Rerratificação do Edital de Abertura." — não contém "retifica" nem
# "errata" como substring literal, por causa do "r" duplicado).
RX_REJEITA_EDITAL = re.compile(
    r"retifica|rerratific|errata|gabarito|resultado|convoca|homologa|anexo|cronograma",
    re.IGNORECASE,
)
# "edital de abertura" / "edital completo" em qualquer lugar do texto do link.
RX_ACEITA_ABERTURA = re.compile(r"edital.{0,40}?(abertura|completo)", re.IGNORECASE)
# "edital nº X" sem "abertura" no texto: aceita quando X é o edital Nº 1 da
# sequência (com ou sem zeros à esquerda: "1", "01", "01.001" contam como 1)
# — na prática, entre dezenas de casos reais observados (FAFIPA e IBAM,
# 13.8.2026), o edital de abertura é SEMPRE o primeiro da numeração, e
# "02", "03", "09"... são sempre fase posterior (deferimento, isenção,
# resultado...), mesmo quando o texto do link não usa nenhuma das palavras
# de fase da lista de rejeição acima (caso real: "Edital n.º 02.001/2026 -
# Deferimento das solicitações de isenção da taxa de inscrição", FAFIPA
# Água Comprida/4181 — "deferimento"/"isenção" não estão em RX_REJEITA; é o
# número "02" (não "01") que decide).
RX_NUMERO_EDITAL = re.compile(r"edital\b.{0,40}?(\d{1,3})(?:[.\-/]\d)", re.IGNORECASE)


def eh_edital_abertura(texto_link):
    """True quando o TEXTO de um link de anexo identifica o edital de
    ABERTURA (não uma fase posterior). Ver os RX_* acima para a régua."""
    t = " ".join((texto_link or "").split())
    if not t:
        return False
    if RX_REJEITA_EDITAL.search(t):
        return False
    if RX_ACEITA_ABERTURA.search(t):
        return True
    m = RX_NUMERO_EDITAL.search(t)
    return bool(m and int(m.group(1)) == 1)


def edital_de_abertura(raiz, base):
    """URL do PDF do edital de abertura entre os links de anexo em `raiz`
    (Tag do BeautifulSoup — tipicamente #blocoPublicacoes ou o painel de
    documentos do card). Vazio se nenhum ou mais de um link casar: mais de
    um é ambíguo, e a regra do produto é fail-closed (na dúvida, vazio)."""
    if raiz is None:
        return ""
    achados = []
    for a in raiz.find_all("a", href=True):
        texto = a.get_text(" ", strip=True) or a.get("data-astv", "")
        if eh_edital_abertura(texto):
            achados.append(urljoin(base + "/", a["href"]))
    if len(achados) > 1:
        print(
            f"[banca] aviso: {len(achados)} links casaram como edital de abertura em "
            f"{base}, ambíguo, deixando vazio (fail-closed)"
        )
        return ""
    return achados[0] if achados else ""


def orgao_de_titulo(titulo, uf_fixa=""):
    """(orgao, municipio, uf) a partir do título do card. `uf_fixa` é a UF
    do site quando ele cobre um único estado (ex.: "SP" no ibamsp-concursos,
    cujos títulos não repetem a UF); "" quando a UF vem do próprio título."""
    m_org = RX_ORGAO.search(titulo)
    m_uf = RX_UF.search(titulo)
    if m_org:
        orgao = f"{m_org.group(1)} {m_org.group(2)}".strip()
        return orgao, m_org.group(2).strip(), (m_uf.group(1) if m_uf else uf_fixa)
    if uf_fixa:
        m_ente = RX_ENTE_CAIXA_ALTA.match(titulo)
        if m_ente:
            ente = " ".join(w.capitalize() for w in m_ente.group(1).split())
            return ente, ente, uf_fixa
    return "", "", (m_uf.group(1) if m_uf else uf_fixa)


def site_inscricao(sopa, base):
    """Botão 'Inscrição Online' de #TopoInformacoes; vazio se as inscrições
    ainda não abriram (o botão simplesmente não existe nesse caso).

    A busca por div.botoes tem que ficar DENTRO de #TopoInformacoes: o
    cabeçalho do site também tem um <div class="item botoes"> com o link
    genérico "Área do Candidato" (/painel/), e sopa.find teria pego esse
    (o primeiro do documento) em vez do botão do concurso — bug real, só
    aparecia com a página inteira (pego na validação ao vivo de 13.8.2026,
    não nas fixtures de teste, que já recortavam a página a partir do
    próprio #TopoInformacoes).
    """
    topo = sopa.find(id="TopoInformacoes")
    if not topo:
        return ""
    botoes = topo.find("div", class_="botoes")
    if not botoes:
        return ""
    a = botoes.find("a", href=True)
    return urljoin(base + "/", a["href"]) if a else ""


def get_com_retry(url, contexto):
    """GET com retry (3 tentativas, backoff); estoura na 3ª falha.

    Sem underscore porque radar/coletores/ibam.py também usa (banca
    IBAM-RJ, que não roda ProSeleta mas reaproveita esse idioma de retry).
    """
    for tentativa in range(1, 4):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            if tentativa == 3:
                raise
            print(f"[fafipa] aviso: {contexto} falhou ({e!r}), tentativa {tentativa}/3")
            time.sleep(5 * tentativa)


def _texto(resp):
    """Decodifica sem confiar no charset declarado: a listagem
    /index/abertos/ vem em utf-8 de verdade mesmo declarando iso-8859-1 no
    header, e o detalhe /informacoes/<id>/ é iso-8859-1 legítimo — mesma
    observação de selecao.py (bases confirmadas em 29.7.2026)."""
    try:
        return resp.content.decode("utf-8")
    except UnicodeDecodeError:
        return resp.content.decode("iso-8859-1")


def _extrair_cards(html):
    """(id, título) dos cards de /index/abertos/, tolerando os dois layouts
    vistos ao vivo: <h3><a> (padrão) e <a><h3> (ex.: IBAM-SP/Mauá,
    13.8.2026) — mesma estratégia dupla de selecao.py."""
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
    for a in sopa.find_all("a", href=RX_ID):
        m = RX_ID.search(a["href"])
        if not m or m.group(1) in cards:
            continue
        texto = a.get_text(" ", strip=True)
        if texto and len(texto) > 8 and not RX_BOTAO.search(texto):
            cards[m.group(1)] = texto
    return list(cards.items())


def _texto_detalhe(sopa):
    """Texto útil da página de detalhe: tipo, título, informações gerais e
    tabela de vagas (mesma composição de selecao.py)."""
    tipo, titulo_detalhe = "", ""
    topo = sopa.find(id="TopoInformacoes")
    if topo:
        p_tipo = topo.find("p", class_="tipo")
        tipo = p_tipo.get_text(strip=True) if p_tipo else ""
        h2 = topo.find("h2")
        titulo_detalhe = h2.get_text(" ", strip=True) if h2 else ""
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


def coletar_proseleta(bases, cursor, fonte):
    """Núcleo comum às bancas ProSeleta (ver docstring do módulo).

    `bases`: [{"url", "banca", "uf_fixa"}, ...]. Falha de rede numa base
    não trava o cursor: só é propagada (RuntimeError) se TODAS as bases
    falharem na listagem, e mesmo assim o cursor não chega a ser
    sobrescrito com o que já tinha sido coletado antes de estourar.
    """
    vistos = set(cursor.get("vistos", []))
    novos_vistos = []
    achados = []
    bases_ok = 0
    detalhes_processados = 0
    limite_avisado = False
    limite_atingido = False

    for cfg_base in bases:
        if limite_atingido:
            break
        base = cfg_base["url"].rstrip("/")
        banca = cfg_base["banca"]
        uf_fixa = cfg_base.get("uf_fixa", "")
        try:
            resp = get_com_retry(f"{base}/index/abertos/", f"listagem de {base}")
        except requests.RequestException as e:
            print(f"[{fonte}] aviso: base {base} falhou ao listar abertos ({e!r}), pulando")
            continue
        bases_ok += 1
        cards = _extrair_cards(_texto(resp))

        for cid, titulo_card in cards:
            chave = f"{base}|{cid}"
            if chave in vistos:
                continue
            if detalhes_processados >= LIMITE_DETALHES:
                if not limite_avisado:
                    print(
                        f"[{fonte}] aviso: limite de {LIMITE_DETALHES} páginas de "
                        "detalhe atingido, restante fica para a próxima execução"
                    )
                    limite_avisado = True
                limite_atingido = True
                break

            url_detalhe = f"{base}/informacoes/{cid}/"
            try:
                resp_d = get_com_retry(url_detalhe, f"detalhe {base}/{cid}")
            except requests.RequestException as e:
                print(f"[{fonte}] aviso: {url_detalhe} inacessível ({e!r}), tentando na próxima execução")
                continue
            detalhes_processados += 1
            time.sleep(1)

            sopa_d = BeautifulSoup(_texto(resp_d), "html.parser")
            cargo_texto = _texto_detalhe(sopa_d)
            edital_url = edital_de_abertura(sopa_d.find(id="blocoPublicacoes"), base)
            site_insc = site_inscricao(sopa_d, base)
            orgao, municipio, uf = orgao_de_titulo(titulo_card, uf_fixa)

            det = {"banca": banca, "id": cid, "base": base}
            if edital_url:
                det["edital_url"] = edital_url
            if site_insc:
                det["site_inscricao"] = site_insc

            achados.append(
                Achado(
                    fonte=fonte,
                    titulo=f"{titulo_card} — {banca}",
                    url=url_detalhe,
                    cargo_texto=cargo_texto,
                    orgao=orgao,
                    municipio=municipio,
                    uf=uf,
                    detalhes=det,
                )
            )
            novos_vistos.append(chave)
        time.sleep(1)

    if bases and bases_ok == 0:
        raise RuntimeError(f"todas as {len(bases)} base(s) ({fonte}) falharam")

    cursor["vistos"] = (list(cursor.get("vistos", [])) + novos_vistos)[-MAX_VISTOS:]
    return achados


def coletar(cfg, cursor, desde_padrao):
    # a listagem de abertos é sempre o estado atual (sem histórico por
    # data); desde_padrao não se aplica — o corte é por ID já visto (cursor)
    bases_cfg = cfg.get("bancas_fafipa") or [BASE_PADRAO]
    bases = [{"url": b, "banca": "Fundação FAFIPA", "uf_fixa": ""} for b in bases_cfg]
    return coletar_proseleta(bases, cursor, fonte="fafipa")
