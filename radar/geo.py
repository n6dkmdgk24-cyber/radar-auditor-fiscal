"""Geolocalização dos achados: distância REAL até Maringá/PR, para o painel
ordenar por proximidade em vez de só agrupar por UF.

Fonte dos municípios: data/municipios.csv, um recorte enxuto (nome
normalizado, UF, latitude, longitude) do CSV público kelvins/municipios-
brasileiros (github.com/kelvins/municipios-brasileiros), baixado em
13.8.2026. Distância por haversine (linha reta, não rodoviária — suficiente
para ordenar o painel por "perto/longe", não para calcular deslocamento).

FAIL-CLOSED: município que não bate no CSV, ou UF vazia/ambígua, devolve
None/"desconhecido" — nunca um palpite. Homônimo é o caso mais concreto:
"Sarandi" existe no Paraná (6 km de Maringá) e no Rio Grande do Sul (mais de
500 km); sem a UF para desambiguar, não há como saber qual é qual, e chutar
o mais próximo inflaria a proximidade de um concurso que pode estar do outro
lado do país.
"""

import csv
import math
import re
from pathlib import Path

from .filtro import normalizar

BASE = Path(__file__).resolve().parent.parent
CSV_MUNICIPIOS = BASE / "data" / "municipios.csv"

MARINGA = (-23.4205, -51.9333)

_UFS_VIZINHAS = {"SP", "SC", "MS"}
_UFS_CONHECIDAS = {
    "AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA", "MG", "MS",
    "MT", "PA", "PB", "PE", "PI", "PR", "RJ", "RN", "RO", "RR", "RS", "SC",
    "SE", "SP", "TO",
}
_RAIO_KM = 40.0
_RAIO_TERRA_KM = 6371.0

# "Município de X", "Prefeitura (Municipal) de X", "Câmara (Municipal) de X"
# na frente do nome — sobra do texto de origem (orgao/manchete), não do
# nome do município propriamente dito.
_RX_PREFIXO = re.compile(
    r"^(?:municipio|prefeitura(?: municipal)?|camara(?: municipal)?)\s+de\s+"
)

_cache = None  # {(nome_normalizado, uf): (lat, lon)} — carregado uma vez


def _carregar():
    """Carrega o CSV para memória na primeira chamada (cache do módulo)."""
    global _cache
    if _cache is not None:
        return _cache
    tabela = {}
    with open(CSV_MUNICIPIOS, encoding="utf-8") as f:
        for linha in csv.DictReader(f):
            chave = (linha["nome_normalizado"], linha["uf"])
            tabela[chave] = (float(linha["latitude"]), float(linha["longitude"]))
    _cache = tabela
    return _cache


def _limpar_nome(municipio: str, sigla_uf: str) -> str:
    """Normaliza e tira prefixo de ente/sufixo de UF que grudou no nome.

    O sufixo só é removido quando bate com a PRÓPRIA sigla já informada em
    `uf` (ex.: "Cianorte - PR" com uf="PR"). Um sufixo genérico de 2 letras
    quase virou bug real: "Xangri-lá" (RS) normaliza para "xangri-la", e
    "-la" tem o mesmo formato de um sufixo de UF — cortar qualquer sufixo de
    2 letras trocava o nome por "xangri", que não existe no CSV. Exigir que
    o sufixo seja EXATAMENTE a UF informada elimina esse falso positivo (não
    há município no CSV terminado em "-" + sigla de UF real que não seja a
    própria).
    """
    n = normalizar(municipio).strip()
    n = _RX_PREFIXO.sub("", n)
    if sigla_uf:
        n = re.sub(rf"\s*[-/(]\s*{re.escape(sigla_uf)}\)?\s*$", "", n)
    return n.strip(" -")


def _coordenadas(municipio: str, uf: str):
    """(lat, lon) do município ou None — fail-closed, nunca chuta.

    UF vazia nunca é adivinhada: sem ela um nome homônimo ("Sarandi",
    "Floresta") pode apontar para o lado errado do país.
    """
    if not municipio or not uf:
        return None
    sigla = uf.strip().lower()
    if not sigla:
        return None
    nome = _limpar_nome(municipio, sigla)
    if not nome:
        return None
    return _carregar().get((nome, sigla.upper()))


def distancia_km(municipio: str, uf: str):
    """Distância haversine até Maringá/PR, em km, ou None se não encontrado."""
    coords = _coordenadas(municipio, uf)
    if coords is None:
        return None
    lat1, lon1 = MARINGA
    lat2, lon2 = coords
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return _RAIO_TERRA_KM * c


def faixa(municipio: str, uf: str) -> str:
    """Grupo de proximidade para ordenar o painel, do mais perto ao mais longe:

    "raio"       -> Paraná a até 40 km de Maringá (pedido: 30 km, com
                     variação de até 40 km — ver README/config do painel);
    "pr"         -> resto do Paraná;
    "vizinho"    -> SP, SC, MS (nessa ordem de prioridade cabe ao painel,
                     que ordena por distancia_km dentro do grupo);
    "distante"   -> demais estados;
    "desconhecido" -> município não encontrado no CSV, ou UF vazia.
    """
    uf_norm = (uf or "").strip().upper()
    dist = distancia_km(municipio, uf_norm)
    if dist is None:
        # Sem coordenada não dá para afirmar distância, mas a UF sozinha já
        # basta para o AGRUPAMENTO — e é o caso comum das entidades sem
        # município no nome (TCE-SP, SEFAZ-MT, CaraguaPrev). Um concurso do
        # Paraná sem coordenada fica no bloco do estado, nunca no do raio:
        # afirmar proximidade sem saber seria o tipo de chute que o radar não
        # dá (revisão de 13.8.2026).
        if uf_norm in _UFS_CONHECIDAS:
            if uf_norm == "PR":
                return "pr"
            return "vizinho" if uf_norm in _UFS_VIZINHAS else "distante"
        return "desconhecido"
    if uf_norm == "PR":
        return "raio" if dist <= _RAIO_KM else "pr"
    if uf_norm in _UFS_VIZINHAS:
        return "vizinho"
    return "distante"
