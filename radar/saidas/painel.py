"""Gera o painel estático (docs/index.html), publicado via GitHub Pages."""

import datetime as dt
import html
import time

CATEGORIAS = {
    "tributario": ("Tributário", "#0a6"),
    "controle": ("Controle", "#06b"),
    "conferir": ("Conferir", "#b70"),
}

CSS = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body { font-family: -apple-system, 'Segoe UI', Roboto, Arial, sans-serif;
       margin: 0 auto; max-width: 860px; padding: 24px 16px; line-height: 1.5;
       background: #fafafa; color: #1c1c1c; }
h1 { font-size: 1.5rem; margin-bottom: 4px; }
.sub { color: #666; font-size: .9rem; margin-bottom: 24px; }
.item { background: #fff; border: 1px solid #e3e3e3; border-radius: 10px;
        padding: 14px 16px; margin-bottom: 12px; }
.item a { color: inherit; text-decoration: none; font-weight: 600; }
.item a:hover { text-decoration: underline; }
.meta { color: #666; font-size: .85rem; margin-top: 6px; }
.trecho { color: #555; font-size: .85rem; margin-top: 6px; font-style: italic; }
.badge { display: inline-block; color: #fff; border-radius: 6px;
         font-size: .75rem; padding: 2px 8px; margin-right: 8px; }
.prazo { font-weight: 700; color: #b00; }
h2 { font-size: 1.1rem; margin: 28px 0 12px; }
@media (prefers-color-scheme: dark) {
  body { background: #16181c; color: #e6e6e6; }
  .item { background: #1f2228; border-color: #33363d; }
  .sub, .meta { color: #9aa0a8; }
  .trecho { color: #8a9099; }
  .prazo { color: #ff7b72; }
}
"""


def _prazo(item):
    fim = (item.get("detalhes") or {}).get("inscricoes_fim", "")
    try:
        return dt.date.fromisoformat(fim)
    except (ValueError, TypeError):
        return None


def _render_item(item, hoje):
    nome, cor = CATEGORIAS.get(item["categoria"], ("Outro", "#777"))
    local = " / ".join(x for x in (item.get("municipio") or item.get("orgao"), item.get("uf")) if x)
    prazo = _prazo(item)
    linhas_meta = [
        f'<span class="badge" style="background:{cor}">{nome}</span>'
        f"{html.escape(local) if local else 'local n/d'}"
    ]
    if prazo and prazo >= hoje:
        dias = (prazo - hoje).days
        linhas_meta.append(
            f'<span class="prazo">inscrições até {prazo.strftime("%d.%m.%Y")} ({dias} dia(s))</span>'
        )
    if (item.get("detalhes") or {}).get("possivel_abertura"):
        linhas_meta.append("🔎 possível abertura — conferir no diário")
    linhas_meta.append(
        f"descoberto em {html.escape(item.get('descoberto_em', ''))} · fonte {html.escape(item.get('fonte', ''))}"
    )
    trecho = (item.get("detalhes") or {}).get("trecho", "")
    bloco_trecho = f'<div class="trecho">{html.escape(trecho)}</div>' if trecho else ""
    return (
        '<div class="item">'
        f'<a href="{html.escape(item.get("url", ""), quote=True)}">{html.escape(item.get("titulo", ""))}</a>'
        f'<div class="meta">{" · ".join(linhas_meta)}</div>'
        f"{bloco_trecho}"
        "</div>"
    )


def gerar(estado, cfg, caminho):
    hoje = dt.date.today()
    limite = (hoje - dt.timedelta(days=cfg.get("painel_dias", 60))).isoformat()
    itens = [c for c in estado.concursos if c.get("descoberto_em", "") >= limite]

    com_prazo = sorted(
        (c for c in itens if _prazo(c) and _prazo(c) >= hoje), key=_prazo
    )
    demais = sorted(
        (c for c in itens if c not in com_prazo),
        key=lambda c: c.get("descoberto_em", ""),
        reverse=True,
    )

    blocos = []
    if com_prazo:
        blocos.append("<h2>Com inscrição aberta (por prazo)</h2>")
        blocos.extend(_render_item(c, hoje) for c in com_prazo)
    blocos.append("<h2>Descobertas recentes</h2>" if com_prazo else "")
    blocos.extend(_render_item(c, hoje) for c in demais)
    if not itens:
        blocos.append("<p>Nenhuma descoberta no período.</p>")

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
<div class="sub">Concursos de fiscalização tributária e controle — federal, estadual e municipal.
Atualizado em {time.strftime("%d.%m.%Y %H:%M")} · {len(itens)} item(ns) nos últimos {cfg.get("painel_dias", 60)} dias.</div>
{"".join(blocos)}
</body>
</html>"""

    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(pagina, encoding="utf-8")
    (caminho.parent / ".nojekyll").touch()
