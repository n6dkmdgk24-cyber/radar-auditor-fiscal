"""Pré-classificador determinístico dos achados.

Motivação (falha de 30.7–4.8.2026): o GitHub Models foi aposentado em
30.7.2026, a IA parou de responder e o pipeline "fail-open" publicou todo
achado bruto de diário oficial (portarias de pessoal, dívida ativa, férias,
licitações) como se fosse concurso. As regras abaixo resolvem os casos
óbvios das duas pontas SEM depender de IA:

- "descarte": documento que claramente não é abertura de concurso — pelo
  tipo (portaria, decreto, lei, licitação...) ou pela fase (convocação,
  gabarito, resultado, homologação...).
- "abertura": evidência textual forte de inscrições abertas/anunciadas
  junto de cargo-alvo casado com termo forte (tributário/controle).
- "suspensao": concurso suspenso/cancelado/adiado com termo forte.
- "incerto": todo o resto — decide a IA; sem IA, vai para a fila de
  pendentes (NUNCA para o painel).

Os padrões operam sobre texto normalizado (minúsculas, sem acento) do
título e do trecho. Sinais de título valem mais que sinais de trecho: o
título diz o que o documento É; o trecho é só a janela onde o cargo apareceu.
"""

import re

from .filtro import normalizar

# Tipos de documento que não são concurso (avaliados no TÍTULO).
# "edital de abertura" citado dentro de portaria/convocação é referência,
# não abertura — por isso o tipo do documento tem precedência.
_DOC_NAO_CONCURSO = [
    r"\bportaria\b",
    r"\bdecreto\b",
    r"\blei\b",
    r"\bata\b",
    r"\bacordao\b",
    r"\bresolucao\b",
    r"\binstrucao normativa\b",
    r"\blicitacao\b",
    r"\binexigibilidade\b",
    r"\bdispensa\b",
    r"\bpregao\b",
    r"\bchamada publica\b",
    r"\baudiencia publica\b",
    r"\bdivida ativa\b",
    r"\bnotificacao\b",
    r"\bintimacao\b",
    r"\bauto (de )?infracao\b",
    r"\bdiarias?\b",
    r"\bferias\b",
    r"\blicenca\b",
    r"\bltcat\b",
    r"\bcessao\b",
    r"\bprogressao\b",
    r"\baposentadoria\b",
    r"\bexoneracao\b",
    r"\bextrato\b",
    r"\btermo de\b",
    r"\bsessao ordinaria\b",
    r"\bsessao extraordinaria\b",
]

# Fases de concurso que não permitem inscrição (título ou trecho).
_FASE_SEM_INSCRICAO = [
    r"\bconvocacao\b",
    r"\bconvoca\b",
    r"\bnomeacao\b",
    r"\bnomeia\b",
    r"\bnomear\b",
    r"\beliminacao\b",
    r"\beliminad[oa]s?\b",
    r"\bposse\b",
    r"\bhomologacao\b",
    r"\bresultado\b",
    r"\bgabarito\b",
    r"\bclassificacao\b",
    r"\bheteroidentificacao\b",
    r"\bisencao\b",
    r"\bisentos\b",
    r"\bensalamento\b",
    r"\berrata\b",
    r"\bretificacao\b",
    r"\bretifica\b",
    r"\bdata,? local e horario\b",
    r"\blocal e horario\b",
]

# Evidência de abertura: inscrições abertas, anunciadas ou com prazo.
# A frase solta "edital de abertura" NÃO entra aqui de propósito: ela
# aparece como referência em editais de fase posterior (caso Guaíra/PR).
_EVIDENCIA_ABERTURA = [
    r"\babre (o )?concurso\b",
    r"\babre (o )?processo seletivo\b",
    r"\babre (a )?selecao\b",
    r"\babre \d+ vagas?\b",
    r"\babre vagas?\b",
    r"\babrem?,? inscricoes\b",
    r"\babre inscricoes\b",
    r"\babertura de inscricoes\b",
    r"\babertas as inscricoes\b",
    r"\binscricoes (estao |estarao )?abertas\b",
    r"\bestarao abertas\b",
    r"\breabre\b",
    r"\breabertura\b",
    r"\bprorroga(cao)? (de |das |o prazo de )?inscricoes?\b",
    r"\bperiodo de inscricao\b",
    r"\binscricoes ate\b",
    r"\binscricoes de \d",
    r"\binscricoes do dia\b",
    r"\brecebe inscricoes\b",
    r"\binscreva-se\b",
    r"\blanca (o )?edital\b",
    r"\banuncia (o )?concurso\b",
    r"\bnovo concurso\b",
    r"\bdivulga (o )?edital de (abertura|concurso)\b",
    r"\btorna publica a abertura\b",
]

# Ato de pessoal sobre servidor JÁ ocupante do cargo (padrão dominante dos
# falsos positivos do Querido Diário em 30.7–4.8.2026: férias, licenças,
# progressão, junta médica). Um edital de abertura nunca fala de ocupante,
# matrícula funcional ou período aquisitivo.
_ATO_DE_PESSOAL = [
    r"\bocupante d[eo] cargo\b",
    r"\bmatricula\b",
    r"\blicenca[- ]premio\b",
    r"\blicenca capacitacao\b",
    r"\bferias regulamentares\b",
    r"\bperiodo aquisitivo\b",
    r"\bjunta medica\b",
    r"\brealizou concurso\b",
    r"\bauto de infracao\b",
]

_SUSPENSAO = [
    r"\bsuspens[oa]\b",
    r"\bsuspensao\b",
    r"\bsuspende\b",
    r"\bcancelad[oa]\b",
    r"\bcancela(mento)?\b",
    r"\badiad[oa]\b",
    r"\badia(mento)?\b",
    r"\banulad[oa]\b",
    r"\banula(cao)?\b",
]

_RX_DOC = [re.compile(p) for p in _DOC_NAO_CONCURSO]
_RX_FASE = [re.compile(p) for p in _FASE_SEM_INSCRICAO]
_RX_ABERTURA = [re.compile(p) for p in _EVIDENCIA_ABERTURA]
_RX_SUSPENSAO = [re.compile(p) for p in _SUSPENSAO]
_RX_PESSOAL = [re.compile(p) for p in _ATO_DE_PESSOAL]


def _casados(rxs, texto):
    return [rx.pattern for rx in rxs if rx.search(texto)]


def triar(achado, categoria, termos):
    """Devolve (veredito, motivo) com veredito em
    {"abertura", "suspensao", "descarte", "incerto"}.

    categoria/termos vêm do Filtro: "tributario"/"controle" = cargo-alvo
    casado com termo forte; "conferir" = termo ambíguo (nunca vira
    "abertura" por regra — só a IA promove).
    """
    titulo = normalizar(achado.titulo)
    trecho = normalizar(f"{achado.cargo_texto} {achado.detalhes.get('trecho', '')}")
    tudo = f"{titulo} {trecho}"
    forte = categoria in ("tributario", "controle")

    abertura = _casados(_RX_ABERTURA, tudo)
    suspensao = _casados(_RX_SUSPENSAO, tudo)
    doc_titulo = _casados(_RX_DOC, titulo)
    fase_titulo = _casados(_RX_FASE, titulo)
    fase_trecho = _casados(_RX_FASE, trecho)

    if suspensao and "concurso" in tudo:
        motivo = f"suspensão/cancelamento detectado ({suspensao[0]})"
        return ("suspensao", motivo) if forte else ("incerto", motivo)

    # O tipo do documento tem precedência sobre menções soltas a abertura:
    # portaria/decreto/lei não abrem concurso, mesmo citando o cargo.
    if doc_titulo:
        return "descarte", f"documento não é concurso (título: {doc_titulo[0]})"

    if fase_titulo and not abertura:
        return "descarte", f"fase sem inscrição (título: {fase_titulo[0]})"

    if abertura and forte and not fase_titulo and not fase_trecho:
        return "abertura", f"evidência de inscrição ({abertura[0]}) + termo forte ({termos[0]})"

    if fase_trecho and not abertura:
        return "descarte", f"fase sem inscrição (trecho: {fase_trecho[0]})"

    pessoal = _casados(_RX_PESSOAL, tudo)
    if pessoal and not abertura:
        return "descarte", f"ato de pessoal sobre servidor ({pessoal[0]})"

    return "incerto", "sem evidência decisiva nas regras"
