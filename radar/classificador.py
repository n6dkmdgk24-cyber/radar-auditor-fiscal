"""Revisão final dos achados "conferir" com IA gratuita (opcional).

Usa a API do GitHub Models (formato OpenAI chat/completions) autenticada com
o GITHUB_TOKEN que o próprio workflow do Actions recebe — nenhuma chave
externa, custo zero. Modelo padrão: gpt-4o-mini (limite gratuito ~150
chamadas/dia, muito acima do volume do radar). Configurável por ambiente:
IA_TOKEN (padrão: GITHUB_TOKEN), IA_MODELO, IA_URL.

Para cada achado que o filtro determinístico deixou em "conferir", o modelo
decide: abertura na área-alvo (recupera o selo forte), andamento (continua
em conferir) ou irrelevante (sai dos avisos, com registro auditável em
data/descartados.json). Fail-open: qualquer erro mantém o item em conferir.
"""

import json
import os
import time

import requests

URL_PADRAO = "https://models.github.ai/inference/chat/completions"
MODELO_PADRAO = "openai/gpt-4o-mini"
CLASSES = {"abertura", "andamento", "irrelevante"}
AREAS = {"tributario", "controle", "outra"}

SISTEMA = """Você classifica achados de um radar de concursos públicos brasileiros.
O público-alvo presta concursos de fiscalização TRIBUTÁRIA (auditor/fiscal de tributos em
qualquer esfera: Receita Federal, SEFAZ estaduais, prefeituras/ISS) e de CONTROLE
(TCU, TCEs, CGU, auditor de controle externo/interno).

Dado o texto de um achado (título, órgão e trecho de diário oficial ou notícia), decida:

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
"irrelevante", escolha "andamento" — é melhor um aviso a mais do que um concurso perdido.

Responda SOMENTE com um objeto JSON neste formato, sem nenhum outro texto:
{"classe": "abertura|andamento|irrelevante", "area": "tributario|controle|outra", "resumo": "..."}"""


def disponivel():
    return bool(os.environ.get("IA_TOKEN") or os.environ.get("GITHUB_TOKEN"))


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


def _chamar(texto):
    token = os.environ.get("IA_TOKEN") or os.environ.get("GITHUB_TOKEN")
    resp = requests.post(
        os.environ.get("IA_URL", URL_PADRAO),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={
            "model": os.environ.get("IA_MODELO", MODELO_PADRAO),
            "messages": [
                {"role": "system", "content": SISTEMA},
                {"role": "user", "content": texto},
            ],
            "response_format": {"type": "json_object"},
            "max_tokens": 300,
            "temperature": 0,
        },
        timeout=60,
    )
    resp.raise_for_status()
    veredito = json.loads(resp.json()["choices"][0]["message"]["content"])
    if veredito.get("classe") not in CLASSES or veredito.get("area") not in AREAS:
        raise ValueError(f"veredito fora do esquema: {veredito!r}")
    return {
        "classe": veredito["classe"],
        "area": veredito["area"],
        "resumo": str(veredito.get("resumo", ""))[:160],
    }


def revisar(candidatos, cfg):
    """Recebe [(Achado, categoria, termos)] e devolve (revisados, descartados).

    Só examina itens "conferir"; os demais passam intocados. descartados é
    uma lista de (Achado, veredito) para registro auditável.
    """
    revisados, descartados = [], []
    for achado, categoria, termos in candidatos:
        if categoria != "conferir":
            revisados.append((achado, categoria, termos))
            continue
        try:
            veredito = _chamar(_texto_item(achado, termos))
            time.sleep(2)  # cortesia com o limite de requisições do plano gratuito
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
