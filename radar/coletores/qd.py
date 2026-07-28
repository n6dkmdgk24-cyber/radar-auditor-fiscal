"""Coletor do Querido Diário (diários oficiais municipais, Open Knowledge Brasil).

API: GET https://api.queridodiario.ok.org.br/gazettes — pública, sem chave.
A querystring usa a sintaxe "simple query string" do OpenSearch: frases entre
aspas, | é OU, + é E, parênteses agrupam. A consulta exige a coocorrência de
("concurso público" | "processo seletivo") com pelo menos uma frase de cargo
de cfg['consultas_cargo']. Cortesia da API: ~60 req/min.
"""

import datetime as dt
import re
import time

import requests

from ..modelos import Achado

API = "https://api.queridodiario.ok.org.br/gazettes"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}
TAM_PAGINA = 100
LIMITE_ITENS = 500
SOBREPOSICAO_HORAS = 12  # re-olha o fim da janela anterior (indexação atrasada)
RX_TAGS = re.compile(r"</?[^>]+>")


def _querystring(cfg):
    cargos = " | ".join(f'"{c}"' for c in cfg["consultas_cargo"])
    return f'("concurso público" | "processo seletivo") + ({cargos})'


def coletar(cfg, cursor, desde_padrao):
    params = {
        "querystring": _querystring(cfg),
        "size": TAM_PAGINA,
        "offset": 0,
        "number_of_excerpts": 3,
        "excerpt_size": 400,
        "sort_by": "descending_date",
    }
    if cursor.get("scraped_since"):
        marco = dt.datetime.fromisoformat(cursor["scraped_since"])
        params["scraped_since"] = (
            marco - dt.timedelta(hours=SOBREPOSICAO_HORAS)
        ).isoformat(timespec="seconds")
    else:
        params["published_since"] = desde_padrao.isoformat()

    achados = []
    while True:
        resp = requests.get(API, params=params, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        pagina = resp.json().get("gazettes", [])
        for g in pagina:
            trechos = RX_TAGS.sub("", "\n".join(g.get("excerpts") or []))
            municipio = g.get("territory_name", "")
            uf = g.get("state_code", "")
            achados.append(
                Achado(
                    fonte="qd",
                    titulo=f"Diário Oficial de {municipio}/{uf} — {g.get('date', '')}",
                    url=g.get("url") or g.get("txt_url", ""),
                    cargo_texto=trechos,
                    municipio=municipio,
                    uf=uf,
                    data_publicacao=g.get("date", ""),
                    detalhes={
                        "txt_url": g.get("txt_url", ""),
                        "edition": g.get("edition", ""),
                        "scraped_at": g.get("scraped_at", ""),
                    },
                )
            )
        if len(pagina) < TAM_PAGINA:
            break
        if len(achados) >= LIMITE_ITENS:
            print(f"[qd] aviso: limite de {LIMITE_ITENS} itens atingido, paginação interrompida")
            break
        params["offset"] += TAM_PAGINA
        time.sleep(1)

    cursor["scraped_since"] = dt.datetime.now().isoformat(timespec="seconds")
    return achados
