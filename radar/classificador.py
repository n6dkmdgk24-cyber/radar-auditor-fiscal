"""Revisão final dos achados com IA gratuita (GitHub Models).

Usa a API do GitHub Models (formato OpenAI chat/completions) autenticada com
o GITHUB_TOKEN que o próprio workflow do Actions recebe — nenhuma chave
externa, custo zero. Modelo padrão: gpt-4.1 (mais preciso), com fallback
automático para gpt-4o-mini se a cota diária do primeiro esgotar.
Configurável por ambiente: IA_TOKEN (padrão: GITHUB_TOKEN), IA_MODELO, IA_URL.

TODOS os candidatos passam pela revisão (não só os "conferir"): a IA é o
árbitro final. Só o veredito "abertura" na área-alvo COM cargo citado no
texto gera aviso; "suspensao" na área-alvo entra com marcador ⚠️; andamento,
irrelevante e abertura de outra área saem dos avisos com registro auditável
em data/descartados.json. Fail-open: erro de API mantém o item como estava.
"""

import json
import os
import time

import requests

URL_PADRAO = "https://models.github.ai/inference/chat/completions"
MODELO_PADRAO = "openai/gpt-4.1"
MODELO_RESERVA = "openai/gpt-4o-mini"
CLASSES = {"abertura", "andamento", "suspensao", "irrelevante"}
AREAS = {"tributario", "controle", "outra"}

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
"irrelevante", escolha "andamento".

Responda SOMENTE com um objeto JSON neste formato, sem nenhum outro texto:
{"classe": "abertura|suspensao|andamento|irrelevante", "area": "tributario|controle|outra",
 "cargo": "...", "inscricoes": "...", "cadastro_reserva": false, "resumo": "..."}"""


def disponivel():
    return bool(os.environ.get("IA_TOKEN") or os.environ.get("GITHUB_TOKEN"))


def _texto_item(achado, termos):
    partes = [
        f"Título: {achado.titulo}",
        f"Órgão/local: {achado.orgao or achado.municipio or 'n/d'} / {achado.uf or 'n/d'}",
        f"Termos casados pelo filtro: {', '.join(termos)}",
        f"Trecho: {achado.detalhes.get('trecho', '')}",
    ]
    contexto = (achado.cargo_texto or "").strip()[:2000]
    if contexto:
        partes.append(f"Contexto adicional: {contexto}")
    return "\n".join(partes)


def _pedir(modelo, token, texto):
    """Uma chamada ao modelo, com retry de 429; devolve o veredito ou None se a cota esgotar."""
    for _ in range(2):
        resp = requests.post(
            os.environ.get("IA_URL", URL_PADRAO),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={
                "model": modelo,
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
        if resp.status_code == 429:
            time.sleep(min(int(resp.headers.get("retry-after", "20")), 60))
            continue
        resp.raise_for_status()
        return json.loads(resp.json()["choices"][0]["message"]["content"])
    return None  # cota persistentemente esgotada neste modelo


def _chamar(texto):
    token = os.environ.get("IA_TOKEN") or os.environ.get("GITHUB_TOKEN")
    modelos = [os.environ.get("IA_MODELO", MODELO_PADRAO)]
    if MODELO_RESERVA not in modelos:
        modelos.append(MODELO_RESERVA)
    veredito = None
    for modelo in modelos:
        veredito = _pedir(modelo, token, texto)
        if veredito is not None:
            break
        print(f"[ia] aviso: cota de {modelo} esgotada, tentando modelo reserva")
    if veredito is None:
        raise RuntimeError("cota de todos os modelos esgotada (HTTP 429 persistente)")
    if veredito.get("classe") not in CLASSES or veredito.get("area") not in AREAS:
        raise ValueError(f"veredito fora do esquema: {veredito!r}")
    return {
        "classe": veredito["classe"],
        "area": veredito["area"],
        "cargo": str(veredito.get("cargo", ""))[:120],
        "inscricoes": str(veredito.get("inscricoes", ""))[:80],
        "cadastro_reserva": bool(veredito.get("cadastro_reserva")),
        "resumo": str(veredito.get("resumo", ""))[:160],
    }


def revisar(candidatos, cfg):
    """Recebe [(Achado, categoria, termos)] — TODOS passam pela IA — e devolve
    (revisados, descartados). descartados é [(Achado, veredito)] para auditoria."""
    revisados, descartados = [], []
    for achado, categoria, termos in candidatos:
        try:
            veredito = _chamar(_texto_item(achado, termos))
            time.sleep(3)
        except Exception as e:  # fail-open: sem veredito, o item segue como estava
            print(f"[ia] aviso: classificação falhou ({e!r}), item mantido como {categoria}")
            revisados.append((achado, categoria, termos))
            continue

        achado.detalhes["ia"] = veredito
        alvo = veredito["area"] in ("tributario", "controle")
        if veredito["classe"] == "abertura" and alvo and veredito["cargo"]:
            revisados.append((achado, veredito["area"], termos))
        elif veredito["classe"] == "suspensao" and alvo:
            revisados.append((achado, veredito["area"], termos))
        else:
            # andamento, irrelevante, outra área ou abertura sem cargo citado:
            # não gera aviso, fica registrado em data/descartados.json
            descartados.append((achado, veredito))
    return revisados, descartados
