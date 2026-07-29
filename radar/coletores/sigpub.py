"""Coletor do SIGPub — Diário Oficial dos Municípios do Paraná (AMP).

Busca pública em https://www.diariomunicipal.com.br/amp/pesquisar, formulário
GET sem login (name="busca_avancada"). Os campos relevantes são
busca_avancada[texto] (palavra-chave), busca_avancada[dataInicio]/[dataFim]
(dd/mm/aaaa, intervalo precisa ser inferior a 6 meses) e busca_avancada[_token]
(CSRF do formulário, obtido uma vez por sessão via GET simples e reaproveitado
em todas as consultas seguintes). A paginação usa busca_avancada[page]=N,
11 resultados por página.

A busca do SIGPub NÃO é por frase exata: é uma correspondência solta dos
termos da palavra-chave (confirmado buscando "auditor fiscal" e encontrando
matérias com "auditoria" e "fiscalizar" em contextos não relacionados, sem a
frase completa). Por isso cada resultado tem sua matéria completa buscada
(GET /amp/load/<hash>, que redireciona para a página do texto) e o trecho de
~300 caracteres é montado a partir da primeira ocorrência real de alguma
frase de cfg['consultas_cargo'] no texto integral, e não do que a busca
"disse" ter encontrado.
"""

import datetime as dt
import re
import time

import requests
from bs4 import BeautifulSoup

from ..filtro import _frase_para_regex, normalizar
from ..modelos import Achado

BASE = "https://www.diariomunicipal.com.br/amp"
PESQUISAR = f"{BASE}/pesquisar"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9",
}
TENTATIVAS = 3
JANELA_MAX_DIAS = 179  # o formulário rejeita intervalo de datas >= 6 meses
LIMITE_ITENS = 80
POR_PAGINA = 11  # observado no datatable do SIGPub

RX_HASH = re.compile(r"/amp/load/([0-9A-Fa-f]+)")
RX_TOKEN = re.compile(r'name="busca_avancada\[_token\]"\s+value="([^"]*)"')
RX_ENTIDADE_MUNICIPIO = re.compile(
    r"^(?:PREFEITURA(?:\s+MUNICIPAL)?\s+DE|MUNIC[ÍI]PIO\s+DE|C[ÂA]MARA(?:\s+MUNICIPAL)?\s+DE)\s+(.+)$",
    re.IGNORECASE,
)


def _get_com_retry(sessao, url, params=None):
    for tentativa in range(1, TENTATIVAS + 1):
        try:
            resp = sessao.get(url, params=params, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            if tentativa == TENTATIVAS:
                raise
            print(f"[sigpub] aviso: requisição falhou ({e!r}), tentativa {tentativa}/{TENTATIVAS}")
            time.sleep(3 * tentativa)


def _sessao_e_token():
    sessao = requests.Session()
    resp = _get_com_retry(sessao, PESQUISAR)
    m = RX_TOKEN.search(resp.text)
    if not m:
        raise RuntimeError("token CSRF não encontrado no formulário de busca do SIGPub")
    return sessao, m.group(1)


def _municipio_de_entidade(entidade):
    m = RX_ENTIDADE_MUNICIPIO.match(entidade.strip())
    return m.group(1).strip().title() if m else ""


def _linhas_resultado(html):
    sopa = BeautifulSoup(html, "html.parser")
    tabela = sopa.find("table", id="datatable")
    if not tabela or not tabela.find("tbody"):
        return []
    linhas = []
    for tr in tabela.find("tbody").find_all("tr"):
        celulas = tr.find_all("td")
        if len(celulas) < 4:
            continue
        link = celulas[0].find("a")
        m_hash = RX_HASH.search(link["href"]) if link and link.get("href") else None
        if not m_hash:
            continue
        linhas.append(
            {
                "hash": m_hash.group(1),
                "entidade": celulas[0].get_text(strip=True),
                "titulo": celulas[1].get_text(strip=True),
                "orgao": celulas[2].get_text(strip=True),
                "data": celulas[3].get_text(strip=True),  # dd-mm-aaaa
            }
        )
    return linhas


def _data_iso(data_br):
    try:
        return dt.datetime.strptime(data_br, "%d-%m-%Y").strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return ""


def _texto_materia(sessao, hash_id):
    resp = _get_com_retry(sessao, f"{BASE}/load/{hash_id}")
    sopa = BeautifulSoup(resp.text, "html.parser")
    corpo = sopa.find(id="materia")
    return corpo.get_text(" ", strip=True) if corpo else sopa.get_text(" ", strip=True)


def _melhor_trecho(texto, rx_cargos):
    """~300 chars ao redor da 1ª frase de cargo achada no texto integral, senão o início dele."""
    t = normalizar(texto)
    for rx in rx_cargos:
        m = rx.search(t)
        if m:
            ini = max(0, m.start() - 150)
            return " ".join(texto[ini : m.end() + 150].split())[:300]
    return " ".join(texto.split())[:300]


def coletar(cfg, cursor, desde_padrao):
    hoje = dt.date.today()
    de = desde_padrao
    if cursor.get("ultima_data"):
        de = max(de, dt.date.fromisoformat(cursor["ultima_data"]))
    if (hoje - de).days > JANELA_MAX_DIAS:
        de = hoje - dt.timedelta(days=JANELA_MAX_DIAS)
        print(f"[sigpub] aviso: janela maior que {JANELA_MAX_DIAS} dias, limitada a partir de {de.isoformat()}")

    sessao, token = _sessao_e_token()
    rx_cargos = [_frase_para_regex(c) for c in cfg["consultas_cargo"]]

    achados, vistos, falhas = [], set(), []
    limite_atingido = False
    for frase in cfg["consultas_cargo"]:
        if limite_atingido:
            break
        params_base = {
            "busca_avancada[texto]": frase,
            "busca_avancada[dataInicio]": de.strftime("%d/%m/%Y"),
            "busca_avancada[dataFim]": hoje.strftime("%d/%m/%Y"),
            "busca_avancada[_token]": token,
        }
        pagina = 1
        try:
            while True:
                params = dict(params_base)
                if pagina > 1:
                    params["busca_avancada[page]"] = pagina
                resp = _get_com_retry(sessao, PESQUISAR, params=params)
                linhas = _linhas_resultado(resp.text)
                if not linhas:
                    break
                for linha in linhas:
                    if linha["hash"] in vistos:
                        continue
                    vistos.add(linha["hash"])
                    if len(achados) >= LIMITE_ITENS:
                        print(f"[sigpub] aviso: limite de {LIMITE_ITENS} itens atingido, coleta interrompida")
                        limite_atingido = True
                        break
                    time.sleep(1)
                    try:
                        texto = _texto_materia(sessao, linha["hash"])
                    except requests.RequestException as e:
                        print(f"[sigpub] aviso: matéria {linha['hash']} inacessível ({e!r}), pulando")
                        continue
                    trecho = _melhor_trecho(texto, rx_cargos)
                    data_iso = _data_iso(linha["data"])
                    achados.append(
                        Achado(
                            fonte="sigpub",
                            titulo=f"{linha['entidade']} — {linha['titulo']} ({data_iso or linha['data']})",
                            url=f"{BASE}/load/{linha['hash']}",
                            cargo_texto=texto[:3000],
                            orgao=linha["entidade"],
                            municipio=_municipio_de_entidade(linha["entidade"]),
                            uf="PR",
                            data_publicacao=data_iso,
                            detalhes={
                                "trecho": trecho,
                                "contexto_fraco": True,
                                "orgao_interno": linha["orgao"],
                            },
                        )
                    )
                if limite_atingido:
                    break
                if len(linhas) < POR_PAGINA:
                    break
                pagina += 1
                time.sleep(1)
        except requests.RequestException as e:
            falhas.append(f"'{frase}': {e!r}")
            continue
        time.sleep(1)

    if falhas and len(falhas) == len(cfg["consultas_cargo"]):
        raise RuntimeError(f"todas as {len(falhas)} consultas ao SIGPub falharam; primeira: {falhas[0]}")
    if falhas:
        print(f"[sigpub] aviso: {len(falhas)} de {len(cfg['consultas_cargo'])} consultas falharam")

    cursor["ultima_data"] = hoje.isoformat()
    return achados
