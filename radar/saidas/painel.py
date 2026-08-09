"""Gera o painel estático (docs/index.html), publicado via GitHub Pages.

Desenho do painel (feedback do Danilo, 6.8.2026):
- fila "aguardando confirmação" no TOPO, como caixa clicável/expansível;
- agrupamento geográfico por proximidade de Maringá/PR: Paraná primeiro,
  depois vizinhos (SC/SP/MS), depois o resto — dentro de cada grupo, quem
  encerra inscrição primeiro aparece primeiro;
- cartão mostra datas de abertura e encerramento das inscrições, vagas
  reais do cargo ("1 vaga + CR" em vez de selo genérico), banca, validade
  e link do site de inscrição — nunca o regex interno da triagem;
- encerrados/suspensos saem da vitrine principal para uma caixa recolhida.
"""

import datetime as dt
import html

from .. import tempo
from ..extrator import titulo_cargo

CATEGORIAS = {
    "tributario": ("Tributário", "#0a6"),
    "controle": ("Controle", "#06b"),
    "conferir": ("Conferir área", "#b70"),
}

UFS_VIZINHAS = ("SC", "SP", "MS")

CSS = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body { font-family: -apple-system, 'Segoe UI', Roboto, Arial, sans-serif;
       margin: 0 auto; max-width: 860px; padding: 24px 16px; line-height: 1.5;
       background: #fafafa; color: #1c1c1c; }
h1 { font-size: 1.5rem; margin-bottom: 4px; }
.sub { color: #666; font-size: .9rem; margin-bottom: 20px; }
.item { background: #fff; border: 1px solid #e3e3e3; border-radius: 10px;
        padding: 14px 16px; margin-bottom: 12px; }
.item a.titulo { color: inherit; text-decoration: none; font-weight: 600; }
.item a.titulo:hover { text-decoration: underline; }
.meta { color: #666; font-size: .85rem; margin-top: 6px; }
.trecho { color: #555; font-size: .85rem; margin-top: 6px; font-style: italic; }
.nota { color: #8a5a00; font-size: .82rem; margin-top: 6px; }
.badge { display: inline-block; color: #fff; border-radius: 6px;
         font-size: .75rem; font-weight: 600; padding: 2px 8px; margin-right: 6px; }
.chip { display: inline-block; color: #444; border: 1px solid #c9c9c9;
        border-radius: 6px; font-size: .75rem; font-weight: 600;
        padding: 1px 8px; margin-right: 6px; background: transparent; }
.tags { margin-bottom: 6px; }
.prazo { font-weight: 700; color: #0a6; }
.prazo-curto { font-weight: 700; color: #b00; }
.links { margin-top: 6px; font-size: .85rem; }
.links a { color: #06b; text-decoration: none; margin-right: 14px; }
.links a:hover { text-decoration: underline; }
h2 { font-size: 1.05rem; margin: 26px 0 12px; }
details { margin: 14px 0; }
details > summary { cursor: pointer; font-weight: 600; font-size: .95rem;
                    padding: 10px 14px; background: #f0f0f2; border: 1px solid #e0e0e3;
                    border-radius: 10px; list-style: none; }
details > summary::before { content: "▸ "; }
details[open] > summary::before { content: "▾ "; }
details > summary::-webkit-details-marker { display: none; }
details > .conteudo { margin-top: 12px; }
@media (prefers-color-scheme: dark) {
  body { background: #16181c; color: #e6e6e6; }
  .item { background: #1f2228; border-color: #33363d; }
  .sub, .meta { color: #9aa0a8; }
  .trecho { color: #8a9099; }
  .nota { color: #d9a53f; }
  .chip { color: #cfd3d9; border-color: #4a4e57; }
  .prazo { color: #4cc38a; }
  .prazo-curto { color: #ff7b72; }
  .links a { color: #58a6ff; }
  details > summary { background: #1f2228; border-color: #33363d; }
}
"""


def _data(iso):
    try:
        return dt.date.fromisoformat(iso or "")
    except (ValueError, TypeError):
        return None


def _fmt_data(iso):
    """AAAA-MM-DD -> d.m.aaaa (sem zero à esquerda)."""
    d = _data(iso)
    return f"{d.day}.{d.month}.{d.year}" if d else (iso or "")


def _local(item):
    """Município/UF (ou órgão/UF). Sem município nem órgão, retorna '' —
    UF sozinha não identifica nada e o título original é melhor."""
    lugar = item.get("municipio") or item.get("orgao") or ""
    if not lugar:
        return ""
    uf = item.get("uf") or ""
    return f"{lugar}/{uf}" if uf and uf not in lugar else lugar


def _status(item, hoje):
    """em_curso | futuro | encerrado | suspenso | sem_prazo.

    em_curso e futuro ficam na vitrine; o cabeçalho os conta separado — dizer
    "41 com inscrições abertas" incluindo 14 que ainda vão abrir é mentira.
    """
    det = item.get("detalhes") or {}
    ia = det.get("ia") or {}
    if ia.get("classe") == "suspensao":
        return "suspenso"
    fim, inicio = _data(det.get("inscricoes_fim")), _data(det.get("inscricoes_inicio"))
    if fim:
        if fim < hoje:
            return "encerrado"
        return "futuro" if inicio and inicio > hoje else "em_curso"
    return "sem_prazo"


VITRINE = ("em_curso", "futuro", "sem_prazo")
ARQUIVO = ("encerrado", "suspenso")


def _grupo(item):
    uf = item.get("uf") or ""
    if uf == "PR":
        return 0
    if uf in UFS_VIZINHAS:
        return 1
    return 2


def _linha_inscricoes(item, hoje):
    det = item.get("detalhes") or {}
    ia = det.get("ia") or {}
    ini, fim = _data(det.get("inscricoes_inicio")), _data(det.get("inscricoes_fim"))
    texto = ia.get("inscricoes", "")
    if not (ini or fim or texto):
        return ""
    periodo = (
        f"{_fmt_data(ini.isoformat())} a {_fmt_data(fim.isoformat())}"
        if ini and fim else
        (f"até {_fmt_data(fim.isoformat())}" if fim else
         f"a partir de {_fmt_data(ini.isoformat())}" if ini else html.escape(texto))
    )
    # concurso suspenso não tem contagem regressiva — o prazo do edital
    # original não está correndo
    if ia.get("classe") == "suspensao":
        return f"🗓 inscrições no edital original: {periodo}"
    if fim and fim < hoje:
        return f"🗓 inscrições encerradas em {_fmt_data(fim.isoformat())}"
    if ini and ini > hoje:
        ate = f" (até {_fmt_data(fim.isoformat())})" if fim else ""
        return f"🗓 inscrições abrem em {_fmt_data(ini.isoformat())}{ate}"
    if fim:
        dias = (fim - hoje).days
        classe = "prazo-curto" if dias <= 7 else "prazo"
        return f'🗓 inscrições: <span class="{classe}">{periodo} · falta(m) {dias} dia(s)</span>'
    return f"🗓 inscrições: {periodo}"


def _render_item(item, hoje):
    det = item.get("detalhes") or {}
    ia = det.get("ia") or {}
    nome, cor = CATEGORIAS.get(item["categoria"], ("Outro", "#777"))

    # selos e etiquetas: categoria, suspensão, vagas reais do cargo
    tags = [f'<span class="badge" style="background:{cor}">{nome}</span>']
    if ia.get("classe") == "suspensao":
        tags.append('<span class="badge" style="background:#c0392b">⚠️ Suspenso</span>')
    vagas = (ia.get("vagas") or "").strip()
    if vagas:
        tags.append(f'<span class="chip">{html.escape(vagas)}</span>')
    elif ia.get("cadastro_reserva"):
        tags.append('<span class="chip">cadastro de reserva</span>')
    if ia.get("validade"):
        tags.append(f'<span class="chip">validade {html.escape(ia["validade"])}</span>')

    # título padronizado: "Município/UF — Cargo"; sem cargo extraído, cai no título original
    local = _local(item)
    cargo = titulo_cargo((ia.get("cargo") or "").strip())
    titulo = f"{local} — {cargo}" if local and cargo else item.get("titulo", "")

    meta = [m for m in (_linha_inscricoes(item, hoje),) if m]
    rodape = []
    if det.get("banca"):
        rodape.append(f"banca: {html.escape(det['banca'])}")
    rodape.append(f"fonte: {html.escape(item.get('fonte', ''))}")
    rodape.append(f"descoberto em {_fmt_data(item.get('descoberto_em', ''))}")
    if ia.get("prazo_atualizado_em"):
        rodape.append(f"prazo atualizado em {_fmt_data(ia['prazo_atualizado_em'])}")
    meta.append(" · ".join(rodape))

    resumo = ia.get("resumo") or det.get("trecho", "")
    bloco_resumo = f'<div class="trecho">{html.escape(resumo[:300])}</div>' if resumo else ""

    nota = ""
    if item["categoria"] == "conferir":
        nota = (
            f'<div class="nota">⚠️ Cargo genérico ({html.escape(cargo or "fiscal")}): '
            "o artigo não diz se a atribuição é tributária — conferir no edital.</div>"
        )

    links = []
    site = det.get("site_inscricao", "")
    if site and site != item.get("url"):
        links.append(f'<a href="{html.escape(site, quote=True)}">↗ site de inscrição</a>')
    if item.get("url", "").lower().endswith(".pdf"):
        links.append(f'<a href="{html.escape(item["url"], quote=True)}">edital (PDF)</a>')
    bloco_links = f'<div class="links">{"".join(links)}</div>' if links else ""

    metas = "".join(f'<div class="meta">{m}</div>' for m in meta)
    return (
        '<div class="item">'
        f'<div class="tags">{"".join(tags)}</div>'
        f'<a class="titulo" href="{html.escape(item.get("url", ""), quote=True)}">{html.escape(titulo)}</a>'
        f"{metas}{bloco_resumo}{nota}{bloco_links}"
        "</div>"
    )


def _ordem_vitrine(item, hoje):
    """Dentro do grupo: quem encerra primeiro no topo; sem prazo, mais
    recente primeiro."""
    det = item.get("detalhes") or {}
    fim = _data(det.get("inscricoes_fim"))
    if fim:
        return (0, fim.isoformat(), "")
    # inverte a data de descoberta para ordenar decrescente com chave única
    desc = item.get("descoberto_em", "")
    return (1, "", "".join(chr(255 - ord(c)) for c in desc))


def _bloco_pendentes(pendentes):
    itens = []
    for achado, _categoria, _termos, meta in pendentes:
        trecho = achado.detalhes.get("trecho", "")
        bloco_trecho = f'<div class="trecho">{html.escape(trecho[:300])}</div>' if trecho else ""
        itens.append(
            '<div class="item">'
            '<div class="tags"><span class="badge" style="background:#777">🔎 Sem veredito</span></div>'
            f'<a class="titulo" href="{html.escape(achado.url, quote=True)}">{html.escape(achado.titulo)}</a>'
            f'<div class="meta">fonte: {html.escape(achado.fonte)} · na fila desde '
            f"{_fmt_data(meta.get('enfileirado_em', ''))}</div>"
            f"{bloco_trecho}"
            "</div>"
        )
    n = len(pendentes)
    return (
        "<details>"
        f"<summary>🔎 {n} item(ns) aguardando confirmação — a triagem automática "
        "não decidiu; reavaliados a cada execução, expiram sozinhos</summary>"
        f'<div class="conteudo">{"".join(itens)}</div>'
        "</details>"
    )


def gerar(estado, cfg, caminho, hoje=None):
    hoje = hoje or tempo.hoje()
    limite = (hoje - dt.timedelta(days=cfg.get("painel_dias", 60))).isoformat()
    itens = [c for c in estado.concursos if c.get("descoberto_em", "") >= limite]

    situacao = {id(c): _status(c, hoje) for c in itens}
    vitrine = [c for c in itens if situacao[id(c)] in VITRINE]
    arquivo = [c for c in itens if situacao[id(c)] in ARQUIVO]
    em_curso = sum(1 for c in vitrine if situacao[id(c)] == "em_curso")
    futuros = sum(1 for c in vitrine if situacao[id(c)] == "futuro")

    blocos = []

    pendentes = estado.pendentes_carregados() if hasattr(estado, "pendentes_carregados") else []
    if pendentes:
        blocos.append(_bloco_pendentes(pendentes))

    grupos = (
        (0, "📍 Paraná"),
        (1, "🗺️ Vizinhos — SC · SP · MS"),
        (2, "🌎 Demais estados"),
    )
    for chave, rotulo in grupos:
        do_grupo = sorted(
            (c for c in vitrine if _grupo(c) == chave),
            key=lambda c: _ordem_vitrine(c, hoje),
        )
        if not do_grupo:
            continue
        blocos.append(f"<h2>{rotulo} ({len(do_grupo)})</h2>")
        blocos.extend(_render_item(c, hoje) for c in do_grupo)

    if not vitrine:
        blocos.append("<p>Nenhuma descoberta com inscrições em aberto no período.</p>")

    if arquivo:
        arquivo.sort(
            key=lambda c: (c.get("detalhes", {}).get("inscricoes_fim") or c.get("descoberto_em", "")),
            reverse=True,
        )
        blocos.append(
            "<details>"
            f"<summary>🗄 {len(arquivo)} concurso(s) com inscrições encerradas ou suspensos</summary>"
            f'<div class="conteudo">{"".join(_render_item(c, hoje) for c in arquivo)}</div>'
            "</details>"
        )

    sub = (
        "Concursos de fiscalização tributária e controle — federal, estadual e municipal. "
        f"Atualizado em {tempo.agora().strftime('%d.%m.%Y %H:%M')} · "
        f"{em_curso} com inscrições em curso · {futuros} com inscrições a abrir · "
        f"{len(itens)} descoberto(s) nos últimos {cfg.get('painel_dias', 60)} dias."
    )

    pagina = f"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Radar Auditor Fiscal</title>
<style>{CSS}</style>
</head>
<body>
<h1>📡 Radar Auditor Fiscal</h1>
<div class="sub">{sub}</div>
{"".join(blocos)}
</body>
</html>"""

    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(pagina, encoding="utf-8")
    (caminho.parent / ".nojekyll").touch()
