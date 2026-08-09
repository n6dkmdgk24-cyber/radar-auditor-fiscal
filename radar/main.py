"""Orquestrador do radar.

Uso:
  python -m radar.main                     # execução normal (notifica e salva estado)
  python -m radar.main --dry-run           # imprime, não notifica nem salva
  python -m radar.main --backtest-dias 30  # coleta retroativa (implica dry-run)
  python -m radar.main --fontes qd,pci     # restringe as fontes desta execução
"""

import argparse
import datetime as dt
import importlib
import os
import sys
import traceback
from pathlib import Path

import yaml

from .estado import Estado
from .filtro import Filtro

BASE = Path(__file__).resolve().parent.parent


def main(argv=None):
    ap = argparse.ArgumentParser(description="Radar de concursos — auditor fiscal")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--backtest-dias", type=int, default=0)
    ap.add_argument("--fontes", default="")
    args = ap.parse_args(argv)
    dry = args.dry_run or args.backtest_dias > 0

    cfg = yaml.safe_load((BASE / "config.yaml").read_text(encoding="utf-8"))
    estado = Estado(BASE / "data")
    filtro = Filtro(cfg["termos"])

    dias = args.backtest_dias or cfg.get("janela_inicial_dias", 3)
    desde_padrao = dt.date.today() - dt.timedelta(days=dias)

    ativas = [f for f, on in cfg["fontes"].items() if on]
    if args.fontes:
        ativas = [f.strip() for f in args.fontes.split(",") if f.strip()]

    achados, falhas = [], []
    for fonte in ativas:
        # em dry-run/backtest o cursor é descartável, para não avançar o estado real
        cursor = {} if dry else estado.cursor(fonte)
        if args.backtest_dias:
            cursor.clear()
        try:
            mod = importlib.import_module(f"radar.coletores.{fonte}")
            res = mod.coletar(cfg, cursor, desde_padrao)
            achados.extend(res)
            print(f"[{fonte}] {len(res)} achados brutos")
        except Exception:
            falhas.append((fonte, traceback.format_exc(limit=3)))
            print(f"[{fonte}] FALHOU:\n{traceback.format_exc(limit=1)}", file=sys.stderr)

    from . import extrator
    from .regras import FONTES_DE_NOTICIA, manchete_de_prazo

    def _resolver_duplicata(a, categoria, termos):
        """Item cujo ente já tem cartão publicado. Devolve:
        "novo"       -> período que começa depois do prazo antigo: outro
                        certame ou reabertura; segue para a triagem;
        "atualizado" -> prorrogação do mesmo certame: o prazo entrou no cartão;
        "duplicata"  -> mesma coisa já publicada, nada a fazer.

        Notícia de fase posterior não mexe em prazo; diário oficial também
        não (o extrator só é confiável no artigo estereotipado de notícia).
        """
        if a.fonte not in FONTES_DE_NOTICIA or not manchete_de_prazo(a.titulo):
            return "duplicata"
        ex = extrator.extrair(a, termos)
        if not ex["inscricoes_fim"]:
            return "duplicata"
        a.detalhes["inscricoes_fim"] = ex["inscricoes_fim"]
        if ex["inscricoes_inicio"]:
            a.detalhes.setdefault("inscricoes_inicio", ex["inscricoes_inicio"])
        a.detalhes.setdefault("ia", {})["inscricoes"] = ex["inscricoes_texto"]
        if estado.atualizar_concurso(a, categoria):
            print(f"[triagem] prazo atualizado no cartão existente: {a.titulo[:70]}")
            return "atualizado"
        if estado.eh_certame_novo(a, categoria):
            print(f"[triagem] certame novo em ente já conhecido: {a.titulo[:70]}")
            return "novo"
        return "duplicata"

    candidatos, duplicatas, certames_novos = [], 0, set()
    for a in achados:
        # o trecho extraído do texto integral entra na classificação: o cargo
        # pode estar nele e não nos excerpts (caso real: Santos 17.7.2026)
        texto = f"{a.titulo}\n{a.cargo_texto}\n{a.detalhes.get('trecho', '')}"
        categoria, termos = filtro.classificar(texto)
        if not categoria:
            continue
        # coletor sinalizou coocorrência fraca (cargo e "concurso" em trechos
        # distintos do mesmo diário): não descarta, mas não ganha selo forte
        if categoria in ("tributario", "controle") and a.detalhes.get("contexto_fraco"):
            categoria = "conferir"
        if estado.ja_visto(a, categoria):
            if _resolver_duplicata(a, categoria, termos) != "novo":
                duplicatas += 1
                continue
            certames_novos.add(a.url)
        candidatos.append((a, categoria, termos))
    if duplicatas:
        print(f"[coleta] {duplicatas} item(ns) já conhecido(s) do mesmo ente")

    # triagem FAIL-CLOSED: regras determinísticas + verificação profunda do
    # texto integral (sem IA). Sem veredito, o item vai para
    # data/pendentes.json — nunca vira aviso de concurso.
    from . import triagem

    pendentes_antigos = estado.pendentes_carregados()
    resultado = triagem.triar(candidatos, pendentes_antigos, cfg)

    novos = []
    for a, categoria, termos, de_pendentes in resultado.publicar:
        # item da fila: a própria URL já está marcada desde o enfileiramento,
        # mas o CONCURSO pode ter sido publicado por outra fonte nesse meio
        # tempo — a chave do ente pega essa duplicata (caso Coronel Vivida
        # pci×cnb, 7.8.2026)
        # certame novo de ente conhecido já foi resolvido na coleta: a chave
        # de ente existe, mas o período começa depois do prazo antigo
        if a.url not in certames_novos:
            visto = (
                estado.ja_visto_ente(a, categoria) if de_pendentes
                else estado.ja_visto(a, categoria)
            )
            if visto:
                if estado.atualizar_concurso(a, categoria):
                    print(f"[triagem] prazo atualizado no cartão existente: {a.titulo[:70]}")
                continue
        estado.marcar(a, categoria)
        estado.registrar_concurso(a, categoria, termos)
        novos.append((a, categoria, termos))
    for a, veredito, _de_pendentes in resultado.descartar:
        estado.marcar(a, "descartado")
        estado.registrar_descartado(a, veredito)
    for a, _categoria, _termos, _meta in resultado.pendentes:
        estado.marcar_url(a)  # evita re-coleta da URL sem reservar o ente
    estado.definir_pendentes(resultado.pendentes)

    if resultado.descartar:
        print(f"[triagem] {len(resultado.descartar)} descartado(s) — data/descartados.json")
    if resultado.pendentes:
        print(f"[triagem] {len(resultado.pendentes)} pendente(s) na fila — data/pendentes.json")
    for linha in resultado.relatorio:
        print(f"[triagem] {linha['destino']:<32} regras={linha['regras']:<9} {linha['titulo'][:80]}")

    # avisos operacionais (não são achados): fila de pendentes e mudanças de
    # estado dos coletores (falha NOVA ou recuperação — sem repetir todo dia)
    avisos = []
    if resultado.novos_pendentes:
        avisos.append(
            f"🕐 Radar: {resultado.novos_pendentes} item(ns) novo(s) aguardando confirmação "
            f"no painel (fila total: {len(resultado.pendentes)})."
        )
    falhas_atuais = {f for f, _ in falhas}
    falhas_novas = falhas_atuais - estado.falhas_anteriores()
    recuperadas = (estado.falhas_anteriores() - falhas_atuais) & set(ativas)
    avisos.extend(f"✅ Radar: coletor {f} voltou a funcionar." for f in sorted(recuperadas))
    falhas_para_alertar = [(f, tb) for f, tb in falhas if f in falhas_novas]
    estado.registrar_falhas(falhas_atuais)

    print(f"\n== {len(novos)} novidade(s) | fontes com falha: {[f for f, _ in falhas] or 'nenhuma'} ==")
    for a, categoria, termos in novos:
        local = " / ".join(x for x in (a.municipio or a.orgao, a.uf) if x)
        print(f"- [{categoria}] {a.titulo} ({local or 'local n/d'}) <{a.url}> termos={termos}")

    if dry:
        for fonte, tb in falhas:
            print(f"\n--- traceback {fonte} ---\n{tb}", file=sys.stderr)
        return 0

    from .saidas import email as s_email
    from .saidas import painel as s_painel
    from .saidas import telegram as s_telegram

    erros_saida = []
    for nome, fn in (
        ("telegram", lambda: s_telegram.enviar(novos, falhas_para_alertar, cfg)),
        ("telegram-avisos", lambda: s_telegram.enviar_textos(avisos)),
        ("email", lambda: s_email.enviar(novos, falhas, cfg)),
        ("painel", lambda: s_painel.gerar(estado, cfg, BASE / "docs" / "index.html")),
        ("relatorio", lambda: estado.registrar_relatorio(resultado.relatorio)),
    ):
        try:
            fn()
        except Exception:
            erros_saida.append((nome, traceback.format_exc(limit=3)))
            print(f"[saida:{nome}] FALHOU", file=sys.stderr)

    estado.expirar(cfg.get("expira_vistos_dias", 180))
    estado.salvar()

    for nome, tb in falhas + erros_saida:
        print(f"\n--- traceback {nome} ---\n{tb}", file=sys.stderr)
    # falha total de coleta = erro do run (para o Actions acusar)
    return 1 if ativas and len(falhas) == len(ativas) else 0


if __name__ == "__main__":
    sys.exit(main())
