"""Extração determinística de dados estruturados dos artigos de concurso.

Os artigos das fontes de notícia (pci/cnb) são estereotipados: período de
inscrições, vagas por cargo, banca, validade e site de inscrição aparecem
em frases-padrão ("As inscrições estarão abertas das 8h do dia 11 de agosto
de 2026 até às 23h59 do dia 8 de outubro de 2026, pelo site ...";
"Fiscal Tributário (1 vaga + CR)"). Este módulo lê o texto já coletado e
devolve esses campos SEM IA, por regex, na mesma filosofia fail-closed da
triagem: campo duvidoso fica VAZIO — nunca inventado, nunca aproximado.

Motivação (feedback do Danilo, 6.8.2026):
- o painel mostrava o regex da regra no lugar de um resumo legível;
- faltavam as datas de abertura/encerramento das inscrições nos cartões;
- o selo "cadastro de reserva" disparava por qualquer menção no artigo,
  mesmo quando o cargo-alvo tinha vaga imediata ("1 vaga + CR");
- retificação era descartada sem olhar se as inscrições seguiam abertas
  (caso Piraju/SP: retificação com inscrições até 30.8 foi descartada);
- o cartão só linkava a notícia, não o site da banca/inscrição;
- o cartão não mostrava salário nem linkava o PDF do edital de abertura
  (feedback do Danilo, 12.8.2026, inspirado numa newsletter concorrente).

Armadilhas já corrigidas (revisão adversarial de 6.8.2026), todas com teste:
- vagas do cargo VIZINHO ("Fiscal de Tributos Fonoaudiólogo (1 vaga)");
- termos sobrepostos dobrando a contagem ("Auditor Fiscal de Tributos" casa
  "auditor fiscal" E "fiscal de tributos" no mesmo parêntese);
- jornada/salário entre parênteses virando vagas ("(40 horas semanais)");
- data de PROVA na frase de inscrição virando fim do prazo;
- período cruzando o ano-novo ("15 de dezembro de 2026 a 15 de janeiro");
- "por meio do site www..." virando banca "site www".
"""

import datetime as dt
import re

from . import tempo
from .filtro import frase_para_regex, normalizar

_MESES = {
    "janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4, "maio": 5,
    "junho": 6, "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10,
    "novembro": 11, "dezembro": 12,
}
_RX_MES = "|".join(_MESES)

# "8 de outubro de 2026", "1o de novembro" (º vira "o" na normalização),
# com ano opcional (preenchido pela data anterior da mesma frase)
_RX_DATA_EXTENSO = re.compile(
    rf"\b(\d{{1,2}})[oº]?\s+de\s+({_RX_MES})(?:\s+de\s+(\d{{4}}))?"
)
# "6 a 30 de agosto de 2026" — dia solto herda mês/ano da data seguinte
_RX_DIA_SOLTO = re.compile(
    rf"\b(\d{{1,2}})[oº]?\s+(?:a|ate|e)\s+\d{{1,2}}[oº]?\s+de\s+(?:{_RX_MES})"
)
_RX_DATA_NUMERICA = re.compile(r"\b(\d{1,2})[/.](\d{1,2})[/.](\d{4})\b")

# etapas posteriores citadas na MESMA frase das inscrições: as datas a partir
# daí não são do prazo de inscrição (prova, resultado, convocação...)
_RX_OUTRA_ETAPA = re.compile(
    r"\bprovas?\b|\baplicac\w+\b|\bresultado\b|\bgabarito\b|\bhomologac\w+\b|"
    r"\bconvocac\w+\b|\bclassificac\w+\b|\bvalidade\b|\bisenc\w+\b|\bposse\b"
)

# domínio do site de inscrição citado no corpo ("pelo site www.x.com.br",
# "no site concursos.unioeste.br", "no site www.vunesp.com.br/TCSP2501")
_RX_SITE = re.compile(
    r"(?:pelos?|nos?|dos?)\s+sites?(?:\s+oficial)?(?:\s+do\s+concurso)?[\s,:]+"
    r"((?:www\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)+(?:/[\w.~-]+)?)",
    re.I,
)
_DOMINIOS_PROPRIOS = ("pciconcursos.com", "concursosnobrasil.com")
# "pelo site do IBAM", "no site oficial do INEPAM": nome da entidade, sem
# domínio — casa com o link do artigo em vez de chutar o primeiro link
_RX_SITE_NOME = re.compile(
    r"(?:pelos?|nos?|dos?)\s+sites?(?:\s+oficial)?\s+d[oae]s?\s+([A-ZÀ-Ú][\w&'.-]{2,40})"
)
# palavras genéricas demais para casar um domínio: "Instituto Brasileiro de
# Administração Municipal" não pode apontar para o site da prefeitura só
# porque o link do ente contém "municipal"
_TOKENS_GENERICOS = {
    "site", "sites", "concurso", "concursos", "fundacao", "instituto",
    "internet", "municipal", "municipais", "municipio", "estadual", "federal",
    "nacional", "brasileiro", "brasileira", "administracao", "desenvolvimento",
    "apoio", "publico", "publica", "publicos", "consultoria", "assessoria",
    "servicos", "pesquisa", "avaliacao", "selecao", "pessoal", "gestao",
    "prefeitura", "camara", "ensino", "educacao", "tecnologia", "estado",
}

# Banca: âncoras de QUEM ORGANIZA. As mesmas âncoras aparecem em frases de
# inscrição ("por meio do site www...", "realizada pela internet"), por isso
# a captura passa pelo veto abaixo — sem ele saía banca="site www"
# (Rifaina/SP) e banca="Secretaria Municipal de Finanças" (Manaus/AM).
_RX_BANCA = re.compile(
    r"(?:organizad[oa]s?\s+pel[oa]|executad[oa]s?\s+pel[oa]|realizad[oa]s?\s+pel[oa]|"
    r"sob\s+os\s+cuidados\s+d[oa]|a\s+cargo\s+d[oa]|"
    r"por\s+(?:meio|intermedio)\s+d[oa]|banca\s+organizadora\s+e\s+a?\s*)\s*"
    r"([a-z0-9][\w&'. -]{2,60}?)(?=\s*[,.;(]|\s+(?:para|com|visando|que|destinad)\b)"
)
# captura que não é nome de banca (é meio de inscrição, órgão ou lixo)
_VETO_BANCA = re.compile(
    r"^(site|sites|internet|portal|link|endereco|sistema|plataforma|aplicativo|"
    r"app|formulario|email|e-mail|prefeitura|municipio|camara|secretaria|"
    r"tribunal|orgao|comissao|empresa|www|http)\b|www\.|https?:"
)

_RX_VALIDADE = re.compile(
    r"validade\s+de\s+(\d+|um|uma|dois|duas|tres|quatro)\s+(anos?|mes(?:es)?)"
)
_EXTENSO = {"um": "1", "uma": "1", "dois": "2", "duas": "2", "tres": "3", "quatro": "4"}

# REMUNERAÇÃO: campo pedido pelo Danilo (12.8.2026) para pôr o salário em
# destaque no cartão, como a newsletter que inspirou esta rodada faz. Dois
# formatos aparecem nos artigos: o cheio "R$ 1.621,00" (ponto de milhar,
# vírgula decimal) e o abreviado "R$ 6,5 mil"/"R$ 22,8 mil" (vírgula decimal
# solta, sem ponto de milhar). Alternativas separadas na regex porque, se
# fossem uma regra só, "6,5" sem ponto de milhar colidiria com o formato
# cheio ou o cheio teria que aceitar 1-3 dígitos soltos por engano.
_RX_DINHEIRO = re.compile(
    r"r\$\s*(\d{1,3}(?:\.\d{3})+(?:,\d{2})?|\d+(?:,\d{1,2})?)\s*(mil)?\b"
)
# valor de taxa de inscrição, auxílio-alimentação ou multa não é remuneração,
# mesmo aparecendo na MESMA frase do salário (caso real Marialva/PR:
# "R$ 6.768,66, acrescidos de auxílio-alimentação de R$ 730,00" não pode
# devolver R$ 730 como remuneração do cargo). "auxilios?" e não "auxili\w*":
# a lista de cargos de Cuité de Mamanguape/PB tem "Auxiliar de Saúde Bucal"
# na MESMA frase (a lista inteira vira uma frase só, sem ponto entre os
# cargos) — "auxili\w*" casava "auxiliar" e cortava a frase antes da
# remuneração, apagando R$ 1.621,00 a R$ 11.975,00 inteiros.
_RX_DINHEIRO_EXCLUIR = re.compile(r"\btaxas?\b|\bauxilios?\b|\bmultas?\b")
_RX_REMUNERACAO_KW = re.compile(r"\bremunera\w*|\bsalari\w*")

# Parênteses de VAGAS: precisa falar de vaga/CR/cadastro. Só dígito não basta
# — "(40 horas semanais)" e "(R$ 3.500,00)" viravam 40 e 3 vagas.
_RX_VAGAS_PARENTESES = re.compile(r"\bvagas?\b|\bcr\b|cadastro")
# quantidade colada a "vaga(s)" ("2 vagas + CR ampla; 1 vaga PcD" = 3) ou ao
# formato abreviado "(1 + CR)", usado pelo IBAM (caso Balneário Piçarras/SC)
_RX_QUANTIDADE = re.compile(r"(\d+)\s*vagas?\b|(\d+)\s*\+\s*(?:cr\b|cadastro)")

# Entre o fim do nome do cargo e o "(" só pode haver CONTINUAÇÃO do próprio
# cargo (preposição, hífen, especialidade, jornada, romano). Qualquer outra
# palavra indica que o parêntese é do cargo seguinte da lista — caso real
# "Farmacêutico Fiscal de Obras Fiscal de Tributos Fonoaudiólogo (1 vaga)",
# em que a vaga é do Fonoaudiólogo.
_CONTINUADORES = {
    "de", "da", "do", "das", "dos", "e", "em", "no", "na", "para", "-", "/", "|",
    "municipal", "municipais", "estadual", "estaduais", "federal", "publico",
    "publica", "tributario", "tributaria", "tributarios", "fazendario",
    "interno", "externo", "junior", "senior", "pleno", "i", "ii", "iii", "iv", "v",
    "nivel", "superior", "medio", "fundamental", "completo", "incompleto",
    "geral", "adjunto", "substituto", "classe", "grau", "categoria",
    "vi", "vii", "viii", "ix", "x",
}
_RX_TOKEN_NEUTRO = re.compile(r"^\d+[hº°]?$|^r\$$|^\d+/\d+$")
_JANELA_GAP = 60
_GAP_MAX = 40


def _fmt(iso):
    """AAAA-MM-DD -> d.m.aaaa."""
    try:
        d = dt.date.fromisoformat(iso)
        return f"{d.day}.{d.month}.{d.year}"
    except (ValueError, TypeError):
        return iso or ""


_ACENTOS_CARGO = {
    "tributario": "tributário", "tributaria": "tributária", "tecnico": "técnico",
    "fazendario": "fazendário", "publico": "público", "publica": "pública",
    "arrecadacao": "arrecadação", "atribuicao": "atribuição", "hibrida": "híbrida",
    "financas": "finanças", "municipio": "município",
}
_MINUSCULAS_CARGO = {"de", "da", "do", "das", "dos", "e", "em", "a", "o"}


def titulo_cargo(cargo):
    """'agente de arrecadacao' -> 'Agente de Arrecadação'. Nome já formatado
    (com maiúscula) passa intacto."""
    if not cargo or any(c.isupper() for c in cargo):
        return cargo
    palavras = []
    for bruta in cargo.split():
        nucleo = bruta.strip("()[],.;:")
        prefixo = bruta[: len(bruta) - len(bruta.lstrip("([")) ]
        sufixo = bruta[len(prefixo) + len(nucleo):]
        nucleo = _ACENTOS_CARGO.get(nucleo, nucleo)
        if nucleo not in _MINUSCULAS_CARGO:
            nucleo = nucleo[:1].upper() + nucleo[1:]
        palavras.append(f"{prefixo}{nucleo}{sufixo}")
    return " ".join(palavras)


def _datas_da_frase(frase, ano_padrao=None):
    """Datas da frase, em ordem. Ano ausente herda da data ANTERIOR e avança
    um ano quando a sequência retrocede (período cruzando o ano-novo)."""
    achadas = []  # (pos, dia, mes, ano|None)
    for m in _RX_DATA_EXTENSO.finditer(frase):
        achadas.append((m.start(), int(m.group(1)), _MESES[m.group(2)],
                        int(m.group(3)) if m.group(3) else None))
    for m in _RX_DATA_NUMERICA.finditer(frase):
        dia, mes, ano = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mes <= 12:
            achadas.append((m.start(), dia, mes, ano))
    # "6 a 30 de agosto": o dia solto herda mês/ano da primeira data extensa adiante
    for m in _RX_DIA_SOLTO.finditer(frase):
        seguinte = _RX_DATA_EXTENSO.search(frase, m.start())
        if seguinte:
            achadas.append((m.start(), int(m.group(1)),
                            _MESES[seguinte.group(2)],
                            int(seguinte.group(3)) if seguinte.group(3) else None))
    achadas.sort()

    anos_explicitos = [a for _, _, _, a in achadas if a]
    corrente = anos_explicitos[0] if anos_explicitos else ano_padrao
    datas, anterior = [], None
    for _, dia, mes, ano in achadas:
        if ano:
            corrente = ano
        elif anterior and anterior.month >= 11 and mes <= 3:
            # único avanço de ano admitido: período que cruza o ano-novo
            # ("15 de dezembro de 2026 a 15 de janeiro"). Qualquer outro
            # retrocesso é data de outro contexto (edital original, data de
            # publicação) — mantém o ano corrente e deixa a incoerência
            # aparecer, para _periodo_inscricoes devolver vazio (fail-closed).
            corrente = (corrente or 0) + 1
        try:
            data = dt.date(corrente, mes, dia)
        except (ValueError, TypeError):
            continue
        datas.append(data)
        anterior = data
    return datas


def _trecho_de_inscricao(frase):
    """Corta a frase antes da menção a outra etapa (prova, resultado...):
    as datas dali em diante não pertencem ao prazo de inscrição."""
    pos_inscricao = frase.find("inscric")
    m = _RX_OUTRA_ETAPA.search(frase)
    if m and (pos_inscricao == -1 or m.start() > pos_inscricao):
        return frase[: m.start()]
    return frase


def _periodo_inscricoes(texto_norm, ano_padrao):
    """(inicio, fim) como date|None, lidos da melhor frase sobre inscrições."""
    frases = re.split(r"(?<=[.!?;])\s+", texto_norm)
    candidatas = []
    for f in frases:
        if not re.search(r"inscric|cadastro pode ser feito", f):
            continue
        if re.search(r"\bisenc\w+|\bisento", f[: f.find("inscric") + 1] or f[:40]):
            continue
        trecho = _trecho_de_inscricao(f)
        if "inscric" not in trecho and "cadastro pode ser feito" not in trecho:
            continue
        datas = _datas_da_frase(trecho, ano_padrao)
        if datas:
            candidatas.append((len(datas), trecho, datas))
    if not candidatas:
        return None, None
    # a frase com mais datas é a que enuncia o período completo (início e fim)
    _, frase, datas = max(candidatas, key=lambda c: c[0])
    if len(datas) >= 2:
        inicio, fim = datas[0], datas[-1]
        if fim < inicio:  # extração incoerente: nada é afirmado
            return None, None
        return inicio, fim
    # uma data só: "até/prorrogadas para X" é fim; "a partir de X" é início
    if re.search(r"\bate\b|encerra|prorrogad", frase):
        return None, datas[0]
    if re.search(r"a partir", frase):
        return datas[0], None
    return None, None


def _gap_e_continuacao(gap):
    """True se o texto entre o cargo e o '(' for continuação do próprio cargo.

    Decide pelo PRIMEIRO token: especialidade e complemento vêm ligados por
    conector ("- DIPE - Ciências Contábeis", "e Tributos", "de Tributos
    Municipais", "Nível Superior"); outro cargo da lista entra justaposto ou
    depois de vírgula ("Fiscal de Tributos Fonoaudiólogo (1 vaga)",
    "Fiscal de Tributos, Fonoaudiólogo (2 vagas)").
    """
    limpo = gap.strip()
    if not limpo:
        return True
    if len(limpo) > _GAP_MAX or limpo[0] in ",;":
        return False
    primeiro = re.split(r"[\s,]+", limpo)[0]
    if not primeiro:
        return False
    return (
        primeiro in _CONTINUADORES
        or bool(_RX_TOKEN_NEUTRO.match(primeiro))
        or set(primeiro) <= set("-–/|.")
    )


def _vagas_do_cargo(texto_norm, termos, original=None, alinhado=False):
    """{nome do cargo: {"n": int|None, "cr": bool}} lido de "Cargo (1 vaga + CR)".

    Duas armadilhas resolvidas aqui (revisão de 6.8.2026):
    - cada parêntese conta UMA vez, ainda que dois termos se sobreponham no
      mesmo nome ("Auditor Fiscal de Tributos" casa dois termos);
    - a agregação é pelo NOME COMPLETO do cargo, não pelo termo: "Agente
      Fiscal em Enfermagem I (1 vaga)" e "Agente Fiscal em Engenharia
      Sanitária I (1 vaga)" são dois cargos, não "Agente Fiscal (2 vagas)".
      Especialidade ligada por hífen ("Auditor de Controle Externo - DIPE -
      Ciências Contábeis") continua somando no cargo-mãe.
    """
    achados = {}  # posição do parêntese -> (distancia, nome, numeros, cr)
    for termo in termos:
        rx = re.compile(
            frase_para_regex(termo).pattern + r"([^()]{0,%d}?)\(\s*([^()]{1,60}?)\s*\)"
            % _JANELA_GAP
        )
        for m in rx.finditer(texto_norm):
            gap, conteudo = m.group(1), m.group(2)
            if not _RX_VAGAS_PARENTESES.search(conteudo):
                continue
            if not _gap_e_continuacao(gap):
                continue  # o parêntese é do cargo seguinte da lista
            numeros = [int(n) for par in _RX_QUANTIDADE.findall(conteudo) for n in par if n]
            cr = bool(re.search(r"\bcr\b|cadastro", conteudo))
            if not numeros and not cr:
                continue
            complemento = gap.strip()
            # hífen/barra = especialidade do mesmo cargo (soma no cargo-mãe);
            # qualquer outro complemento identifica um cargo próprio
            if not complemento or complemento[0] in "-–/|":
                nome = termo
            elif alinhado and original:
                # grafia real do artigo, com acentos e maiúsculas
                nome = re.sub(r"\s+", " ", original[m.start():m.end(1)]).strip()
            else:
                nome = re.sub(r"\s+", " ", f"{termo} {complemento}").strip()
            chave = m.end()
            anterior = achados.get(chave)
            if anterior is None or len(gap) < anterior[0]:
                achados[chave] = (len(gap), nome, numeros, cr)

    vagas = {}
    for _distancia, nome, numeros, cr in achados.values():
        atual = vagas.setdefault(nome, {"n": None, "cr": False})
        if numeros:
            atual["n"] = (atual["n"] or 0) + sum(numeros)
        atual["cr"] = atual["cr"] or cr
    return vagas


def _vagas_texto(vagas):
    """{"fiscal tributario": {"n": 1, "cr": True}} -> "1 vaga + CR"."""
    partes = []
    for termo, v in vagas.items():
        if v["n"]:
            s = f"{v['n']} vaga" + ("s" if v["n"] > 1 else "")
            if v["cr"]:
                s += " + CR"
        elif v["cr"]:
            s = "cadastro de reserva"
        else:
            continue
        partes.append((termo, s))
    if not partes:
        return ""
    if len(partes) == 1:
        return partes[0][1]
    return " · ".join(f"{titulo_cargo(t)}: {s}" for t, s in partes)


def _valor_dinheiro(bruto, mil):
    """'2.565,32' -> 2565.32; '6,5' com mil=True -> 6500.0. Ponto é sempre
    separador de milhar e vírgula é sempre decimal (nunca o inverso, é o
    padrão monetário brasileiro em qualquer um dos dois formatos aceitos)."""
    limpo = bruto.replace(".", "").replace(",", ".")
    try:
        valor = float(limpo)
    except ValueError:
        return None
    return valor * 1000 if mil else valor


def _fmt_dinheiro(valor):
    """2565.32 -> 'R$ 2.565,32' (sempre com centavos, mesmo vindo de '6,5 mil')."""
    inteiro, centavos = divmod(round(valor * 100), 100)
    milhar = f"{inteiro:,}".replace(",", ".")
    return f"R$ {milhar},{centavos:02d}"


def _remuneracao(texto_norm, termos):
    """(texto pronto pro cartão, valor float|None) — vazio quando não há
    valor confiável (fail-closed).

    Duas fontes, nesta ordem de prioridade (regra do Danilo, 12.8.2026):
    1. valor AMARRADO AO CARGO-ALVO — "Fiscal de Tributos (R$ 4.500,00)" —,
       pela mesma mecânica de _vagas_do_cargo: o parêntese só pertence ao
       termo se o texto entre os dois for continuação do próprio cargo,
       senão é o valor do cargo VIZINHO (mesma armadilha das vagas);
    2. só a FAIXA do certame ("remuneração de R$ X a R$ Y", "salários vão
       de R$ X a R$ Y", "até R$ X") — usa o TETO e marca "até", porque o
       piso quase sempre é de um cargo de nível fundamental, não do
       cargo-alvo, e dizer que o cargo-alvo paga o piso seria inventar.
    """
    for termo in termos:
        rx = re.compile(
            frase_para_regex(termo).pattern + r"([^()]{0,%d}?)\(\s*([^()]{1,60}?)\s*\)"
            % _JANELA_GAP
        )
        for m in rx.finditer(texto_norm):
            gap, conteudo = m.group(1), m.group(2)
            if not _gap_e_continuacao(gap) or _RX_DINHEIRO_EXCLUIR.search(conteudo):
                continue
            md = _RX_DINHEIRO.search(conteudo)
            if md:
                valor = _valor_dinheiro(md.group(1), md.group(2))
                if valor is not None:
                    return _fmt_dinheiro(valor), valor

    for frase in re.split(r"(?<=[.!?;])\s+", texto_norm):
        if not _RX_REMUNERACAO_KW.search(frase):
            continue
        m_ex = _RX_DINHEIRO_EXCLUIR.search(frase)
        trecho = frase[: m_ex.start()] if m_ex else frase
        valores = []
        for md in _RX_DINHEIRO.finditer(trecho):
            valor = _valor_dinheiro(md.group(1), md.group(2))
            if valor is not None:
                valores.append((md.start(), valor))
        if not valores:
            continue
        maior = max(v for _, v in valores)
        # "até" já dito no texto ("salários de até R$ X") ou faixa com 2+
        # valores (o maior é o teto, o menor não pertence ao cargo-alvo)
        ate_no_texto = bool(re.search(r"\bate\b", trecho[: valores[0][0]]))
        if len(valores) > 1 or ate_no_texto:
            return f"até {_fmt_dinheiro(maior)}", maior
        return _fmt_dinheiro(maior), maior
    return "", None


def _resumo(texto_original, titulo=""):
    """Primeira frase substancial do corpo do artigo. A quebra de linha
    também separa e o próprio título é pulado (o cnb entrega
    'título\\nresumo\\ncorpo' e o título não termina em ponto — sem isso o
    cartão repetia o próprio título)."""
    titulo_norm = normalizar(titulo).strip()
    for frase in re.split(r"(?<=[.!?])\s+|\n", texto_original.strip()):
        f = frase.strip()
        if len(f) < 60 or f.lower().startswith(("confira", "ouça", "escute")):
            continue
        if titulo_norm and normalizar(f).startswith(titulo_norm):
            continue
        return (f[:237] + "…") if len(f) > 240 else f
    return ""


def _site_inscricao(texto_original, links_artigo, banca):
    """Domínio citado na frase de inscrição; senão, link do artigo que case o
    NOME citado ("pelo site do IBAM") ou a banca. Sem isso, VAZIO — o primeiro
    link externo costuma ser o site da prefeitura, e rotulá-lo "site de
    inscrição" seria mentira (casos Guarulhos/SP e Itararé/SP, 6.8.2026)."""
    m = _RX_SITE.search(texto_original)
    if m and not any(d in m.group(1).lower() for d in _DOMINIOS_PROPRIOS):
        url = m.group(1)
        return url if url.startswith("http") else f"https://{url}"

    nomes = _RX_SITE_NOME.findall(texto_original) + ([banca] if banca else [])
    tokens = {
        normalizar(t).strip(".,;:'\"")
        for nome in nomes
        for t in re.split(r"[\s\-]+", nome)
        if len(t.strip(".,;:")) >= 4
    } - _TOKENS_GENERICOS
    # nome todo genérico ("Instituto Brasileiro de Administração Municipal"):
    # a sigla das iniciais é o que aparece no domínio (ibam-concursos.org.br)
    for nome in nomes:
        palavras = [p for p in re.split(r"[\s\-]+", normalizar(nome)) if len(p) >= 3]
        if len(palavras) >= 3:
            tokens.add("".join(p[0] for p in palavras))
    if not tokens:
        return ""
    # casa contra o HOST do link, nunca contra o texto da âncora: o link do
    # ente vem antes do link da banca e "Câmara Municipal de X" casaria
    # qualquer token genérico do nome da banca
    for _texto, href in links_artigo or []:
        if any(d in href for d in _DOMINIOS_PROPRIOS):
            continue
        host = normalizar(re.sub(r"^https?://", "", href).split("/")[0])
        if any(t in host for t in tokens):
            return href
    return ""


# EDITAL: link do PDF de abertura citado no artigo (campo pedido pelo
# Danilo, 12.8.2026). O link mais citado no corpo costuma ser o mais
# RECENTE, quase sempre uma retificação, não o edital original — por isso a
# rejeição roda ANTES da aceitação, e qualquer PDF de fase posterior
# (retificação, errata, gabarito, resultado, convocação, homologação, anexo,
# cronograma) é descartado mesmo citando "edital" no nome ou no texto do link.
_RX_EDITAL_ACEITAR = re.compile(r"\bedital\b")
_RX_EDITAL_REJEITAR = re.compile(
    r"retificac\w*|errata|gabarito|resultado|convocac\w*|homologac\w*|anexo|cronograma"
)
_RX_PDF = re.compile(r"\.pdf(?:[?#]|$)", re.I)


def _edital_url(links_artigo):
    """PDF do edital de abertura entre os links do artigo, ou vazio quando
    nenhum link do artigo é claramente o edital (fail-closed)."""
    for texto, href in links_artigo or []:
        if not _RX_PDF.search(href):
            continue
        alvo = normalizar(f"{texto} {href}")
        if _RX_EDITAL_REJEITAR.search(alvo):
            continue
        if _RX_EDITAL_ACEITAR.search(alvo):
            return href
    return ""


def vazio():
    """Extração neutra — para fontes cujo texto não é artigo de notícia
    (diários oficiais), onde datas e parênteses vêm de contexto alheio."""
    return {
        "inscricoes_inicio": "", "inscricoes_fim": "", "inscricoes_texto": "",
        "vagas": {}, "vagas_texto": "", "cr_somente": False,
        "banca": "", "site_inscricao": "", "validade": "", "resumo": "",
        "remuneracao": "", "remuneracao_valor": None, "edital_url": "",
    }


def extrair(achado, termos, hoje=None):
    """Campos estruturados do artigo; tudo que não for encontrado fica vazio.

    Devolve dict com: inscricoes_inicio/fim (ISO ou ""), inscricoes_texto,
    vagas (por termo), vagas_texto, cr_somente, banca, site_inscricao,
    validade, resumo, remuneracao (texto pronto pro cartão), remuneracao_valor
    (float|None, para ordenar), edital_url (PDF do edital de abertura).
    """
    hoje = hoje or tempo.hoje()
    original = f"{achado.titulo}\n{achado.cargo_texto}\n{achado.detalhes.get('trecho', '')}"
    texto = normalizar(original)
    # normalizar() faz NFKD, que expande caracteres de compatibilidade ("…"
    # vira "..."): quando o comprimento muda, os índices do texto normalizado
    # não valem no original e recortar por eles corta o nome da banca no meio
    alinhado = len(texto) == len(original)

    inicio, fim = _periodo_inscricoes(texto, hoje.year)
    vagas = _vagas_do_cargo(texto, termos, original, alinhado)
    remuneracao, remuneracao_valor = _remuneracao(texto, termos)
    # o coletor de banca (ex.: selecao.net.br) já lê o edital_url da própria
    # página de detalhe, com muito mais precisão do que o artigo de notícia
    # — esse valor tem precedência e o extrator não sobrescreve
    edital_url = achado.detalhes.get("edital_url") or _edital_url(
        achado.detalhes.get("links_artigo")
    )
    m_banca = _RX_BANCA.search(texto)
    banca = ""
    if m_banca and not _VETO_BANCA.search(m_banca.group(1)):
        if alinhado:
            # recupera a grafia original (com maiúsculas/acentos) pela posição
            banca = original[m_banca.start(1):m_banca.end(1)].strip(" -,")
        else:
            banca = titulo_cargo(m_banca.group(1).strip(" -,"))
    m_val = _RX_VALIDADE.search(texto)
    validade = ""
    if m_val:
        n = _EXTENSO.get(m_val.group(1), m_val.group(1))
        if m_val.group(2).startswith("ano"):
            validade = f"{n} {'anos' if n != '1' else 'ano'}"
        else:
            validade = f"{n} {'meses' if n != '1' else 'mês'}"
        if "prorrogad" in texto[m_val.start():m_val.start() + 200]:
            validade += ", prorrogável"

    if inicio and fim:
        inscricoes_texto = f"{_fmt(inicio.isoformat())} a {_fmt(fim.isoformat())}"
    elif fim:
        inscricoes_texto = f"até {_fmt(fim.isoformat())}"
    elif inicio:
        inscricoes_texto = f"a partir de {_fmt(inicio.isoformat())}"
    else:
        inscricoes_texto = ""

    return {
        "inscricoes_inicio": inicio.isoformat() if inicio else "",
        "inscricoes_fim": fim.isoformat() if fim else "",
        "inscricoes_texto": inscricoes_texto,
        "vagas": vagas,
        "vagas_texto": _vagas_texto(vagas),
        "cr_somente": bool(vagas) and all(not v["n"] and v["cr"] for v in vagas.values()),
        "banca": banca,
        "site_inscricao": _site_inscricao(
            original, achado.detalhes.get("links_artigo"), banca
        ),
        "validade": validade,
        "resumo": _resumo(
            achado.cargo_texto or achado.detalhes.get("trecho", ""), achado.titulo
        ),
        "remuneracao": remuneracao,
        "remuneracao_valor": remuneracao_valor,
        "edital_url": edital_url,
    }
