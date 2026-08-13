"""Painel v6: blocos por proximidade real de Maringá, hierarquia do cartão,
controles do navegador (ocultar, novidades, filtros) e honestidade do que é
exibido — nada de contagem inflada, countdown em concurso suspenso, regex da
regra vazando ou HTML mal escapado.
"""

import datetime as dt
import re

import yaml

from radar.saidas import painel

HOJE = dt.date(2026, 8, 13)
CFG = yaml.safe_load("painel_dias: 60\n")


class EstadoFake:
    def __init__(self, concursos, pendentes=()):
        self.concursos = concursos
        self._pendentes = list(pendentes)

    def pendentes_carregados(self):
        return self._pendentes


def _cartao(municipio, uf, categoria="tributario", inicio="", fim="",
            descoberto="2026-08-01", classe="abertura", ia=None, **det_extra):
    det = {
        "ia": {
            "classe": classe, "cargo": "fiscal de tributos",
            "resumo": "Resumo legível do artigo, com contexto suficiente.",
            **(ia or {}),
        }
    }
    if inicio:
        det["inscricoes_inicio"] = inicio
    if fim:
        det["inscricoes_fim"] = fim
    det.update(det_extra)
    return {
        "descoberto_em": descoberto, "categoria": categoria, "termos": ["fiscal de tributos"],
        "fonte": "pci", "titulo": f"Prefeitura de {municipio}", "url": f"https://x/{municipio}",
        "orgao": f"Prefeitura de {municipio}", "municipio": municipio, "uf": uf,
        "data_publicacao": descoberto, "detalhes": det,
    }


def _gerar(concursos, pendentes=(), tmp_path=None):
    destino = tmp_path / "index.html"
    painel.gerar(EstadoFake(concursos, pendentes), CFG, destino, hoje=HOJE)
    return destino.read_text(encoding="utf-8")


def _secoes(html):
    return [m.group(1) for m in re.finditer(r'data-secao="([^"]+)"', html)]


# --- geografia -------------------------------------------------------------

def test_blocos_por_proximidade_real_de_maringa(tmp_path):
    html = _gerar([
        _cartao("Sarandi", "PR", fim="2026-09-30"),        # ~6 km  -> raio
        _cartao("Londrina", "PR", fim="2026-09-30"),       # ~90 km -> pr
        _cartao("Blumenau", "SC", fim="2026-09-30"),       # vizinho
        _cartao("Manaus", "AM", fim="2026-09-30"),         # distante
    ], tmp_path=tmp_path)
    assert _secoes(html)[:4] == ["raio", "pr", "vizinho", "distante"]
    assert html.index("Sarandi") < html.index("Londrina") < html.index("Blumenau") < html.index("Manaus")


def test_dentro_do_bloco_o_mais_perto_vem_antes(tmp_path):
    html = _gerar([
        _cartao("Foz do Iguaçu", "PR", fim="2026-09-30"),
        _cartao("Cianorte", "PR", fim="2026-09-30"),       # bem mais perto
    ], tmp_path=tmp_path)
    assert html.index("Cianorte") < html.index("Foz do Iguaçu")


def test_inscricao_em_curso_vem_antes_de_quem_ainda_vai_abrir(tmp_path):
    """Mesmo estando mais longe: prazo correndo é mais urgente que prazo futuro."""
    html = _gerar([
        _cartao("Sarandi", "PR", inicio="2026-09-01", fim="2026-09-30"),   # ~6 km, abre depois
        _cartao("Marialva", "PR", inicio="2026-08-01", fim="2026-08-30"),  # ~28 km, em curso
    ], tmp_path=tmp_path)
    assert html.index("Marialva") < html.index("Sarandi")


def test_prova_remota_entra_no_bloco_do_raio(tmp_path):
    """Concurso com prova a distância serve morando em Maringá, então aparece
    junto com os daqui, ainda que o município seja em outro estado."""
    html = _gerar([_cartao("Manaus", "AM", fim="2026-09-30", ia={"prova_remota": True})],
                  tmp_path=tmp_path)
    assert _secoes(html)[0] == "raio"
    assert "prova remota" in html


def test_municipio_sem_localizacao_tem_bloco_proprio(tmp_path):
    html = _gerar([_cartao("Cidade Que Não Existe", "ZZ", fim="2026-09-30")], tmp_path=tmp_path)
    assert "desconhecido" in _secoes(html)


def test_distancia_aparece_no_cartao(tmp_path):
    html = _gerar([_cartao("Sarandi", "PR", fim="2026-09-30")], tmp_path=tmp_path)
    assert re.search(r"\d+ km de Maringá", html)


# --- estado do concurso ----------------------------------------------------

def test_contagem_separa_em_curso_de_futuros(tmp_path):
    html = _gerar([
        _cartao("Sarandi", "PR", inicio="2026-08-01", fim="2026-08-30"),   # em curso
        _cartao("Marialva", "PR", inicio="2026-08-20", fim="2026-09-30"),  # vai abrir
        _cartao("Cianorte", "PR", inicio="2026-07-01", fim="2026-08-05"),  # encerrado
    ], tmp_path=tmp_path)
    assert "<b>1</b> com inscrições em curso" in html
    assert "<b>1</b> vão abrir" in html
    assert "🗄 Encerrados e suspensos" in html


def test_prazo_curto_ganha_destaque_de_urgencia(tmp_path):
    html = _gerar([_cartao("Sarandi", "PR", inicio="2026-08-01", fim="2026-08-16")],
                  tmp_path=tmp_path)
    assert 'class="prazo urgente"' in html
    assert "faltam 3 dias" in html


def test_ultimo_dia_e_dito_por_extenso(tmp_path):
    html = _gerar([_cartao("Sarandi", "PR", inicio="2026-08-01", fim="2026-08-13")],
                  tmp_path=tmp_path)
    assert "último dia" in html


def test_suspenso_nao_tem_contagem_regressiva(tmp_path):
    html = _gerar([_cartao("Cuiabá", "MT", fim="2026-09-15", classe="suspensao",
                           ia={"inscricoes": "1.7.2026 a 15.9.2026"})], tmp_path=tmp_path)
    assert "faltam" not in html
    assert "edital original" in html
    assert "Suspenso" in html


# --- conteúdo do cartão ----------------------------------------------------

def test_cartao_mostra_remuneracao_vagas_validade_banca_e_links(tmp_path):
    html = _gerar([
        _cartao("Coronel Vivida", "PR", inicio="2026-08-11", fim="2026-10-08",
                banca="Fundação FAFIPA",
                site_inscricao="https://www.fundacaofafipa.org.br",
                edital_url="https://banca.org/edital-001-2026.pdf",
                ia={"vagas": "1 vaga + CR", "validade": "2 anos, prorrogável",
                    "remuneracao": "até R$ 21.525,56", "esfera": "municipal"}),
    ], tmp_path=tmp_path)
    assert "R$ 21.525,56" in html
    assert "1 vaga + CR" in html
    assert "validade 2 anos, prorrogável" in html
    assert "banca Fundação FAFIPA" in html
    assert 'href="https://banca.org/edital-001-2026.pdf">📄 edital' in html
    assert 'href="https://www.fundacaofafipa.org.br">↗ inscrição' in html
    assert "Tributário municipal" in html


def test_cargo_generico_ganha_aviso_de_conferir(tmp_path):
    html = _gerar([_cartao("Marialva", "PR", categoria="conferir", fim="2026-09-03",
                           ia={"cargo": "agente fiscal"})], tmp_path=tmp_path)
    assert "Conferir área" in html
    assert "conferir no edital" in html
    assert "Marialva/PR — Agente Fiscal" in html


def test_html_escapado_e_sem_regex(tmp_path):
    cartao = _cartao("Sarandi", "PR", fim="2026-09-01")
    cartao["titulo"] = 'Prefeitura de "X" & Cia <script>'
    cartao["detalhes"]["ia"]["cargo"] = ""
    cartao["detalhes"]["ia"]["resumo"] = "Resumo com <b>tag</b> & 'aspas'."
    cartao["detalhes"]["ia"]["criterio"] = r"manchete de abertura (\babre (o )?concursos?\b)"
    html = _gerar([cartao], tmp_path=tmp_path)
    # o único <script> da página é o do próprio painel; nada vindo do dado
    assert html.count("<script>") == 1
    assert "&lt;script&gt;" in html          # título hostil foi escapado
    assert "&lt;b&gt;tag&lt;/b&gt;" in html
    assert "\\b" not in html, "o critério técnico da regra não pode ir para o painel"


# --- estrutura e controles -------------------------------------------------

def test_fila_de_pendentes_fica_no_topo_e_recolhida(tmp_path):
    from radar.modelos import Achado

    pendente = (
        Achado(fonte="domsc", titulo="Prefeitura municipal de Belmonte — 0511/2026",
               url="https://d/1", detalhes={"trecho": "trecho do diário"}),
        "conferir", ["fiscal de tributos"], {"enfileirado_em": "2026-08-07", "tentativas": 3},
    )
    html = _gerar([_cartao("Sarandi", "PR", fim="2026-09-01")], [pendente], tmp_path=tmp_path)
    assert html.index("Aguardando confirmação") < html.index("Perto de Maringá")
    assert "<details" in html


def test_secoes_sao_recolhiveis_e_o_arquivo_comeca_fechado(tmp_path):
    html = _gerar([
        _cartao("Sarandi", "PR", fim="2026-09-30"),
        _cartao("Cianorte", "PR", fim="2026-07-01"),   # encerrado
    ], tmp_path=tmp_path)
    aberta = re.search(r'<details class="secao" data-secao="raio" open>', html)
    fechada = re.search(r'<details class="secao" data-secao="arquivo">', html)
    assert aberta and fechada


def test_cada_cartao_tem_id_estavel_e_botao_de_ocultar(tmp_path):
    html = _gerar([_cartao("Sarandi", "PR", fim="2026-09-30")], tmp_path=tmp_path)
    assert re.search(r'class="item" data-id="[0-9a-f]{12}"', html)
    assert 'class="ocultar"' in html
    # o painel é estático: o estado de "oculto" e de "novo" vive no navegador
    assert "localStorage" in html and "radar:ocultos" in html and "radar:vistos" in html


def test_controles_de_filtro_presentes(tmp_path):
    html = _gerar([_cartao("Sarandi", "PR", fim="2026-09-30")], tmp_path=tmp_path)
    for classe in ("so-abertas", "so-vaga", "so-perto"):
        assert f'data-classe="{classe}"' in html


def test_duas_colunas_em_tela_larga_e_uma_no_celular(tmp_path):
    html = _gerar([_cartao("Sarandi", "PR", fim="2026-09-30")], tmp_path=tmp_path)
    assert "grid-template-columns: repeat(auto-fill, minmax(330px, 1fr))" in html
    assert "@media (max-width: 620px)" in html
    assert "grid-template-columns: 1fr" in html


def test_tema_escuro_definido(tmp_path):
    html = _gerar([_cartao("Sarandi", "PR", fim="2026-09-30")], tmp_path=tmp_path)
    assert "prefers-color-scheme: dark" in html


def test_sem_vitrine_avisa_em_vez_de_pagina_vazia(tmp_path):
    html = _gerar([_cartao("Sarandi", "PR", fim="2026-07-01")], tmp_path=tmp_path)
    assert "Nenhuma descoberta com inscrições em aberto" in html
