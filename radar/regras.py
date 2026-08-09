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

import datetime as dt
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
    r"\bautorizacao\b",
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
    # "isencao" NÃO entra: pedido de isenção da taxa é seção normal de edital
    # de abertura (caso real: Limeira/SP 31.7.2026); a lista de isentos, que é
    # fase posterior, casa por "isentos".
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
    r"\babre (o )?concursos?\b",
    r"\babre (o )?processos? seletivos?\b",
    r"\babre (a )?selecao\b",
    r"\babre (os )?edita(l|is)\b",
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
    r"\blanca (o )?concursos?\b",
    r"\bpublica (o )?edita(l|is)\b",
    r"\banuncia (o )?concurso\b",
    r"\boferta \d+ vagas\b",
    r"\boferece \d+ vagas\b",
    r"\boferece oportunidade\b",
    r"\bconta com \d+ vagas?\b",
    r"\blibera (o )?edital\b",
    r"\bsai (o )?edital\b",
    r"\bedital (publicado|liberado|divulgado)\b",
    r"\bdivulga (o )?edital\b",
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
    r"\benquadramento\b",
    r"\bplano de cargos\b",
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

# Fontes cujo título é manchete editorial (diz o que aconteceu); diários
# oficiais brutos (qd/sigpub/domsc/dou) ficam de fora.
FONTES_DE_NOTICIA = {"pci", "cnb", "selecao"}

# Prorrogação/reabertura DE INSCRIÇÕES (a palavra precisa estar amarrada a
# "inscri..." na mesma oração). "Concurso terá validade de 2 anos, podendo
# ser prorrogado" é boilerplate de todo artigo e NÃO é novo prazo — pegadinha
# real de Meridiano/Piraju, 7.8.2026.
_RX_PRORROGACAO = re.compile(
    r"(?:prorrog|reabr)\w*[^.;]{0,80}?inscric"
    r"|inscric\w*[^.;]{0,80}?(?:prorrogad|reabert)"
    r"|novo (?:prazo|periodo) de inscric"
)

_RX_RETIFICACAO = [re.compile(p) for p in (r"\bretifica\w*\b", r"\berrata\b")]
_RX_DOC = [re.compile(p) for p in _DOC_NAO_CONCURSO]
_RX_FASE = [re.compile(p) for p in _FASE_SEM_INSCRICAO]
_RX_ABERTURA = [re.compile(p) for p in _EVIDENCIA_ABERTURA]
_RX_SUSPENSAO = [re.compile(p) for p in _SUSPENSAO]
_RX_PESSOAL = [re.compile(p) for p in _ATO_DE_PESSOAL]


def _casados(rxs, texto):
    return [rx.pattern for rx in rxs if rx.search(texto)]


def manchete_de_prazo(titulo):
    """True quando o TÍTULO fala de abertura, prorrogação ou retificação —
    gate barato para o caminho de atualização de prazo de cartão já publicado
    (main.py), sem triagem completa. Retificação entra porque é assim que a
    prorrogação costuma ser noticiada ("retifica edital e prorroga
    inscrições"); quem decide é a data extraída. Fase posterior
    (resultado, convocação, gabarito) nunca mexe em prazo."""
    t = normalizar(titulo)
    fases = [f for f in _casados(_RX_FASE, t)
             if "retifica" not in f and "errata" not in f]
    return bool(_casados(_RX_ABERTURA, t) or _casados(_RX_RETIFICACAO, t)) and not fases


def triar(achado, categoria, termos, extracao=None, hoje=None):
    """Devolve (veredito, motivo) com veredito em
    {"abertura", "suspensao", "descarte", "incerto"}.

    categoria/termos vêm do Filtro: "tributario"/"controle" = cargo-alvo
    casado com termo forte; "conferir" = termo ambíguo.
    extracao: dados estruturados do radar/extrator.py (período de inscrição
    etc.) — decide retificação pela DATA real, não por palavra-chave.
    """
    titulo = normalizar(achado.titulo)
    trecho = normalizar(f"{achado.cargo_texto} {achado.detalhes.get('trecho', '')}")
    tudo = f"{titulo} {trecho}"
    forte = categoria in ("tributario", "controle")
    cargo = termos[0] if termos else "cargo-alvo"
    noticia = achado.fonte in FONTES_DE_NOTICIA

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

    # Retificação noticiada decide pela DATA: o artigo do PCI/CNB sempre
    # reafirma o período de inscrições do concurso retificado. Inscrições
    # ainda abertas => avisa (caso Piraju/SP, perdido em 7.8.2026 pela regra
    # antiga de palavra-chave); encerradas => descarta com o motivo certo
    # (caso Meridiano/SP, encerrado em 3.8.2026). Sem data legível, cai na
    # fila de pendentes — nunca descarte silencioso nem aval do verificador
    # (a anatomia do artigo de retificação é idêntica à de abertura).
    # Cargo genérico ("agente fiscal" de conselho, "fiscal municipal") não
    # ganha aviso por retificação: o ruído não compensa fora do alvo forte.
    retificacao = any("retifica" in f or "errata" in f for f in fase_titulo)
    if retificacao and achado.fonte in FONTES_DE_NOTICIA:
        fim = (extracao or {}).get("inscricoes_fim", "")
        if fim:
            if fim >= (hoje or dt.date.today()).isoformat():
                return "abertura", f"retificação com inscrições abertas até {fim}"
            return "descarte", f"retificação de concurso com inscrições encerradas em {fim}"
        if _RX_PRORROGACAO.search(tudo):
            return "abertura", f"retificação estendendo prazo de inscrição ({cargo})"
        return "incerto", "retificação sem período de inscrição legível no artigo"

    if fase_titulo and not abertura:
        return "descarte", f"fase sem inscrição (título: {fase_titulo[0]})"

    # Evidência de abertura no PRÓPRIO TÍTULO (manchete de notícia) decide
    # sozinha: o corpo de uma notícia de abertura cita etapas futuras
    # ("classificação", "resultado") sem que isso mude o que a manchete diz.
    # Em fonte de notícia, vale até para termo ambíguo ("controlador
    # interno"): a manchete diz que o concurso ABRIU e o cargo está no corpo
    # do artigo (a extração é restrita ao <article>, sem menus).
    abertura_titulo = _casados(_RX_ABERTURA, titulo)
    if abertura_titulo and not fase_titulo and (forte or achado.fonte in FONTES_DE_NOTICIA):
        return "abertura", f"manchete de abertura ({abertura_titulo[0]}) + cargo ({cargo})"

    if abertura and forte and not fase_titulo and not fase_trecho:
        return "abertura", f"evidência de inscrição ({abertura[0]}) + termo forte ({cargo})"

    if fase_trecho and not abertura:
        # Em FONTE DE NOTÍCIA o corpo do artigo de abertura sempre cita as
        # etapas futuras ("classificação", "resultado", "convocação"): isso
        # não é fase, é descrição do certame. Descartar por aí perdia
        # abertura real (caso Conceição do Mato Dentro/MG, 7.8.2026) — aqui
        # o item vai ao verificador, que lê o texto integral.
        if noticia:
            return "incerto", f"fase citada no corpo do artigo ({fase_trecho[0]}) — verificar"
        return "descarte", f"fase sem inscrição (trecho: {fase_trecho[0]})"

    pessoal = _casados(_RX_PESSOAL, tudo)
    if pessoal and not abertura:
        return "descarte", f"ato de pessoal sobre servidor ({pessoal[0]})"

    return "incerto", "sem evidência decisiva nas regras"
