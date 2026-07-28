"""Coletor da busca do DOU (Seção 3, onde saem editais) em in.gov.br.

A página de consulta embute os resultados em JSON num <script id="params">
(mesma API que alimenta o buscador oficial; é o que o Ro-DOU do governo usa).
O in.gov.br oscila com acesso automatizado: cada consulta tem 3 tentativas com
backoff. Se TODAS as consultas falharem, estoura exceção (o main alerta).
Fallback futuro: INLABS (XML diário da Imprensa Nacional, cadastro gratuito).
"""

import datetime as dt
import json
import re
import time

import requests
from bs4 import BeautifulSoup

from ..filtro import normalizar
from ..modelos import Achado

# A Seção 3 menciona os cargos em atos que não são concurso (mercadoria
# abandonada, extratos de acordo, convocações sindicais); só interessa o que
# tem contexto de concurso/seleção.
RX_CONTEXTO = re.compile(r"concurso|processo seletivo|selecao publica|inscric")

BUSCA = "https://www.in.gov.br/consulta/-/buscar/dou"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9",
}
TENTATIVAS = 3


def _consultar(frase, de, ate):
    params = {
        "q": f'"{frase}"',
        "s": "do3",
        "exactDate": "personalizado",
        "publishFrom": de.strftime("%d-%m-%Y"),
        "publishTo": ate.strftime("%d-%m-%Y"),
        "sortType": "0",
        "delta": "50",
    }
    erro = None
    for tentativa in range(1, TENTATIVAS + 1):
        try:
            resp = requests.get(BUSCA, params=params, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            sopa = BeautifulSoup(resp.text, "html.parser")
            script = sopa.find(
                "script",
                id=lambda i: i and i.endswith("_params"),
                type="application/json",
            )
            if script is None or not script.string:
                raise RuntimeError("resposta sem o bloco JSON de resultados (script *_params)")
            return json.loads(script.string).get("jsonArray", [])
        except Exception as e:
            erro = e
            time.sleep(3 * tentativa)
    raise RuntimeError(f"consulta '{frase}' falhou após {TENTATIVAS} tentativas: {erro!r}")


def _data_iso(pub_date):
    try:
        return dt.datetime.strptime(pub_date, "%d/%m/%Y").strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return ""


def coletar(cfg, cursor, desde_padrao):
    hoje = dt.date.today()
    de = desde_padrao
    if cursor.get("ultima_data"):
        de = max(de, dt.date.fromisoformat(cursor["ultima_data"]) - dt.timedelta(days=1))

    achados, vistos, falhas = [], set(), []
    for frase in cfg["consultas_cargo"]:
        try:
            itens = _consultar(frase, de, hoje)
        except RuntimeError as e:
            falhas.append(str(e))
            continue
        for item in itens:
            chave = item.get("urlTitle") or item.get("classPK") or item.get("title", "")
            if not chave or chave in vistos:
                continue
            vistos.add(chave)
            orgao = item.get("hierarchyStr") or ""
            trecho = BeautifulSoup(item.get("content", ""), "html.parser").get_text(" ", strip=True)
            if not RX_CONTEXTO.search(normalizar(f"{item.get('title', '')} {trecho}")):
                continue
            achados.append(
                Achado(
                    fonte="dou",
                    titulo=f"{item.get('title', '').strip()} ({item.get('artType', 'DOU')})",
                    url=f"https://www.in.gov.br/web/dou/-/{item.get('urlTitle', '')}",
                    cargo_texto=f"{trecho}\n{orgao}",
                    orgao=orgao.split("/")[-1].strip() if orgao else "",
                    data_publicacao=_data_iso(item.get("pubDate", "")),
                    detalhes={"artType": item.get("artType", ""), "hierarquia": orgao},
                )
            )
        time.sleep(2)

    if falhas and len(falhas) == len(cfg["consultas_cargo"]):
        raise RuntimeError(f"todas as {len(falhas)} consultas ao DOU falharam; primeira: {falhas[0]}")
    if falhas:
        print(f"[dou] aviso: {len(falhas)} de {len(cfg['consultas_cargo'])} consultas falharam")

    cursor["ultima_data"] = hoje.isoformat()
    return achados
