"""Triagem dos candidatos: regras determinísticas + IA opcional, FAIL-CLOSED.

Fluxo por item (novos da coleta + fila de pendentes de execuções anteriores):

1. radar/regras.py dá um veredito determinístico.
   - "descarte" encerra ali (sem gastar IA) — registro em descartados.json.
2. Para "abertura", "suspensao" e "incerto", a IA (se configurada) é o
   árbitro final, com a régua de produto de sempre: só "abertura" na
   área-alvo COM cargo literal gera aviso; "suspensao" entra com ⚠️;
   andamento/irrelevante viram descarte auditável.
3. SEM IA (não configurada ou com erro):
   - regra "abertura"/"suspensao" publica com a evidência das regras;
   - regra "incerto" vai para a FILA DE PENDENTES (data/pendentes.json) e
     é re-tentado a cada execução até a IA voltar ou expirar.

Nada sem veredito chega ao painel ou ao Telegram. Este é o conserto do
desastre de 30.7–4.8.2026, quando o fail-open publicou lixo de diário
oficial por cinco dias após a aposentadoria do GitHub Models.
"""

import datetime as dt
from dataclasses import dataclass, field

from . import classificador, regras

# depois de N erros seguidos de IA, para de tentar nesta execução
_LIMITE_ERROS_SEGUIDOS = 3


@dataclass
class Resultado:
    # (achado, categoria_final, termos, de_pendentes)
    publicar: list = field(default_factory=list)
    # (achado, veredito_dict, de_pendentes)
    descartar: list = field(default_factory=list)
    # (achado, categoria, termos, meta) — meta = {"enfileirado_em", "tentativas"}
    pendentes: list = field(default_factory=list)
    # um dict por item processado — vira data/relatorio.json e log do Actions
    relatorio: list = field(default_factory=list)
    novos_pendentes: int = 0
    expirados: int = 0
    erros_ia: int = 0
    ultimo_erro_ia: str = ""


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


def _veredito_regras(classe, achado, categoria, termos, motivo):
    texto = f"{achado.titulo} {achado.cargo_texto} {achado.detalhes.get('trecho', '')}".lower()
    return {
        "classe": classe,
        "area": categoria if categoria in ("tributario", "controle") else "outra",
        "cargo": termos[0] if termos else "",
        "inscricoes": "",
        "cadastro_reserva": "cadastro de reserva" in texto or "cadastro reserva" in texto,
        "resumo": motivo,
        "origem": "regras",
    }


def triar(candidatos, pendentes_antigos, cfg):
    """candidatos: [(achado, categoria, termos)] novos desta coleta.
    pendentes_antigos: [(achado, categoria, termos, meta)] da fila persistida.
    """
    r = Resultado()
    hoje = dt.date.today()
    expira_dias = cfg.get("pendentes_expira_dias", 30)
    ia_ativa = classificador.disponivel()
    erros_seguidos = 0

    fila = [(a, c, t, None) for a, c, t in candidatos]
    fila += [(a, c, t, m) for a, c, t, m in pendentes_antigos]

    for achado, categoria, termos, meta in fila:
        de_pendentes = meta is not None
        veredito_r, motivo = regras.triar(achado, categoria, termos)
        linha = {
            "fonte": achado.fonte,
            "titulo": achado.titulo[:160],
            "url": achado.url,
            "categoria_filtro": categoria,
            "termos": termos,
            "regras": veredito_r,
            "regras_motivo": motivo,
            "ia": None,
            "destino": None,
        }

        if veredito_r == "descarte":
            classe = "irrelevante" if "documento" in motivo else "andamento"
            r.descartar.append(
                (achado, _veredito_regras(classe, achado, categoria, termos, motivo), de_pendentes)
            )
            linha["destino"] = "descartado (regras)"
            r.relatorio.append(linha)
            continue

        # IA como árbitro final (com disjuntor após erros seguidos)
        veredito_ia = None
        if ia_ativa and erros_seguidos < _LIMITE_ERROS_SEGUIDOS:
            try:
                veredito_ia = classificador.classificar(_texto_item(achado, termos))
                erros_seguidos = 0
            except Exception as e:  # noqa: BLE001 — qualquer erro => fail-closed
                erros_seguidos += 1
                r.erros_ia += 1
                r.ultimo_erro_ia = repr(e)
                print(f"[ia] erro na classificação ({e!r}) — item segue para as regras/pendentes")

        if veredito_ia is not None:
            linha["ia"] = veredito_ia
            achado.detalhes["ia"] = veredito_ia
            alvo = veredito_ia["area"] in ("tributario", "controle")
            if veredito_ia["classe"] == "abertura" and alvo and veredito_ia["cargo"]:
                r.publicar.append((achado, veredito_ia["area"], termos, de_pendentes))
                linha["destino"] = "publicado (ia: abertura)"
            elif veredito_ia["classe"] == "suspensao" and alvo:
                r.publicar.append((achado, veredito_ia["area"], termos, de_pendentes))
                linha["destino"] = "publicado (ia: suspensao)"
            else:
                r.descartar.append((achado, veredito_ia, de_pendentes))
                linha["destino"] = f"descartado (ia: {veredito_ia['classe']})"
            r.relatorio.append(linha)
            continue

        # Sem veredito de IA: só as pontas fortes das regras publicam.
        if veredito_r in ("abertura", "suspensao"):
            veredito = _veredito_regras(veredito_r, achado, categoria, termos, motivo)
            achado.detalhes["ia"] = veredito
            r.publicar.append((achado, categoria, termos, de_pendentes))
            linha["destino"] = f"publicado (regras: {veredito_r})"
            r.relatorio.append(linha)
            continue

        # "incerto" sem IA => fila de pendentes (fail-closed), com expiração.
        if meta is None:
            meta = {"enfileirado_em": hoje.isoformat(), "tentativas": 0}
            r.novos_pendentes += 1
        meta["tentativas"] += 1
        limite = dt.date.fromisoformat(meta["enfileirado_em"]) + dt.timedelta(days=expira_dias)
        if hoje > limite:
            r.expirados += 1
            r.descartar.append(
                (
                    achado,
                    _veredito_regras(
                        "irrelevante", achado, categoria, termos,
                        f"expirou sem classificação após {expira_dias} dias na fila",
                    ),
                    de_pendentes,
                )
            )
            linha["destino"] = "descartado (expirado na fila)"
        else:
            r.pendentes.append((achado, categoria, termos, meta))
            linha["destino"] = f"pendente (tentativa {meta['tentativas']})"
        r.relatorio.append(linha)

    return r
