"""Classificador por IA — API da Anthropic (Claude Haiku 4.5).

Histórico: o classificador original usava o GitHub Models (gpt-4.1 com o
GITHUB_TOKEN do Actions, custo zero). O GitHub aposentou a plataforma em
30.7.2026 e o endpoint passou a responder 410 — foi isso que derrubou a
triagem entre 30.7 e 4.8.2026. Este módulo é o substituto: usa a API da
Anthropic quando o segredo ANTHROPIC_API_KEY existir no ambiente.

Sem a chave, `disponivel()` devolve False e a triagem opera só com as
regras determinísticas (radar/regras.py), segurando os casos incertos na
fila de pendentes — NUNCA fail-open.

Modelo padrão: claude-haiku-4-5 (classificação simples; ~US$ 0,01-0,05/dia
neste volume). Sobreponha com IA_MODELO. A saída é JSON garantido por
schema (structured outputs), sem parsing frágil.
"""

import json
import os

CLASSES = {"abertura", "andamento", "suspensao", "irrelevante"}
AREAS = {"tributario", "controle", "outra"}

MODELO_PADRAO = "claude-haiku-4-5"

_ESQUEMA = {
    "type": "object",
    "properties": {
        "classe": {"type": "string", "enum": sorted(CLASSES)},
        "area": {"type": "string", "enum": sorted(AREAS)},
        "cargo": {"type": "string"},
        "inscricoes": {"type": "string"},
        "cadastro_reserva": {"type": "boolean"},
        "resumo": {"type": "string"},
    },
    "required": ["classe", "area", "cargo", "inscricoes", "cadastro_reserva", "resumo"],
    "additionalProperties": False,
}

SISTEMA = """Você classifica achados de um radar de concursos públicos brasileiros.
O público-alvo presta concursos de fiscalização TRIBUTÁRIA (auditor/fiscal de tributos em
qualquer esfera: Receita Federal, SEFAZ estaduais, prefeituras/ISS) e de CONTROLE
(TCU, TCEs, CGU, auditor/controlador de controle externo/interno).

Dado o texto de um achado (título, órgão e trecho de diário oficial ou notícia), responda:

- classe:
  - "abertura": edital de abertura publicado, inscrições abertas ou anunciadas, ou
    retificação/prorrogação que ABRA ou ESTENDA prazo de inscrição. É a única classe que
    gera aviso ao candidato.
  - "suspensao": concurso da área-alvo SUSPENSO, cancelado ou adiado.
  - "andamento": concurso real, mas em fase que não permite mais inscrição: homologação de
    inscrições, gabarito, resultado de prova/títulos, convocação, nomeação, posse,
    retificação sem novo prazo.
  - "irrelevante": não é concurso (ato de fiscalização, auto de infração, portaria de
    pessoal, curso de formação, remoção interna, servidor assinando documento) OU o cargo
    não é da área-alvo (fiscal sanitário/de obras/posturas/ambiental/trânsito, agente
    fiscal de conselho profissional como CREA/CREFITO/CRM).
- area: "tributario", "controle" ou "outra" — a área do cargo em questão.
- cargo: o nome do cargo da área-alvo COPIADO LITERALMENTE do texto (ex.: "Auditor Fiscal
  de Tributos Municipais", "Fiscal de Tributos", "Controlador Interno"). NUNCA deduza nem
  complete: se nenhum cargo da área-alvo estiver escrito no texto, deixe "" e use
  area="outra".
- inscricoes: o período ou prazo de inscrição COPIADO do texto (ex.: "16/08 a 16/09/2026"),
  ou "" se o texto não trouxer.
- cadastro_reserva: true somente se o texto indicar que as vagas do cargo-alvo são apenas
  para cadastro de reserva (sem vaga imediata).
- resumo: uma frase curta (máx. 120 caracteres) dizendo o que o texto é.

REGRA DE EVIDÊNCIA: "abertura" exige que o texto mencione inscrições (abertas, anunciadas
ou com período) ou diga expressamente que é edital de abertura / que o concurso foi aberto.
Um texto que apenas cita o cargo, sem nada sobre abertura ou inscrições, NÃO é "abertura" —
classifique como "andamento" ou "irrelevante" conforme o caso.

Na dúvida entre "abertura" e "andamento" (quando há alguma evidência de abertura), escolha
"abertura" — perder um edital é pior do que um aviso a mais. Na dúvida entre "andamento" e
"irrelevante", escolha "andamento"."""

_cliente = None


def disponivel():
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _obter_cliente():
    global _cliente
    if _cliente is None:
        import anthropic

        # o SDK lê ANTHROPIC_API_KEY do ambiente e refaz 429/5xx sozinho
        _cliente = anthropic.Anthropic(timeout=60.0)
    return _cliente


def classificar(texto):
    """Uma chamada de classificação; devolve o veredito ou levanta exceção.

    Quem chama (radar/triagem.py) decide o destino em caso de erro —
    fail-closed: o item vai para a fila de pendentes, nunca para o painel.
    """
    resp = _obter_cliente().messages.create(
        model=os.environ.get("IA_MODELO", MODELO_PADRAO),
        max_tokens=500,
        system=SISTEMA,
        output_config={"format": {"type": "json_schema", "schema": _ESQUEMA}},
        messages=[{"role": "user", "content": texto}],
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("classificação recusada pelo modelo")
    if resp.stop_reason == "max_tokens":
        raise RuntimeError("resposta truncada (max_tokens)")
    corpo = next(b.text for b in resp.content if b.type == "text")
    veredito = json.loads(corpo)
    if veredito.get("classe") not in CLASSES or veredito.get("area") not in AREAS:
        raise ValueError(f"veredito fora do esquema: {veredito!r}")
    return {
        "classe": veredito["classe"],
        "area": veredito["area"],
        "cargo": str(veredito.get("cargo", ""))[:120],
        "inscricoes": str(veredito.get("inscricoes", ""))[:80],
        "cadastro_reserva": bool(veredito.get("cadastro_reserva")),
        "resumo": str(veredito.get("resumo", ""))[:160],
        "origem": f"ia:{os.environ.get('IA_MODELO', MODELO_PADRAO)}",
    }
