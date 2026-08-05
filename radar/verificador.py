"""Verificação profunda SEM IA: decide os casos incertos pelo texto integral.

Depois que radar/regras.py resolve os casos óbvios pelo título/trecho, os
incertos passam por aqui. A ideia: um edital de abertura tem anatomia
estereotipada (seções de inscrições com datas, taxa, requisitos, vagas,
provas, cronograma), e um ato de pessoal também (servidor, matrícula,
ocupante do cargo, conceder, enquadramento). Em vez de opinar sobre um
trecho de 300 caracteres, baixamos o DOCUMENTO INTEIRO e pontuamos a
janela de texto ao redor de cada ocorrência do cargo-alvo.

Vereditos:
- ("abertura", motivo, extras) — anatomia de edital + cargo na janela +
  evidência de inscrição; extras traz o período de inscrições extraído.
- ("descarte", motivo, {})   — janela dominada por ato de pessoal ou fase
  sem inscrição, sem nenhum sinal de abertura.
- ("incerto", motivo, {})    — indecidível (ou falha de rede ao baixar);
  o item fica na fila de pendentes e aparece no painel como "aguardando
  confirmação".

Sem IA por decisão de produto (5.8.2026): a dependência de IA gratuita
morreu com o GitHub Models e o Danilo vetou API paga.
"""

import re

import requests

from .filtro import normalizar

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}
TIMEOUT = 60
JANELA = 3500          # chars para cada lado da ocorrência do cargo
MIN_TEXTO_LOCAL = 800  # cargo_texto menor que isso pede busca remota

# Anatomia de edital de abertura — cada grupo conta no máximo 1 ponto,
# então o placar mede DIVERSIDADE de seções, não repetição.
_ANATOMIA = {
    "secao_inscricoes": r"\bda?s? inscricoes\b|\bda inscricao\b",
    "taxa": r"\btaxa de inscricao\b|\bvalor da (taxa|inscricao)\b",
    "vagas": r"\bda?s? vagas\b|\bquadro de vagas\b|\btotal de vagas\b",
    "requisitos": r"\brequisitos?\b|\bescolaridade\b|\bnivel (medio|superior|fundamental)\b",
    "remuneracao": r"\bvencimento\b|\bremuneracao\b|\bsalario\b|\bsubsidio\b",
    "carga": r"\bcarga horaria\b",
    "provas": r"\bprova objetiva\b|\bdas provas\b|\bconteudo programatico\b",
    "cronograma": r"\bcronograma\b|\bcalendario do concurso\b",
    "banca": r"\bbanca\b|\borganizadora\b|\binstituto\b|\bfundacao\b",
    "edital": r"\bedital de abertura\b|\btorna publica? a abertura\b|\babre concurso\b",
}
_RX_ANATOMIA = {k: re.compile(p) for k, p in _ANATOMIA.items()}

# Período/prazo de inscrição com data ou horário — o sinal mais forte.
_RX_PERIODO = re.compile(
    r"inscric\w+[^.;]{0,160}?(\d{1,2}[/.]\d{1,2}[/.]\d{2,4})"
    r"(?:[^.;0-9]{0,60}?(\d{1,2}[/.]\d{1,2}[/.]\d{2,4}))?"
)
_RX_PERIODO_ALT = re.compile(
    r"(?:periodo|prazo) de inscric\w+|inscric\w+ (?:abertas|encerram|ate as)"
)

# Contra-evidência na janela: o cargo aparece em ato de pessoal ou em fase
# posterior de concurso (reaproveita a semântica de radar/regras.py).
_CONTRA = [
    r"\bmatricula\b",
    r"\bocupante d[eo] cargo\b",
    r"\bconceder?\b",
    r"\benquadramento\b",
    r"\bnomear\b",
    r"\bnomeacao\b",
    r"\bexonera\w*\b",
    r"\baposentad\w*\b",
    r"\bprogressao\b",
    r"\bferias\b",
    r"\blicenca\b",
    r"\bconvoca\w*\b",
    r"\bhomologacao d[oa] resultado\b",
    r"\bgabarito\b",
    r"\bresultado (final|definitivo|preliminar)\b",
    r"\bclassificacao final\b",
    r"\beliminad\w*\b",
    r"\bposse\b",
    r"\bjunta medica\b",
]
_RX_CONTRA = [re.compile(p) for p in _CONTRA]

_RX_TAGS = re.compile(r"<script.*?</script>|<style.*?</style>|<[^>]+>", re.S | re.I)


def _limpar_html(html):
    return re.sub(r"\s+", " ", _RX_TAGS.sub(" ", html))


def obter_texto(achado):
    """Melhor texto disponível do documento; None se só falhar rede.

    Ordem: cargo_texto já integral (sigpub/pci/selecao) > .txt do Querido
    Diário > HTML da própria URL do achado.
    """
    # título + trecho sempre entram: o cargo pode estar só no trecho quando
    # o cargo_texto vem truncado (sigpub corta em 3000 chars — caso real:
    # decreto de enquadramento de Querência do Norte, 5.8.2026)
    base = f"{achado.titulo}\n{achado.detalhes.get('trecho', '')}"
    local = (achado.cargo_texto or "").strip()
    if len(local) >= MIN_TEXTO_LOCAL:
        return f"{base}\n{local}"

    txt_url = achado.detalhes.get("txt_url")
    if txt_url:
        resp = requests.get(txt_url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.text

    if achado.url and achado.url.startswith("http"):
        resp = requests.get(achado.url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        tipo = resp.headers.get("content-type", "")
        if "html" in tipo or "text" in tipo:
            return f"{base}\n{local}\n{_limpar_html(resp.text)}"

    # sem fonte remota: analisa o que houver (título + trecho + cargo_texto)
    return f"{base}\n{local}"


def _rx_termo(termo):
    palavras = [re.escape(p) for p in normalizar(termo).split()]
    return re.compile(r"\b" + r"[-\s]+".join(palavras) + r"\b")


def _pontuar_janela(janela):
    grupos = [k for k, rx in _RX_ANATOMIA.items() if rx.search(janela)]
    periodo = _RX_PERIODO.search(janela)
    periodo_alt = bool(_RX_PERIODO_ALT.search(janela))
    contra = sum(1 for rx in _RX_CONTRA for _ in rx.finditer(janela))
    pontos = len(grupos) + (3 if periodo else 0) + (1 if periodo_alt else 0)
    return pontos, grupos, periodo, contra


def analisar(texto, termos):
    """Pontua a janela ao redor de cada ocorrência de cada termo casado."""
    t = normalizar(texto)
    melhor = None  # (pontos, grupos, periodo, contra, termo)
    for termo in termos:
        for m in _rx_termo(termo).finditer(t):
            ini = max(0, m.start() - JANELA)
            janela = t[ini : m.end() + JANELA]
            pontos, grupos, periodo, contra = _pontuar_janela(janela)
            if melhor is None or pontos - contra > melhor[0] - melhor[3]:
                melhor = (pontos, grupos, periodo, contra, termo)
    if melhor is None:
        return "incerto", "cargo não localizado no texto integral", {}

    pontos, grupos, periodo, contra, termo = melhor

    # ABERTURA: diversidade de seções de edital + evidência de inscrição
    # (período com data OU marcador expresso) e contra-evidência minoritária.
    tem_inscricao = periodo is not None or "secao_inscricoes" in grupos or "edital" in grupos
    if pontos >= 6 and tem_inscricao and contra <= pontos // 2:
        extras = {}
        if periodo:
            datas = [d for d in periodo.groups() if d]
            extras["inscricoes"] = " a ".join(datas)
        motivo = (
            f"anatomia de edital ({len(grupos)} seções: {', '.join(sorted(grupos))}) "
            f"+ cargo '{termo}' na janela"
        )
        return "abertura", motivo, extras

    # DESCARTE: nenhuma evidência de abertura e contra-evidência presente.
    if pontos < 3 and contra >= 2:
        return "descarte", (
            f"texto integral sem anatomia de edital (pontos={pontos}) e com "
            f"{contra} sinal(is) de ato de pessoal/fase posterior"
        ), {}

    return "incerto", (
        f"evidência insuficiente (pontos={pontos}, contra={contra}, "
        f"seções: {', '.join(sorted(grupos)) or 'nenhuma'})"
    ), {}


def verificar(achado, termos):
    """Baixa/reúne o texto integral e analisa. Falha de rede => incerto."""
    try:
        texto = obter_texto(achado)
    except Exception as e:  # noqa: BLE001 — rede indisponível não decide mérito
        return "incerto", f"falha ao obter texto integral ({e!r})", {}
    if not texto or len(texto.strip()) < 40:
        return "incerto", "texto integral vazio ou curto demais", {}
    return analisar(texto, termos)
