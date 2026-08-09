"""Painel: separação vitrine × arquivo, agrupamento geográfico, ordenação e
honestidade dos cartões (nada de contagem inflada, countdown em concurso
suspenso, regex da regra ou HTML mal escapado)."""

import datetime as dt
import re

import pytest
import yaml

from radar.saidas import painel

HOJE = dt.date(2026, 8, 9)
CFG = yaml.safe_load("painel_dias: 60\n")


class EstadoFake:
    def __init__(self, concursos, pendentes=()):
        self.concursos = concursos
        self._pendentes = list(pendentes)

    def pendentes_carregados(self):
        return self._pendentes


def _cartao(municipio, uf, categoria="tributario", inicio="", fim="",
            descoberto="2026-08-01", classe="abertura", **extra):
    det = {"ia": {"classe": classe, "cargo": "fiscal de tributos", "resumo": "Resumo do artigo.",
                  **extra.pop("ia", {})}}
    if inicio:
        det["inscricoes_inicio"] = inicio
    if fim:
        det["inscricoes_fim"] = fim
    det.update(extra)
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


def test_contagem_separa_em_curso_de_futuros(tmp_path):
    html = _gerar([
        _cartao("A", "PR", inicio="2026-08-01", fim="2026-08-30"),   # em curso
        _cartao("B", "PR", inicio="2026-08-20", fim="2026-09-30"),   # ainda vai abrir
        _cartao("C", "PR", inicio="2026-07-01", fim="2026-08-05"),   # encerrado
    ], tmp_path=tmp_path)
    assert "1 com inscrições em curso" in html
    assert "1 com inscrições a abrir" in html
    assert "1 concurso(s) com inscrições encerradas" in html


def test_grupos_por_proximidade_do_parana(tmp_path):
    html = _gerar([
        _cartao("Maringá", "PR", fim="2026-09-01"),
        _cartao("Blumenau", "SC", fim="2026-09-01"),
        _cartao("Manaus", "AM", fim="2026-09-01"),
    ], tmp_path=tmp_path)
    ordem = [m.group(1) for m in re.finditer(r"<h2>(.*?)</h2>", html)]
    assert ordem[0].startswith("📍 Paraná")
    assert "Vizinhos" in ordem[1]
    assert "Demais" in ordem[2]
    assert html.index("Maringá") < html.index("Blumenau") < html.index("Manaus")


def test_dentro_do_grupo_quem_encerra_antes_vem_primeiro(tmp_path):
    html = _gerar([
        _cartao("Depois", "PR", fim="2026-10-30"),
        _cartao("Antes", "PR", fim="2026-08-20"),
        _cartao("SemPrazo", "PR", descoberto="2026-08-08"),
    ], tmp_path=tmp_path)
    assert html.index("Antes") < html.index("Depois") < html.index("SemPrazo")


def test_suspenso_nao_tem_contagem_regressiva(tmp_path):
    html = _gerar([_cartao("Suspenso", "MT", fim="2026-09-15", classe="suspensao")],
                  tmp_path=tmp_path)
    assert "falta(m)" not in html
    assert "inscrições no edital original" in html
    assert "⚠️ Suspenso" in html


def test_cartao_mostra_vagas_validade_e_site(tmp_path):
    html = _gerar([
        _cartao("Coronel Vivida", "PR", inicio="2026-08-11", fim="2026-10-08",
                banca="Fundação FAFIPA", site_inscricao="https://www.fundacaofafipa.org.br",
                ia={"vagas": "1 vaga + CR", "validade": "2 anos, prorrogável"}),
    ], tmp_path=tmp_path)
    assert "1 vaga + CR" in html
    assert "validade 2 anos, prorrogável" in html
    assert "banca: Fundação FAFIPA" in html
    assert 'href="https://www.fundacaofafipa.org.br">↗ site de inscrição' in html


def test_cargo_generico_ganha_aviso_de_conferir(tmp_path):
    html = _gerar([_cartao("Marialva", "PR", categoria="conferir", fim="2026-09-03",
                           ia={"cargo": "agente fiscal"})], tmp_path=tmp_path)
    assert "Conferir área" in html
    assert "conferir no edital" in html
    assert "Marialva/PR — Agente Fiscal" in html


def test_html_escapado_e_sem_regex(tmp_path):
    cartao = _cartao("Aspas", "PR", fim="2026-09-01")
    cartao["titulo"] = 'Prefeitura de "X" & Cia <script>'
    cartao["detalhes"]["ia"]["cargo"] = ""
    cartao["detalhes"]["ia"]["resumo"] = "Resumo com <b>tag</b> & 'aspas'."
    cartao["detalhes"]["ia"]["criterio"] = r"manchete de abertura (\babre (o )?concursos?\b)"
    html = _gerar([cartao], tmp_path=tmp_path)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "&lt;b&gt;tag&lt;/b&gt;" in html
    assert "\\b" not in html, "o critério técnico da regra não pode ir para o painel"


def test_fila_de_pendentes_fica_no_topo_e_recolhida(tmp_path):
    from radar.modelos import Achado

    pendente = (
        Achado(fonte="domsc", titulo="Prefeitura municipal de Belmonte — 0511/2026",
               url="https://d/1", detalhes={"trecho": "trecho do diário"}),
        "conferir", ["fiscal de tributos"], {"enfileirado_em": "2026-08-07", "tentativas": 3},
    )
    html = _gerar([_cartao("Maringá", "PR", fim="2026-09-01")], [pendente], tmp_path=tmp_path)
    assert html.index("aguardando confirmação") < html.index("📍 Paraná")
    assert "<details>" in html and "1 item(ns) aguardando confirmação" in html


def test_sem_vitrine_avisa_em_vez_de_pagina_vazia(tmp_path):
    html = _gerar([_cartao("Antigo", "PR", fim="2026-07-01")], tmp_path=tmp_path)
    assert "Nenhuma descoberta com inscrições em aberto" in html


@pytest.mark.parametrize("chave", ["prefers-color-scheme: dark", "data-theme", "@media"])
def test_pagina_tem_estilo_para_tema_escuro(tmp_path, chave):
    html = _gerar([_cartao("Maringá", "PR", fim="2026-09-01")], tmp_path=tmp_path)
    if chave == "data-theme":
        pytest.skip("o painel usa color-scheme + prefers-color-scheme, sem toggle próprio")
    assert chave in html
