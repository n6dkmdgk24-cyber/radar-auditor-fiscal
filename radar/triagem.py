"""Triagem dos candidatos SEM IA: regras + verificação profunda, FAIL-CLOSED.

Fluxo por item (novos da coleta + fila de pendentes de execuções anteriores):

1. radar/regras.py — veredito determinístico por título/trecho.
   - "descarte" encerra ali (registro em descartados.json);
   - "abertura"/"suspensao" publica (evidência textual + termo forte).
2. "incerto" vai ao radar/verificador.py, que baixa o TEXTO INTEGRAL do
   documento e pontua a anatomia (edital de abertura × ato de pessoal).
3. Ainda incerto (ou rede fora): FILA DE PENDENTES (data/pendentes.json),
   exibida no painel como "aguardando confirmação", re-tentada a cada
   execução e expirada após o prazo do config.

Nada sem veredito vira aviso de concurso. Este desenho substituiu a IA em
5.8.2026 por decisão de produto: a IA gratuita (GitHub Models) morreu em
30.7.2026 e derrubou a triagem por 5 dias; API paga foi vetada pelo Danilo.
"""

import datetime as dt
from dataclasses import dataclass, field

from . import regras, verificador

# Termo ambíguo confirmado como abertura pelo verificador assume esta área.
_AREA_DO_TERMO_AMBIGUO = {
    "controlador interno": "controle",
    "auditor interno": "controle",
    "auditor municipal": "controle",
    "auditor publico": "controle",
    "fiscal municipal": "tributario",
    "agente fiscal": "tributario",
}


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


def _veredito(classe, area, cargo, motivo, origem, inscricoes="", cadastro_reserva=False):
    return {
        "classe": classe,
        "area": area,
        "cargo": cargo,
        "inscricoes": inscricoes,
        "cadastro_reserva": cadastro_reserva,
        "resumo": motivo,
        "origem": origem,
    }


def _tem_cadastro_reserva(achado):
    texto = f"{achado.titulo} {achado.cargo_texto} {achado.detalhes.get('trecho', '')}".lower()
    return "cadastro de reserva" in texto or "cadastro reserva" in texto


def triar(candidatos, pendentes_antigos, cfg):
    """candidatos: [(achado, categoria, termos)] novos desta coleta.
    pendentes_antigos: [(achado, categoria, termos, meta)] da fila persistida.
    """
    r = Resultado()
    hoje = dt.date.today()
    expira_dias = cfg.get("pendentes_expira_dias", 30)

    fila = [(a, c, t, None) for a, c, t in candidatos]
    fila += [(a, c, t, m) for a, c, t, m in pendentes_antigos]

    for achado, categoria, termos, meta in fila:
        de_pendentes = meta is not None
        cargo = termos[0] if termos else ""
        forte = categoria in ("tributario", "controle")
        area = categoria if forte else "outra"

        veredito_r, motivo_r = regras.triar(achado, categoria, termos)
        linha = {
            "fonte": achado.fonte,
            "titulo": achado.titulo[:160],
            "url": achado.url,
            "categoria_filtro": categoria,
            "termos": termos,
            "regras": veredito_r,
            "regras_motivo": motivo_r,
            "verificador": None,
            "destino": None,
        }

        if veredito_r == "descarte":
            classe = "irrelevante" if "documento" in motivo_r else "andamento"
            r.descartar.append(
                (achado, _veredito(classe, area, "", motivo_r, "regras"), de_pendentes)
            )
            linha["destino"] = "descartado (regras)"
            r.relatorio.append(linha)
            continue

        if veredito_r in ("abertura", "suspensao"):
            categoria_final = (
                categoria if forte else _AREA_DO_TERMO_AMBIGUO.get(cargo, "conferir")
            )
            area_final = categoria_final if categoria_final != "conferir" else "outra"
            achado.detalhes["ia"] = _veredito(
                veredito_r, area_final, cargo, motivo_r, "regras",
                cadastro_reserva=_tem_cadastro_reserva(achado),
            )
            r.publicar.append((achado, categoria_final, termos, de_pendentes))
            linha["destino"] = f"publicado (regras: {veredito_r})"
            r.relatorio.append(linha)
            continue

        # Incerto pelas regras: verificação profunda no texto integral.
        veredito_v, motivo_v, extras = verificador.verificar(achado, termos)
        linha["verificador"] = f"{veredito_v}: {motivo_v}"

        if veredito_v == "abertura":
            categoria_final = (
                categoria if forte else _AREA_DO_TERMO_AMBIGUO.get(cargo, "conferir")
            )
            area_final = categoria_final if categoria_final != "conferir" else "outra"
            achado.detalhes["ia"] = _veredito(
                "abertura", area_final, cargo, motivo_v, "verificador",
                inscricoes=extras.get("inscricoes", ""),
                cadastro_reserva=_tem_cadastro_reserva(achado),
            )
            r.publicar.append((achado, categoria_final, termos, de_pendentes))
            linha["destino"] = "publicado (verificador: abertura)"
            r.relatorio.append(linha)
            continue

        if veredito_v == "descarte":
            r.descartar.append(
                (achado, _veredito("irrelevante", area, "", motivo_v, "verificador"), de_pendentes)
            )
            linha["destino"] = "descartado (verificador)"
            r.relatorio.append(linha)
            continue

        # Indecidível: fila de pendentes (fail-closed), com expiração.
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
                    _veredito(
                        "irrelevante", area, "",
                        f"expirou sem veredito após {expira_dias} dias na fila "
                        f"(último motivo: {motivo_v})",
                        "fila",
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
