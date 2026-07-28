"""Revisão final dos achados "conferir" com Claude Haiku (opcional).

Só roda quando ANTHROPIC_API_KEY existe no ambiente. Para cada achado que o
filtro determinístico deixou em "conferir", o modelo lê o trecho e decide:
abertura de concurso na área-alvo (recupera o selo forte), concurso em
andamento (continua em "conferir") ou irrelevante (sai dos avisos, mas fica
registrado em data/descartados.json — nada some em silêncio). Qualquer erro
de API mantém o comportamento atual (fail-open para "conferir").
"""

import json

import anthropic

MODELO = "claude-haiku-4-5"

FORMATO = {
    "type": "json_schema",
    "schema": {
        "type": "object",
        "properties": {
            "classe": {"type": "string", "enum": ["abertura", "andamento", "irrelevante"]},
            "area": {"type": "string", "enum": ["tributario", "controle", "outra"]},
            "resumo": {"type": "string", "description": "Uma frase curta (máx. 120 caracteres) dizendo o que o texto é"},
        },
        "required": ["classe", "area", "resumo"],
        "additionalProperties": False,
    },
}

SISTEMA = """Você classifica achados de um radar de concursos públicos brasileiros.
O público-alvo presta concursos de fiscalização TRIBUTÁRIA (auditor/fiscal de tributos em
qualquer esfera: Receita Federal, SEFAZ estaduais, prefeituras/ISS) e de CONTROLE
(TCU, TCEs, CGU, auditor de controle externo/interno).

Dado o texto de um achado (título, órgão e trecho de diário oficial ou notícia), responda:

- classe:
  - "abertura": edital de abertura publicado ou inscrições abertas/anunciadas para concurso
    público ou processo seletivo com cargo da área-alvo.
  - "andamento": concurso real com cargo da área-alvo, mas em fase posterior (convocação,
    homologação, gabarito, nomeação, retificação sem novo prazo de inscrição).
  - "irrelevante": não é concurso (ato de fiscalização, auto de infração, portaria de pessoal,
    servidor assinando documento) OU o cargo não é da área-alvo (fiscal sanitário/de obras/
    posturas/ambiental/trânsito, agente fiscal de conselho profissional como CREA/CREFITO/CRM,
    cargo de outra área citado por acaso).
- area: "tributario", "controle" ou "outra" — a área do cargo em questão.
- resumo: uma frase curta (máx. 120 caracteres) dizendo o que o texto é.

Na dúvida entre "abertura" e "andamento", escolha "andamento". Na dúvida entre "andamento" e
"irrelevante", escolha "andamento" — é melhor um aviso a mais do que um concurso perdido."""


def _texto_item(achado, termos):
    partes = [
        f"Título: {achado.titulo}",
        f"Órgão/local: {achado.orgao or achado.municipio or 'n/d'} / {achado.uf or 'n/d'}",
        f"Termos casados pelo filtro: {', '.join(termos)}",
        f"Trecho: {achado.detalhes.get('trecho', '')}",
    ]
    contexto = (achado.cargo_texto or "").strip()[:800]
    if contexto:
        partes.append(f"Contexto adicional: {contexto}")
    return "\n".join(partes)


def revisar(candidatos, cfg):
    """Recebe [(Achado, categoria, termos)] e devolve (revisados, descartados).

    Só examina itens "conferir"; os demais passam intocados. descartados é
    uma lista de (Achado, veredito) para registro auditável.
    """
    cliente = anthropic.Anthropic()  # lê ANTHROPIC_API_KEY do ambiente
    revisados, descartados = [], []
    for achado, categoria, termos in candidatos:
        if categoria != "conferir":
            revisados.append((achado, categoria, termos))
            continue
        try:
            resposta = cliente.messages.create(
                model=MODELO,
                max_tokens=300,
                system=SISTEMA,
                output_config={"format": FORMATO},
                messages=[{"role": "user", "content": _texto_item(achado, termos)}],
            )
            texto = next(b.text for b in resposta.content if b.type == "text")
            veredito = json.loads(texto)
        except Exception as e:  # fail-open: sem veredito, o item segue como conferir
            print(f"[ia] aviso: classificação falhou ({e!r}), item mantido em conferir")
            revisados.append((achado, categoria, termos))
            continue

        achado.detalhes["ia"] = veredito
        if veredito["classe"] == "abertura" and veredito["area"] in ("tributario", "controle"):
            revisados.append((achado, veredito["area"], termos))
        elif veredito["classe"] == "irrelevante":
            descartados.append((achado, veredito))
        else:
            revisados.append((achado, "conferir", termos))
    return revisados, descartados
