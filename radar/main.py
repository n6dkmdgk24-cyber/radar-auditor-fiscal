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

    candidatos = []
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
            continue
        candidatos.append((a, categoria, termos))

    # triagem FAIL-CLOSED: regras determinísticas + IA opcional (Anthropic).
    # Sem veredito, o item vai para data/pendentes.json — nunca para o painel.
    from . import classificador, triagem

    pendentes_antigos = estado.pendentes_carregados()
    resultado = triagem.triar(candidatos, pendentes_antigos, cfg)

    novos = []
    for a, categoria, termos, de_pendentes in resultado.publicar:
        if not de_pendentes and estado.ja_visto(a, categoria):
            continue
        estado.marcar(a, categoria)
        estado.registrar_concurso(a, categoria, termos)
        novos.append((a, categoria, termos))
    for a, veredito, _de_pendentes in resultado.descartar:
        estado.marcar(a, "descartado")
        estado.registrar_descartado(a, veredito)
    for a, categoria, _termos, _meta in resultado.pendentes:
        estado.marcar(a, categoria)  # evita que a coleta re-enfileire a mesma URL
    estado.definir_pendentes(resultado.pendentes)

    if resultado.descartar:
        print(f"[triagem] {len(resultado.descartar)} descartado(s) — data/descartados.json")
    if resultado.pendentes:
        print(f"[triagem] {len(resultado.pendentes)} pendente(s) na fila — data/pendentes.json")
    for linha in resultado.relatorio:
        print(f"[triagem] {linha['destino']:<32} regras={linha['regras']:<9} {linha['titulo'][:80]}")

    # avisos operacionais (não são achados): IA fora do ar / fila crescendo
    avisos = []
    if resultado.erros_ia:
        avisos.append(
            f"⚠️ Radar: classificador de IA falhou em {resultado.erros_ia} item(ns) "
            f"({resultado.ultimo_erro_ia}). Itens seguraram na fila de pendentes."
        )
    if resultado.novos_pendentes:
        motivo = "IA indisponível" if not classificador.disponivel() else "aguardando IA"
        avisos.append(
            f"🕐 Radar: {resultado.novos_pendentes} item(ns) novo(s) na fila de conferência "
            f"({motivo}); fila total: {len(resultado.pendentes)}."
        )

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
        ("telegram", lambda: s_telegram.enviar(novos, falhas, cfg)),
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
