"""Testes de radar/geo.py.

Os valores de distância hardcoded abaixo são os que a fórmula (haversine)
REALMENTE devolve para as coordenadas do CSV kelvins/municipios-brasileiros
(conferido em 13.8.2026 — ver comentário de módulo em radar/geo.py), travados
com tolerância de ±1 km. Não são estimativas: rodar geo.distancia_km() de
novo para essas cidades deve bater com o valor travado, a menos que o CSV
mude.
"""

import math

import pytest

from radar import geo

# cidades a até 40 km de Maringá (pedido do Danilo: bloco "raio" do painel),
# com a distância real devolvida pela fórmula em 13.8.2026
_CIDADES_NO_RAIO = {
    "Sarandi": 6.408,
    "Paiçandu": 12.139,
    "Marialva": 15.992,
    "Mandaguaçu": 18.421,
    "Iguaraçu": 27.391,
    "Ângulo": 25.185,
    "Doutor Camargo": 32.806,
    "Ivatuba": 36.633,
    "Floresta": 25.261,
}


def test_maringa_e_zero_km():
    assert geo.distancia_km("Maringá", "PR") == pytest.approx(0.0, abs=0.01)


@pytest.mark.parametrize("municipio,distancia_esperada", _CIDADES_NO_RAIO.items())
def test_cidades_da_regiao_metropolitana_dentro_de_40km(municipio, distancia_esperada):
    dist = geo.distancia_km(municipio, "PR")
    assert dist is not None
    assert dist == pytest.approx(distancia_esperada, abs=1.0)
    assert dist <= 40
    assert geo.faixa(municipio, "PR") == "raio"


@pytest.mark.parametrize("municipio,distancia_esperada", [
    ("Cianorte", 73.504),
    ("Londrina", 79.077),
])
def test_cianorte_e_londrina_fora_do_raio_mas_no_parana(municipio, distancia_esperada):
    dist = geo.distancia_km(municipio, "PR")
    assert dist == pytest.approx(distancia_esperada, abs=1.0)
    assert dist > 40
    assert geo.faixa(municipio, "PR") == "pr"


def test_blumenau_sc_e_vizinho():
    dist = geo.distancia_km("Blumenau", "SC")
    assert dist == pytest.approx(483.7, abs=1.0)
    assert geo.faixa("Blumenau", "SC") == "vizinho"


@pytest.mark.parametrize("municipio,uf,distancia_esperada", [
    ("Presidente Prudente", "SP", 154.801),
    ("São Paulo", "SP", 540.031),
    ("Campo Grande", "MS", 431.876),
    ("Dourados", "MS", 323.678),
])
def test_estados_vizinhos_sp_e_ms_reconhecidos(municipio, uf, distancia_esperada):
    dist = geo.distancia_km(municipio, uf)
    assert dist == pytest.approx(distancia_esperada, abs=1.0)
    assert geo.faixa(municipio, uf) == "vizinho"


def test_manaus_am_e_distante():
    dist = geo.distancia_km("Manaus", "AM")
    assert dist == pytest.approx(2419.3, abs=1.0)
    assert geo.faixa("Manaus", "AM") == "distante"


def test_homonimo_sarandi_desambiguado_pela_uf():
    """"Sarandi" existe no PR (perto de Maringá) e no RS (longe) — sem a UF
    correta o radar chutaria a distância errada. Caso real que motivou a
    exigência de UF na assinatura da função."""
    perto = geo.distancia_km("Sarandi", "PR")
    longe = geo.distancia_km("Sarandi", "RS")
    assert perto == pytest.approx(6.408, abs=1.0)
    assert longe == pytest.approx(512.5, abs=1.0)
    assert geo.faixa("Sarandi", "PR") == "raio"
    assert geo.faixa("Sarandi", "RS") == "distante"


def test_homonimo_floresta_desambiguado_pela_uf():
    """"Floresta" existe no PR (perto) e em PE (longe)."""
    perto = geo.distancia_km("Floresta", "PR")
    longe = geo.distancia_km("Floresta", "PE")
    assert perto == pytest.approx(25.261, abs=1.0)
    assert longe is not None and longe > 2000
    assert geo.faixa("Floresta", "PR") == "raio"
    assert geo.faixa("Floresta", "PE") == "distante"


def test_municipio_inexistente_devolve_none():
    """Sem coordenada, a DISTÂNCIA nunca é chutada; a faixa cai para o estado
    (entidades como TCE-SP e SEFAZ-MT não têm município no nome, mas têm UF).
    Concurso do Paraná sem coordenada fica no bloco do estado, jamais no do
    raio: proximidade só se afirma com coordenada."""
    assert geo.distancia_km("Cidade Que Não Existe", "PR") is None
    assert geo.faixa("Cidade Que Não Existe", "PR") == "pr"
    assert geo.faixa("Entidade Sem Município", "SP") == "vizinho"
    assert geo.faixa("Entidade Sem Município", "MT") == "distante"
    assert geo.faixa("Qualquer Coisa", "ZZ") == "desconhecido"


def test_uf_vazia_devolve_none_mesmo_com_municipio_valido():
    # fail-closed: mesmo um nome inequívoco não é chutado sem a UF
    assert geo.distancia_km("Maringá", "") is None
    assert geo.faixa("Maringá", "") == "desconhecido"


def test_uf_vazia_nao_adivinha_nem_em_homonimo():
    assert geo.distancia_km("Sarandi", "") is None
    assert geo.faixa("Sarandi", "") == "desconhecido"


def test_municipio_vazio_devolve_none():
    assert geo.distancia_km("", "PR") is None
    assert geo.distancia_km(None, "PR") is None


def test_lookup_tolera_prefixo_municipio_de():
    assert geo.distancia_km("Município de Maringá", "PR") == pytest.approx(0.0, abs=0.01)


def test_lookup_tolera_prefixo_prefeitura_de():
    assert geo.distancia_km("Prefeitura de Maringá", "PR") == pytest.approx(0.0, abs=0.01)
    assert geo.distancia_km("Prefeitura Municipal de Cianorte", "PR") == pytest.approx(
        73.504, abs=1.0
    )


def test_lookup_tolera_prefixo_camara_de():
    assert geo.distancia_km("Câmara de Maringá", "PR") == pytest.approx(0.0, abs=0.01)
    assert geo.distancia_km("Câmara Municipal de Londrina", "PR") == pytest.approx(
        79.077, abs=1.0
    )


@pytest.mark.parametrize("variante", [
    "Cianorte - PR",
    "Cianorte-PR",
    "Cianorte/PR",
    "Cianorte (PR)",
])
def test_lookup_tolera_sufixo_de_uf_colado_ao_nome(variante):
    assert geo.distancia_km(variante, "PR") == pytest.approx(73.504, abs=1.0)


def test_lookup_tolera_nome_com_hifen_legitimo():
    # "Ji-Paraná" tem hífen que faz parte do próprio nome (não é sufixo de UF)
    dist = geo.distancia_km("Ji-Paraná", "RO")
    assert dist is not None
    assert dist > 1000  # RO está longe de Maringá; só confere que achou


def test_lookup_nao_confunde_final_do_nome_com_sufixo_de_uf():
    """Xangri-lá/RS normaliza para "xangri-la": "-la" tem o formato de um
    sufixo de UF (2 letras depois de hífen), mas não é uma sigla de UF.
    Cortar esse sufixo às cegas destruiria o nome do município (bug real
    encontrado e corrigido nesta revisão — ver radar/geo.py:_limpar_nome)."""
    dist = geo.distancia_km("Xangri-lá", "RS")
    assert dist == pytest.approx(734.267, abs=1.0)


def test_uf_case_insensitive():
    assert geo.distancia_km("Maringá", "pr") == pytest.approx(0.0, abs=0.01)


def test_maringa_constante_bate_com_o_csv():
    assert geo.MARINGA == (-23.4205, -51.9333)


def test_distancia_e_simetrica_por_construcao():
    """Confere a própria fórmula: haversine entre dois pontos quaisquer não
    depende da ordem — serve de checagem independente da implementação."""
    lat1, lon1 = geo.MARINGA
    lat2, lon2 = -23.6599, -52.6054  # Cianorte
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    esperado = r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    assert geo.distancia_km("Cianorte", "PR") == pytest.approx(esperado, abs=0.001)


def test_csv_enxuto_tem_5571_municipios():
    """O pedido original previa 5570 (o total histórico, estável desde 2013).

    Em 13.8.2026 o Brasil tem 5571: Boa Esperança do Norte/MT (código IBGE
    5101837) foi instalado em 1.1.2026 — decisão do STF de 6.10.2025
    reconheceu a constitucionalidade da emancipação de 2000, encerrando uma
    disputa de mais de duas décadas com Nova Ubiratã/MT. Confirmado ao vivo
    em 13.8.2026 na API do IBGE (servicodados.ibge.gov.br/api/v1/localidades/
    municipios), que também lista 5571 municípios e inclui esse registro.
    Contar 5570 aqui exigiria excluir um município oficialmente reconhecido
    — o que violaria a regra fail-closed do projeto (nunca inventar ou
    aproximar para bater com um número esperado). Ver nota na entrega.
    """
    with open(geo.CSV_MUNICIPIOS, encoding="utf-8") as f:
        linhas = f.readlines()
    total = len(linhas) - 1  # desconta o cabeçalho
    assert total == 5571


def test_csv_tem_apenas_as_4_colunas_esperadas():
    with open(geo.CSV_MUNICIPIOS, encoding="utf-8") as f:
        cabecalho = f.readline().strip()
    assert cabecalho == "nome_normalizado,uf,latitude,longitude"


def test_carregamento_e_preguicoso_e_cacheado(monkeypatch):
    """Chamar distancia_km não deve reabrir o CSV a cada vez."""
    geo._cache = None
    aberturas = []
    original_open = open

    def _open_contando(*args, **kwargs):
        if args and str(args[0]) == str(geo.CSV_MUNICIPIOS):
            aberturas.append(1)
        return original_open(*args, **kwargs)

    monkeypatch.setattr("builtins.open", _open_contando)
    geo.distancia_km("Maringá", "PR")
    geo.distancia_km("Cianorte", "PR")
    geo.distancia_km("Sarandi", "RS")
    assert len(aberturas) == 1
