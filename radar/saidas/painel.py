"""Gera o painel estático (docs/index.html), publicado via GitHub Pages.

Desenho (feedback do Danilo, 6 e 12.8.2026):

- ORDEM GEOGRÁFICA por distância REAL de Maringá, não por sigla de UF: o
  primeiro bloco é o raio de 40 km (mais um concurso de prova remota, que
  serve de qualquer lugar), depois o resto do Paraná, depois os vizinhos
  (SP/SC/MS) e por fim os demais — sempre do mais perto para o mais longe.
- DUAS COLUNAS em tela larga, uma no celular. A lista é de varredura, não de
  leitura corrida: o olho precisa varrer muitos cartões e parar no que
  interessa.
- HIERARQUIA DENTRO DO CARTÃO, que é o que resolvia a "bagunça": a linha de
  etiquetas diz o QUE é (área, status, vagas, distância), o título diz ONDE,
  a remuneração fica em destaque à direita, o prazo tem contagem regressiva
  e o resto (resumo, banca, links) vem em corpo menor.
- SEÇÕES RECOLHÍVEIS (details/summary), como a fila de pendentes já era.
- OCULTAR CARTÃO: o que não interessa some da vitrine com um clique e fica
  contabilizado num botão de "mostrar ocultos". Estado no localStorage do
  navegador dela — o painel é estático, não há login nem servidor.
- NOVIDADES: cartão descoberto depois da última visita ganha selo e borda
  destacada; a lista de vistos também mora no localStorage.

Nada disso muda a regra do produto: o cartão só mostra o que o artigo
sustenta. Campo sem dado simplesmente não aparece.
"""

import datetime as dt
import hashlib
import html

from .. import geo, tempo
from ..extrator import titulo_cargo

# Área do cargo -> (rótulo do selo, cor). O feedback pediu separação melhor
# que "Tributário/Controle/Conferir": esfera e natureza ficam explícitas.
AREAS = {
    "tributario": ("Tributário", "#0a6"),
    "controle": ("Controle", "#06b"),
    "conferir": ("Conferir área", "#b70"),
}

# distância a partir da qual o número deixa de ajudar (a UF já diz tudo)
_DISTANCIA_MAX_EXIBIDA = 400

CSS = """
:root {
  color-scheme: light dark;
  --fundo: #f6f7f9; --papel: #fff; --borda: #e3e5e9; --texto: #1c1f24;
  --suave: #5d646e; --tenue: #878e98; --acento: #0a66c2;
  --verde: #0a7d55; --vermelho: #b3261e; --ambar: #8a5a00; --novo: #6d28d9;
}
@media (prefers-color-scheme: dark) {
  :root {
    --fundo: #14161a; --papel: #1c1f25; --borda: #2f333a; --texto: #e8eaed;
    --suave: #a2a9b3; --tenue: #7e8590; --acento: #58a6ff;
    --verde: #4cc38a; --vermelho: #ff7b72; --ambar: #d9a53f; --novo: #a78bfa;
  }
}
* { box-sizing: border-box; }
body { font-family: -apple-system, 'Segoe UI', Roboto, Arial, sans-serif;
       margin: 0 auto; max-width: 1180px; padding: 20px 16px 60px;
       line-height: 1.45; background: var(--fundo); color: var(--texto); }
h1 { font-size: 1.45rem; margin: 0 0 4px; }
.sub { color: var(--suave); font-size: .88rem; margin-bottom: 14px; }
.sub b { color: var(--texto); }

/* barra de controles */
.controles { display: flex; flex-wrap: wrap; gap: 8px; align-items: center;
             margin: 0 0 18px; }
.filtro { border: 1px solid var(--borda); background: var(--papel);
          color: var(--suave); border-radius: 999px; padding: 5px 12px;
          font-size: .8rem; font-weight: 600; cursor: pointer; }
.filtro[aria-pressed="true"] { background: var(--acento); border-color: var(--acento);
                               color: #fff; }
.filtro:hover { border-color: var(--acento); }

/* seções */
section { margin: 0 0 10px; }
section > summary { cursor: pointer; list-style: none; padding: 11px 14px;
  background: var(--papel); border: 1px solid var(--borda); border-radius: 10px;
  font-weight: 650; font-size: .95rem; display: flex; align-items: center; gap: 8px; }
section > summary::-webkit-details-marker { display: none; }
section > summary::before { content: "▸"; color: var(--tenue); font-size: .8rem; }
section[open] > summary::before { content: "▾"; }
section > summary .conta { color: var(--tenue); font-weight: 500; font-size: .85rem; }
section > summary .dica { color: var(--tenue); font-weight: 400; font-size: .78rem;
                          margin-left: auto; }
.grade { display: grid; gap: 12px; margin: 12px 0 22px;
         grid-template-columns: repeat(auto-fill, minmax(330px, 1fr)); }

/* cartão */
.item { background: var(--papel); border: 1px solid var(--borda);
        border-radius: 12px; padding: 13px 15px 12px; position: relative;
        display: flex; flex-direction: column; }
.item.novo { border-color: var(--novo); box-shadow: 0 0 0 1px var(--novo) inset; }
.topo { display: flex; align-items: flex-start; gap: 10px; }
.topo-esq { flex: 1; min-width: 0; }
.tags { display: flex; flex-wrap: wrap; gap: 5px; margin-bottom: 7px; }
.badge { display: inline-block; color: #fff; border-radius: 6px; font-size: .7rem;
         font-weight: 700; padding: 2px 7px; letter-spacing: .01em; }
.chip { display: inline-block; color: var(--suave); border: 1px solid var(--borda);
        border-radius: 6px; font-size: .7rem; font-weight: 600; padding: 1px 7px; }
.chip.forte { color: var(--texto); border-color: var(--tenue); }
a.titulo { color: inherit; text-decoration: none; font-weight: 650; font-size: .98rem;
           display: block; }
a.titulo:hover { text-decoration: underline; }
.salario { text-align: right; font-weight: 700; font-size: .95rem; white-space: nowrap; }
.salario small { display: block; font-weight: 500; font-size: .68rem; color: var(--tenue); }
.prazo { font-size: .84rem; margin-top: 7px; font-weight: 600; }
.prazo.aberto { color: var(--verde); }
.prazo.urgente { color: var(--vermelho); }
.prazo.futuro { color: var(--acento); }
.prazo.frio { color: var(--suave); font-weight: 500; }
.resumo { color: var(--suave); font-size: .82rem; margin-top: 7px;
          display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical;
          overflow: hidden; }
.nota { color: var(--ambar); font-size: .78rem; margin-top: 7px; }
.rodape { display: flex; flex-wrap: wrap; gap: 10px; align-items: center;
          margin-top: 10px; padding-top: 9px; border-top: 1px solid var(--borda);
          font-size: .78rem; color: var(--tenue); }
.rodape a { color: var(--acento); text-decoration: none; font-weight: 600; }
.rodape a:hover { text-decoration: underline; }
.ocultar { margin-left: auto; background: none; border: none; color: var(--tenue);
           font-size: .78rem; cursor: pointer; padding: 2px 4px; }
.ocultar:hover { color: var(--vermelho); }
.item[data-oculto="1"] { display: none; }
body.ver-ocultos .item[data-oculto="1"] { display: flex; opacity: .55; }
body.so-vaga .item[data-cr="1"],
body.so-abertas .item:not([data-situacao="em_curso"]),
body.so-perto .item:not([data-faixa="raio"]) { display: none; }
.vazio { color: var(--tenue); font-size: .85rem; padding: 6px 2px 18px; }
footer { color: var(--tenue); font-size: .78rem; margin-top: 26px;
         border-top: 1px solid var(--borda); padding-top: 12px; }
@media (max-width: 620px) {
  .grade { grid-template-columns: 1fr; }
  .salario { font-size: .85rem; }
}
"""

JS = """
(function () {
  var CHAVE_OCULTOS = 'radar:ocultos', CHAVE_VISTOS = 'radar:vistos', CHAVE_FILTROS = 'radar:filtros';
  function ler(chave) {
    try { return JSON.parse(localStorage.getItem(chave)) || []; } catch (e) { return []; }
  }
  function gravar(chave, valor) {
    try { localStorage.setItem(chave, JSON.stringify(valor)); } catch (e) {}
  }

  // --- ocultos ---------------------------------------------------------
  var ocultos = ler(CHAVE_OCULTOS);
  function aplicarOcultos() {
    var n = 0;
    document.querySelectorAll('.item[data-id]').forEach(function (el) {
      var esconder = ocultos.indexOf(el.dataset.id) !== -1;
      el.dataset.oculto = esconder ? '1' : '0';
      if (esconder) n++;
    });
    var botao = document.getElementById('btn-ocultos');
    botao.hidden = n === 0;
    botao.textContent = (document.body.classList.contains('ver-ocultos') ? 'esconder de novo' : 'mostrar ocultos') + ' (' + n + ')';
    document.querySelectorAll('section[data-secao]').forEach(function (sec) {
      var visiveis = sec.querySelectorAll('.item[data-oculto="0"]').length;
      var conta = sec.querySelector('.conta');
      if (conta) conta.textContent = visiveis + (visiveis === 1 ? ' concurso' : ' concursos');
      sec.hidden = visiveis === 0 && !document.body.classList.contains('ver-ocultos');
    });
  }
  document.addEventListener('click', function (ev) {
    var botao = ev.target.closest('.ocultar');
    if (!botao) return;
    var id = botao.closest('.item').dataset.id;
    var i = ocultos.indexOf(id);
    if (i === -1) { ocultos.push(id); } else { ocultos.splice(i, 1); }
    gravar(CHAVE_OCULTOS, ocultos);
    aplicarOcultos();
  });
  document.getElementById('btn-ocultos').addEventListener('click', function () {
    document.body.classList.toggle('ver-ocultos');
    aplicarOcultos();
  });

  // --- novidades desde a última visita ---------------------------------
  // guarda os ids já vistos (mais confiável que carimbo de tempo: um cartão
  // que muda de prazo continua sendo o mesmo cartão)
  var vistos = ler(CHAVE_VISTOS), primeiraVisita = vistos.length === 0, novos = 0;
  document.querySelectorAll('.item[data-id]').forEach(function (el) {
    if (!primeiraVisita && vistos.indexOf(el.dataset.id) === -1) {
      el.classList.add('novo');
      var tags = el.querySelector('.tags');
      var selo = document.createElement('span');
      selo.className = 'badge';
      selo.style.background = 'var(--novo)';
      selo.textContent = 'novo';
      tags.insertBefore(selo, tags.firstChild);
      novos++;
    }
  });
  var aviso = document.getElementById('aviso-novos');
  if (novos > 0) {
    aviso.hidden = false;
    aviso.textContent = novos === 1 ? '1 concurso novo desde a sua última visita.'
                                    : novos + ' concursos novos desde a sua última visita.';
  }
  // só registra como visto ao sair, para o selo sobreviver a um F5 acidental
  window.addEventListener('pagehide', function () {
    var ids = [];
    document.querySelectorAll('.item[data-id]').forEach(function (el) { ids.push(el.dataset.id); });
    gravar(CHAVE_VISTOS, ids);
  });

  // --- filtros ---------------------------------------------------------
  var filtros = ler(CHAVE_FILTROS);
  document.querySelectorAll('.filtro').forEach(function (botao) {
    var classe = botao.dataset.classe;
    if (filtros.indexOf(classe) !== -1) {
      document.body.classList.add(classe);
      botao.setAttribute('aria-pressed', 'true');
    }
    botao.addEventListener('click', function () {
      var ligado = document.body.classList.toggle(classe);
      botao.setAttribute('aria-pressed', ligado ? 'true' : 'false');
      filtros = filtros.filter(function (c) { return c !== classe; });
      if (ligado) filtros.push(classe);
      gravar(CHAVE_FILTROS, filtros);
      aplicarOcultos();
    });
  });

  aplicarOcultos();
})();
"""


def _data(iso):
    try:
        return dt.date.fromisoformat(iso or "")
    except (ValueError, TypeError):
        return None


def _fmt_data(iso):
    d = _data(iso)
    return f"{d.day}.{d.month}.{d.year}" if d else (iso or "")


def _id(item):
    return hashlib.sha1(item.get("url", "").encode()).hexdigest()[:12]


def _local(item):
    lugar = item.get("municipio") or item.get("orgao") or ""
    if not lugar:
        return ""
    uf = item.get("uf") or ""
    return f"{lugar}/{uf}" if uf and uf not in lugar else lugar


def _situacao(item, hoje):
    """em_curso | futuro | encerrado | suspenso | sem_prazo."""
    det = item.get("detalhes") or {}
    ia = det.get("ia") or {}
    if ia.get("classe") == "suspensao":
        return "suspenso"
    if ia.get("classe") == "pre_edital":
        return "pre_edital"
    fim, inicio = _data(det.get("inscricoes_fim")), _data(det.get("inscricoes_inicio"))
    if fim:
        if fim < hoje:
            return "encerrado"
        return "futuro" if inicio and inicio > hoje else "em_curso"
    return "sem_prazo"


VITRINE = ("em_curso", "futuro", "sem_prazo")
ARQUIVO = ("encerrado", "suspenso")
# pré-edital tem bloco próprio: interessa para começar a estudar, mas não há
# o que se inscrever ainda (inspiração da newsletter que o Danilo assina)
PRE_EDITAL = ("pre_edital",)


def _faixa(item):
    """Bloco geográfico do cartão. Prova remota entra no bloco do raio: serve
    de qualquer cidade, então é tão boa quanto um concurso ao lado de casa."""
    if ((item.get("detalhes") or {}).get("ia") or {}).get("prova_remota"):
        return "raio"
    return geo.faixa(item.get("municipio") or item.get("orgao", ""), item.get("uf", ""))


def _distancia(item):
    return geo.distancia_km(item.get("municipio") or item.get("orgao", ""), item.get("uf", ""))


def _ordem(item, hoje):
    """Dentro do bloco: primeiro quem tem inscrição correndo, depois quem vai
    abrir, e dentro disso o mais perto de Maringá; empate desempata pelo
    prazo mais curto."""
    situacao = _situacao(item, hoje)
    peso = {"em_curso": 0, "futuro": 1, "sem_prazo": 2}.get(situacao, 3)
    dist = _distancia(item)
    fim = _data((item.get("detalhes") or {}).get("inscricoes_fim"))
    return (
        peso,
        dist if dist is not None else 99999,
        fim.isoformat() if fim else "9999",
        item.get("municipio") or "",
    )


def _linha_prazo(item, hoje):
    det = item.get("detalhes") or {}
    ia = det.get("ia") or {}
    ini, fim = _data(det.get("inscricoes_inicio")), _data(det.get("inscricoes_fim"))
    texto = ia.get("inscricoes", "")
    if ia.get("classe") == "suspensao":
        base = f"edital original: {html.escape(texto)}" if texto else "concurso suspenso"
        return f'<div class="prazo frio">⚠️ {base}</div>'
    if not (ini or fim or texto):
        return ""
    periodo = (
        f"{_fmt_data(ini.isoformat())} a {_fmt_data(fim.isoformat())}" if ini and fim
        else f"até {_fmt_data(fim.isoformat())}" if fim
        else f"a partir de {_fmt_data(ini.isoformat())}" if ini
        else html.escape(texto)
    )
    if fim and fim < hoje:
        return f'<div class="prazo frio">encerrado em {_fmt_data(fim.isoformat())}</div>'
    if ini and ini > hoje:
        dias = (ini - hoje).days
        quando = "amanhã" if dias == 1 else f"em {dias} dias"
        return f'<div class="prazo futuro">🗓 inscrições abrem {quando} · {periodo}</div>'
    if fim:
        dias = (fim - hoje).days
        classe = "urgente" if dias <= 7 else "aberto"
        resta = "último dia" if dias == 0 else ("falta 1 dia" if dias == 1 else f"faltam {dias} dias")
        return f'<div class="prazo {classe}">🗓 {resta} · {periodo}</div>'
    return f'<div class="prazo aberto">🗓 {periodo}</div>'


def _selos(item, hoje, situacao):
    det = item.get("detalhes") or {}
    ia = det.get("ia") or {}
    nome, cor = AREAS.get(item["categoria"], ("Outro", "#777"))
    esfera = ia.get("esfera") or ""
    if esfera and item["categoria"] != "conferir":
        nome = f"{nome} {esfera}"
    selos = [f'<span class="badge" style="background:{cor}">{html.escape(nome)}</span>']
    if situacao == "suspenso":
        selos.append('<span class="badge" style="background:var(--vermelho)">Suspenso</span>')
    elif situacao == "pre_edital":
        selos.append('<span class="badge" style="background:var(--ambar)">Sem edital ainda</span>')

    vagas = (ia.get("vagas") or "").strip()
    if vagas:
        classe = "chip" if ia.get("cadastro_reserva") else "chip forte"
        selos.append(f'<span class="{classe}">{html.escape(vagas)}</span>')
    elif ia.get("cadastro_reserva"):
        selos.append('<span class="chip">cadastro de reserva</span>')

    if ia.get("prova_remota"):
        selos.append('<span class="chip forte">prova remota</span>')
    else:
        dist = _distancia(item)
        if dist is not None and dist <= _DISTANCIA_MAX_EXIBIDA:
            selos.append(f'<span class="chip">{round(dist)} km de Maringá</span>')

    if ia.get("validade"):
        selos.append(f'<span class="chip">validade {html.escape(ia["validade"])}</span>')
    return "".join(selos)


def _links(item):
    det = item.get("detalhes") or {}
    partes = []
    edital = det.get("edital_url", "")
    if edital:
        partes.append(f'<a href="{html.escape(edital, quote=True)}">📄 edital</a>')
    site = det.get("site_inscricao", "")
    if site and site != item.get("url"):
        partes.append(f'<a href="{html.escape(site, quote=True)}">↗ inscrição</a>')
    url = item.get("url", "")
    if url:
        rotulo = "notícia" if item.get("fonte") in ("pci", "cnb", "estrategia", "gran") else "fonte"
        partes.append(f'<a href="{html.escape(url, quote=True)}">↗ {rotulo}</a>')
    return " ".join(partes)


def _cartao(item, hoje):
    det = item.get("detalhes") or {}
    ia = det.get("ia") or {}
    situacao = _situacao(item, hoje)

    local = _local(item)
    cargo = titulo_cargo((ia.get("cargo") or "").strip())
    titulo = f"{local} — {cargo}" if local and cargo else item.get("titulo", "")

    remuneracao = ia.get("remuneracao") or ""
    bloco_salario = ""
    if remuneracao:
        rotulo = "remuneração" if not remuneracao.lower().startswith("até") else "até"
        valor = remuneracao[3:].strip() if remuneracao.lower().startswith("até") else remuneracao
        bloco_salario = (
            f'<div class="salario">{html.escape(valor)}<small>{rotulo}</small></div>'
        )

    resumo = ia.get("resumo") or det.get("trecho", "")
    bloco_resumo = f'<div class="resumo">{html.escape(resumo[:320])}</div>' if resumo else ""

    nota = ""
    if item["categoria"] == "conferir":
        nota = (
            f'<div class="nota">⚠️ Cargo genérico ({html.escape(cargo or "fiscal")}): '
            "o artigo não diz se a atribuição é tributária — conferir no edital.</div>"
        )

    rodape = []
    if det.get("banca"):
        rodape.append(f"banca {html.escape(det['banca'])}")
    rodape.append(f"{html.escape(item.get('fonte', ''))} · {_fmt_data(item.get('descoberto_em', ''))}")

    return (
        f'<article class="item" data-id="{_id(item)}" data-situacao="{situacao}" '
        f'data-faixa="{_faixa(item)}" data-cr="{1 if ia.get("cadastro_reserva") and not ia.get("vagas") else 0}">'
        '<div class="topo"><div class="topo-esq">'
        f'<div class="tags">{_selos(item, hoje, situacao)}</div>'
        f'<a class="titulo" href="{html.escape(item.get("url", ""), quote=True)}">{html.escape(titulo)}</a>'
        "</div>"
        f"{bloco_salario}</div>"
        f"{_linha_prazo(item, hoje)}{bloco_resumo}{nota}"
        f'<div class="rodape">{_links(item)}'
        f'<span>{" · ".join(rodape)}</span>'
        '<button class="ocultar" title="ocultar este concurso">ocultar</button>'
        "</div></article>"
    )


def _secao(chave, titulo, dica, itens, hoje, aberta=True):
    if not itens:
        return ""
    cartoes = "".join(_cartao(c, hoje) for c in itens)
    plural = "concurso" if len(itens) == 1 else "concursos"
    return (
        f'<details class="secao" data-secao="{chave}"{" open" if aberta else ""}><summary>'
        f"<span>{titulo}</span>"
        f'<span class="conta">{len(itens)} {plural}</span>'
        + (f'<span class="dica">{dica}</span>' if dica else "")
        + f'</summary><div class="grade">{cartoes}</div></details>'
    )


def _secao_pendentes(pendentes):
    if not pendentes:
        return ""
    itens = []
    for achado, _categoria, _termos, meta in pendentes:
        trecho = achado.detalhes.get("trecho", "")
        bloco = f'<div class="resumo">{html.escape(trecho[:300])}</div>' if trecho else ""
        itens.append(
            '<article class="item">'
            '<div class="tags"><span class="badge" style="background:#777">Sem veredito</span></div>'
            f'<a class="titulo" href="{html.escape(achado.url, quote=True)}">{html.escape(achado.titulo)}</a>'
            f"{bloco}"
            f'<div class="rodape"><span>{html.escape(achado.fonte)} · na fila desde '
            f"{_fmt_data(meta.get('enfileirado_em', ''))}</span></div></article>"
        )
    plural = "item" if len(itens) == 1 else "itens"
    return (
        '<details class="secao"><summary><span>🔎 Aguardando confirmação</span>'
        f'<span class="conta">{len(itens)} {plural}</span>'
        '<span class="dica">a triagem não decidiu; expiram sozinhos</span></summary>'
        f'<div class="grade">{"".join(itens)}</div></details>'
    )


def gerar(estado, cfg, caminho, hoje=None):
    hoje = hoje or tempo.hoje()
    limite = (hoje - dt.timedelta(days=cfg.get("painel_dias", 60))).isoformat()
    itens = [c for c in estado.concursos if c.get("descoberto_em", "") >= limite]

    situacoes = {id(c): _situacao(c, hoje) for c in itens}
    vitrine = [c for c in itens if situacoes[id(c)] in VITRINE]
    arquivo = [c for c in itens if situacoes[id(c)] in ARQUIVO]
    pre_edital = [c for c in itens if situacoes[id(c)] in PRE_EDITAL]
    pre_edital.sort(key=lambda c: _ordem(c, hoje))
    em_curso = sum(1 for c in vitrine if situacoes[id(c)] == "em_curso")
    futuros = sum(1 for c in vitrine if situacoes[id(c)] == "futuro")

    blocos = {"raio": [], "pr": [], "vizinho": [], "distante": [], "desconhecido": []}
    for c in vitrine:
        blocos[_faixa(c)].append(c)
    for lista in blocos.values():
        lista.sort(key=lambda c: _ordem(c, hoje))

    pendentes = estado.pendentes_carregados() if hasattr(estado, "pendentes_carregados") else []
    partes = [
        # a fila fica no TOPO por pedido do Danilo (6.8.2026), recolhida
        _secao_pendentes(pendentes),
        _secao("raio", "🎯 Perto de Maringá", "até 40 km, mais os de prova remota",
               blocos["raio"], hoje),
        _secao("pr", "📍 Paraná", "demais cidades do estado", blocos["pr"], hoje),
        _secao("vizinho", "🗺️ Estados vizinhos", "SP · SC · MS, do mais perto ao mais longe",
               blocos["vizinho"], hoje),
        _secao("distante", "🌎 Demais estados", "", blocos["distante"], hoje),
    ]
    if blocos["desconhecido"]:
        partes.append(
            _secao("desconhecido", "❔ Sem localização identificada",
                   "o coletor não conseguiu o município", blocos["desconhecido"], hoje)
        )
    if not vitrine:
        partes.append('<p class="vazio">Nenhuma descoberta com inscrições em aberto no período.</p>')

    arquivo.sort(
        key=lambda c: (c.get("detalhes", {}).get("inscricoes_fim") or c.get("descoberto_em", "")),
        reverse=True,
    )
    partes.append(
        _secao("pre_edital", "⏳ No radar, sem edital ainda",
               "banca definida, comissão formada ou edital anunciado", pre_edital, hoje,
               aberta=False)
    )
    partes.append(
        _secao("arquivo", "🗄 Encerrados e suspensos", "histórico recente", arquivo, hoje, aberta=False)
    )

    sub = (
        f"<b>{em_curso}</b> com inscrições em curso · <b>{futuros}</b> vão abrir · "
        f"{len(itens)} descobertos nos últimos {cfg.get('painel_dias', 60)} dias"
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
<div class="sub" id="aviso-novos" hidden></div>
<div class="controles">
  <button class="filtro" data-classe="so-abertas" aria-pressed="false">inscrições abertas</button>
  <button class="filtro" data-classe="so-vaga" aria-pressed="false">com vaga imediata</button>
  <button class="filtro" data-classe="so-perto" aria-pressed="false">perto de Maringá</button>
  <button class="filtro" id="btn-ocultos" hidden></button>
</div>
{"".join(p for p in partes if p)}
<footer>
Concursos de fiscalização tributária e controle — federal, estadual e municipal.
Atualizado em {tempo.agora().strftime("%d.%m.%Y às %H:%M")}.
Ocultos e novidades ficam guardados neste navegador.
</footer>
<script>{JS}</script>
</body>
</html>"""

    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(pagina, encoding="utf-8")
    (caminho.parent / ".nojekyll").touch()
