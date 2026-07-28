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

# Diário oficial é texto bruto que mistura atos: gabarito de outro concurso
# ao lado de ato de IPTU assinado por "agente fazendário" (Votorantim
# 24.7.2026), portaria de progressão citando o cargo (Curitiba 8.7.2026),
# índice com "edital de abertura" perto de tudo. Nenhuma heurística textual
# dá selo forte com segurança aqui, então TODO achado desta fonte sai como
# contexto_fraco (o main rebaixa para "conferir"). A busca de abertura
# próxima ao cargo serve só para escolher o melhor trecho e marcar
# "possível abertura" como sinal de triagem, nunca como autoridade.
RX_ABERTURA = re.compile(
    r"edital de abertura"
    r"|abertura d[ea]s? inscricoes"
    r"|inscricoes abertas"
    r"|periodo de inscricoes"
    r"|abertura d[eo] concurso"
    r"|realizacao de concurso publico"
    r"|torna publica? a abertura"
    r"|inscricoes estarao abertas"
    r"|as inscricoes (?:serao|poderao ser|deverao ser) (?:realizadas|efetuadas|feitas)"
)
# Cargo imediatamente seguido de secretaria/diretoria/matrícula é assinatura
# de servidor (ex.: "marcio ... agente fazendario secretaria de financas").
RX_POS_ASSINATURA = re.compile(r"^\W{0,10}(secretari|diretor|departamento|matricul|chefe|prefeit)")
JANELA_PROXIMIDADE = 800


def _abertura_proxima(texto_norm, rx_cargos):
    """Procura cargo não-assinatura a até JANELA de um marcador de abertura.

    Retorna (True, trecho) na primeira coocorrência válida, senão (False, "").
    """
    ab_pos = [m.start() for m in RX_ABERTURA.finditer(texto_norm)]
    if not ab_pos:
        return False, ""
    for rx in rx_cargos:
        for m in rx.finditer(texto_norm):
            if RX_POS_ASSINATURA.match(texto_norm[m.end() : m.end() + 90]):
                continue
            if any(abs(p - m.start()) <= JANELA_PROXIMIDADE for p in ab_pos):
                ini = max(0, m.start() - 250)
                return True, " ".join(texto_norm[ini : m.end() + 250].split())
    return False, ""


def _forte_no_texto_integral(txt_url, rx_cargos):
    resp = requests.get(txt_url, headers=HEADERS, timeout=60)
    resp.raise_for_status()
    return _abertura_proxima(normalizar(resp.text), rx_cargos)

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
            _, trecho_forte = _abertura_proxima(normalizar("\n".join(excerpts)), rx_cargos)
            if not trecho_forte and g.get("txt_url"):
                try:
                    _, trecho_forte = _forte_no_texto_integral(g["txt_url"], rx_cargos)
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
                "contexto_fraco": True,
            }
            if trecho_forte:
                detalhes["possivel_abertura"] = True
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
