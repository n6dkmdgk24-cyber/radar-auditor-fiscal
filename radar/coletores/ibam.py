"""Coletor da banca IBAM — Instituto Brasileiro de Administração Municipal.

Sob a marca "IBAM" há DUAS plataformas técnicas distintas, confirmado por
acesso direto em 13.8.2026 (o enunciado desta frente já previa essa
possibilidade: "Se forem diferentes, cubra o que der e registre a
pendência"):

- ibam-concursos.org.br: site próprio (Bootstrap), concursos do Rio de
  Janeiro/Nordeste/outros estados, tudo embutido numa ÚNICA página
  (accordion por concurso, sem página de detalhe separada);
- ibamsp-concursos.org.br (a "IBAM-SP"): roda o MESMO software white-label
  ProSeleta de radar/coletores/fafipa.py (o próprio <title> da página
  confirma "Software ProSeleta") — reaproveita `coletar_proseleta` de lá em
  vez de duplicar a raspagem de listagem/detalhe.

As duas entram sob um único fonte="ibam" (mesmo padrão de bases múltiplas
que selecao.py usa para várias bancas): falha numa não derruba a outra, e só
propaga erro (RuntimeError) se AMBAS falharem na mesma execução.

Achado importante em ibam-concursos.org.br: a home ("/") só lista uma
amostra; o filtro "Concursos em andamento" (?status=2) inclui concursos de
2024/2025 já com "Resultado final" — fora do escopo desta frente (achar o
edital ANTES do portal de notícia). O filtro certo, confirmado ao vivo, é
"?status=1" ("Edital disponível / Inscrições abertas"), que devolve
exatamente os mesmos cards da home — mas usar o parâmetro explícito não
depende de um comportamento padrão não documentado.
"""

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..modelos import Achado
from .fafipa import coletar_proseleta, edital_de_abertura, get_com_retry

BASE_RJ_PADRAO = "https://www.ibam-concursos.org.br"
BASE_SP_PADRAO = "https://www.ibamsp-concursos.org.br"

MAX_VISTOS_RJ = 300

# Entidade + "Cidade/UF" nos títulos do site RJ (ex.: "Município de
# Lages/SC - PS 02/2026", "Câmara Municipal de Penha/SC Edit. 01/26";
# confirmado ao vivo em 13.8.2026). Dois ramos em vez de UF opcional num só
# padrão: com "/UF" o nome da cidade termina exatamente aí (ramo 1); sem
# "/UF" no título ("Município de Casimiro de Abreu - Ed. 01/2026 PS") o
# nome só termina no separador " - " ou no fim da string (ramo 2) — fail-
# closed, não se adivinha a UF quando o título não a menciona. Com UF
# opcional num padrão só, "Penha/SC Edit. 01/26" (sem " - " depois da UF)
# não casava: a classe de caracteres da cidade aceita "-", então a captura
# tentava esticar até a PRÓXIMA barra ("01/26") em vez de parar em "/SC".
RX_ENTE_MUNICIPIO = re.compile(
    r"\b(Munic[íi]pio de|Prefeitura(?: Municipal)? de|C[âa]mara(?: Municipal)? de)\s*"
    r"(?:([A-Za-zÀ-ÿ][\wÀ-ÿ'.\- ]*?)\s*/\s*([A-Z]{2})\b"
    r"|([A-Za-zÀ-ÿ][\wÀ-ÿ'.\- ]*?)(?=\s*[-–]|\s*$))"
)


def _orgao_de_titulo_rj(titulo):
    m = RX_ENTE_MUNICIPIO.search(titulo)
    if not m:
        return "", "", ""
    cidade = (m.group(2) or m.group(4)).strip()
    orgao = f"{m.group(1)} {cidade}".strip()
    return orgao, cidade, (m.group(3) or "")


def _texto_rj(resp):
    """A página declara charset=windows-1252 (sem header HTTP de charset);
    utf-8 falha de cara em qualquer acento — confirmado ao vivo, 13.8.2026."""
    try:
        return resp.content.decode("utf-8")
    except UnicodeDecodeError:
        return resp.content.decode("cp1252")


def _card_id(container):
    alvo = container.find(attrs={"data-bs-target": re.compile(r"^#(?:cargos|docs)-\d+$")})
    if not alvo:
        return None
    return re.search(r"-(\d+)$", alvo["data-bs-target"]).group(1)


def _cartoes_rj(html):
    """[(id, título, descrição, cargos_div, docs_div)] de cada card em
    div.concurso-card — essa classe já exclui o modal de contato do site,
    que também usa <h4> para "SEDE IBAM"/"ESTADO DE SANTA CATARINA" etc."""
    sopa = BeautifulSoup(html, "html.parser")
    cartoes = []
    for container in sopa.select("div.concurso-card"):
        h4 = container.find("h4")
        cid = _card_id(container)
        if not h4 or not cid:
            continue
        titulo = h4.get_text(" ", strip=True)
        descricao_el = container.find("div", class_="fs-16")
        descricao = descricao_el.get_text(" ", strip=True) if descricao_el else ""
        cargos_div = container.find(id=f"cargos-{cid}")
        docs_div = container.find(id=f"docs-{cid}")
        cartoes.append((cid, titulo, descricao, cargos_div, docs_div))
    return cartoes


def _coletar_rj(cursor, base):
    """IBAM (Bootstrap): tudo embutido numa única página, sem página de
    detalhe por concurso — um só GET cobre a listagem inteira."""
    vistos = set(cursor.get("vistos", []))
    # falha de rede propaga sem tocar no cursor (fica intacto p/ próxima execução)
    resp = get_com_retry(f"{base}/?status=1", f"listagem de {base}")
    cartoes = _cartoes_rj(_texto_rj(resp))

    achados = []
    novos_vistos = []
    for cid, titulo, descricao, cargos_div, docs_div in cartoes:
        chave = f"{base}|{cid}"
        if chave in vistos:
            continue

        cargos_texto = cargos_div.get_text(" ", strip=True)[:3000] if cargos_div else ""
        cargo_texto = " ".join(p for p in (titulo, descricao, cargos_texto) if p)
        edital_url = edital_de_abertura(docs_div, base)
        site_insc = ""
        if cargos_div:
            a = cargos_div.find("a", href=re.compile(r"inscricao\.asp"))
            site_insc = urljoin(base + "/", a["href"]) if a else ""
        orgao, municipio, uf = _orgao_de_titulo_rj(titulo)

        det = {"banca": "IBAM", "id": cid, "base": base}
        if edital_url:
            det["edital_url"] = edital_url
        if site_insc:
            det["site_inscricao"] = site_insc

        achados.append(
            Achado(
                fonte="ibam",
                titulo=f"{titulo} — IBAM",
                url=f"{base}/?status=1#collapse-{cid}",
                cargo_texto=cargo_texto,
                orgao=orgao,
                municipio=municipio,
                uf=uf,
                detalhes=det,
            )
        )
        novos_vistos.append(chave)

    cursor["vistos"] = (list(cursor.get("vistos", [])) + novos_vistos)[-MAX_VISTOS_RJ:]
    return achados


def coletar(cfg, cursor, desde_padrao):
    # duas plataformas independentes sob a mesma banca (ver docstring do
    # módulo) — cada uma com seu cursor próprio, uma não trava a outra
    cursor.setdefault("rj", {})
    cursor.setdefault("sp", {})
    achados = []
    falha_rj = falha_sp = None

    try:
        base_rj = cfg.get("base_ibam_rj") or BASE_RJ_PADRAO
        achados.extend(_coletar_rj(cursor["rj"], base_rj))
    except Exception as e:  # noqa: BLE001 — uma plataforma fora do ar não derruba a outra
        falha_rj = e
        print(f"[ibam] aviso: coleta do IBAM (RJ) falhou ({e!r})")

    try:
        bases_sp = cfg.get("bancas_ibam_sp") or [BASE_SP_PADRAO]
        bases_sp = [{"url": b, "banca": "IBAM-SP", "uf_fixa": "SP"} for b in bases_sp]
        achados.extend(coletar_proseleta(bases_sp, cursor["sp"], fonte="ibam"))
    except Exception as e:  # noqa: BLE001 — idem, na outra direção
        falha_sp = e
        print(f"[ibam] aviso: coleta do IBAM-SP falhou ({e!r})")

    if falha_rj is not None and falha_sp is not None:
        raise RuntimeError(f"IBAM (RJ) e IBAM-SP falharam: {falha_rj!r} | {falha_sp!r}")
    return achados
