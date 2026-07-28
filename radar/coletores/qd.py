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

from ..filtro import _frase_para_regex, normalizar
from ..modelos import Achado

# Contexto de concurso exigido NO MESMO TRECHO do cargo. A consulta à API já
# exige a coocorrência no diário inteiro, mas diário é documento longo: uma
# portaria de pessoal citando o cargo + um "concurso público" de outro ato
# gerariam falso positivo (caso real: DO de Curitiba de 8.7.2026). Sem a
# coocorrência no mesmo trecho, o achado é marcado contexto_fraco e o main
# o rebaixa para a categoria "conferir". Padrões deliberadamente estritos:
# "edital" e "inscrição" isolados aparecem em atos de fiscalização (edital de
# notificação, Inscrição Municipal) e não indicam concurso.
RX_CONTEXTO = re.compile(
    r"concurso publico|processo seletivo|selecao publica|edital de abertura|inscricoes"
)
JANELA_PROXIMIDADE = 1500  # distância máx. cargo<->contexto no texto integral


def _forte_no_texto_integral(txt_url, rx_cargos):
    """Baixa o texto integral do diário e procura cargo e contexto próximos.

    Os trechos (excerpts) da API têm 400 caracteres e, num edital real, o
    cabeçalho "CONCURSO PÚBLICO" e a tabela de cargos ficam mais distantes
    que isso (caso real: Bragança Paulista 7.7.2026). Retorna (True, trecho)
    na primeira coocorrência dentro da janela, senão (False, "").
    """
    resp = requests.get(txt_url, headers=HEADERS, timeout=60)
    resp.raise_for_status()
    texto = normalizar(resp.text)
    ctx_pos = [m.start() for m in RX_CONTEXTO.finditer(texto)]
    if not ctx_pos:
        return False, ""
    for rx in rx_cargos:
        for m in rx.finditer(texto):
            if any(abs(p - m.start()) <= JANELA_PROXIMIDADE for p in ctx_pos):
                ini = max(0, m.start() - 200)
                trecho = " ".join(texto[ini : m.end() + 200].split())
                return True, trecho
    return False, ""

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
    rx_cargos = [_frase_para_regex(c) for c in cfg["consultas_cargo"]]
    params = {
        "querystring": _querystring(cfg),
        "size": TAM_PAGINA,
        "offset": 0,
        "number_of_excerpts": 5,
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
            excerpts = [RX_TAGS.sub("", e) for e in (g.get("excerpts") or [])]
            trecho_forte = ""
            for e in excerpts:
                e_norm = normalizar(e)
                if RX_CONTEXTO.search(e_norm) and any(rx.search(e_norm) for rx in rx_cargos):
                    trecho_forte = e
                    break
            if not trecho_forte and g.get("txt_url"):
                try:
                    forte, trecho_forte = _forte_no_texto_integral(g["txt_url"], rx_cargos)
                    time.sleep(1)
                except requests.RequestException as e:
                    print(f"[qd] aviso: texto integral inacessível ({e!r}), mantendo contexto fraco")
            municipio = g.get("territory_name", "")
            uf = g.get("state_code", "")
            detalhes = {
                "txt_url": g.get("txt_url", ""),
                "edition": g.get("edition", ""),
                "scraped_at": g.get("scraped_at", ""),
                "trecho": " ".join((trecho_forte or (excerpts[0] if excerpts else "")).split())[:300],
            }
            if not trecho_forte:
                detalhes["contexto_fraco"] = True
            achados.append(
                Achado(
                    fonte="qd",
                    titulo=f"Diário Oficial de {municipio}/{uf} — {g.get('date', '')}",
                    url=g.get("url") or g.get("txt_url", ""),
                    cargo_texto="\n".join(excerpts),
                    municipio=municipio,
                    uf=uf,
                    data_publicacao=g.get("date", ""),
                    detalhes=detalhes,
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
