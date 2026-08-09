"""Triagem dos candidatos SEM IA: regras + verificação profunda, FAIL-CLOSED.

Fluxo por item (novos da coleta + fila de pendentes de execuções anteriores):

0. radar/extrator.py — dados estruturados do texto já coletado (período de
   inscrições, vagas do cargo, banca, validade, site de inscrição). Alimenta
   as decisões abaixo e os cartões do painel.
1. radar/regras.py — veredito determinístico por título/trecho.
   - "descarte" encerra ali (registro em descartados.json);
   - "abertura"/"suspensao" publica (evidência textual + termo forte);
   - retificação decide pela data de inscrição extraída.
2. "incerto" vai ao radar/verificador.py, que baixa o TEXTO INTEGRAL do
   documento e pontua a anatomia (edital de abertura × ato de pessoal).
3. Ainda incerto (ou rede fora): FILA DE PENDENTES (data/pendentes.json),
   exibida no painel como "aguardando confirmação", re-tentada a cada
   execução e expirada após o prazo do config.

Guarda transversal: "abertura" com inscrições comprovadamente ENCERRADAS
(fim extraído < hoje) não vira aviso — vira descarte com o motivo certo
(feedback do Danilo, 6.8.2026, caso Meridiano/SP).

Nada sem veredito vira aviso de concurso. Este desenho substituiu a IA em
5.8.2026 por decisão de produto: a IA gratuita (GitHub Models) morreu em
30.7.2026 e derrubou a triagem por 5 dias; API paga foi vetada pelo Danilo.
"""

import datetime as dt
import re
from dataclasses import dataclass, field

from . import extrator, regras, tempo, verificador
from .filtro import frase_para_regex, normalizar
from .regras import FONTES_DE_NOTICIA

# Termo ambíguo de CONTROLE confirmado como abertura assume a área direto.
_AREA_DO_TERMO_AMBIGUO = {
    "controlador interno": "controle",
    "auditor interno": "controle",
    "auditor municipal": "controle",
    "auditor publico": "controle",
}

# Termo fiscal genérico: só ganha selo tributário com evidência de atribuição
# tributária perto do cargo. Sem evidência, o cartão sai como "conferir" —
# fiscal nem sempre é de tributos; pode ser obras/posturas/sanitário
# (feedback do Danilo, 6.8.2026; caso real: Fiscal Municipal de Ipuã/SP é
# híbrido de posturas + tributário, e só o edital diz isso).
_TERMOS_FISCAIS_GENERICOS = {"fiscal municipal", "agente fiscal"}
_RX_EVIDENCIA_TRIBUTARIA = re.compile(
    r"tribut|arrecadac|fazendar|\brendas\b|\biss\b|\bissqn\b|\biptu\b|\bitbi\b"
)
_JANELA_EVIDENCIA = 300


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


def _veredito(classe, area, cargo, motivo, origem, extracao=None, cadastro_reserva=False):
    ex = extracao or {}
    return {
        "classe": classe,
        "area": area,
        "cargo": cargo,
        "inscricoes": ex.get("inscricoes_texto", ""),
        "vagas": ex.get("vagas_texto", ""),
        "validade": ex.get("validade", ""),
        "cadastro_reserva": cadastro_reserva,
        # resumo é o texto HUMANO do cartão; o critério técnico (regex da
        # regra) fica em "criterio", para auditoria — nunca no painel
        "resumo": ex.get("resumo") or motivo,
        "criterio": motivo,
        "origem": origem,
    }


def _area_do_ambiguo(cargo, texto_norm):
    if cargo in _AREA_DO_TERMO_AMBIGUO:
        return _AREA_DO_TERMO_AMBIGUO[cargo]
    if cargo in _TERMOS_FISCAIS_GENERICOS:
        for m in frase_para_regex(cargo).finditer(texto_norm):
            ini = max(0, m.start() - _JANELA_EVIDENCIA)
            if _RX_EVIDENCIA_TRIBUTARIA.search(texto_norm[ini:m.end() + _JANELA_EVIDENCIA]):
                return "tributario"
        return "conferir"
    return "conferir"


_RX_DATA_BR = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")


def _fim_do_texto_de_inscricao(texto):
    """Última data de um período dd/mm/aaaa vindo do verificador → ISO.
    Sem isso o cartão publicado por essa via nunca ganhava prazo (e a guarda
    de inscrições encerradas não o alcançava)."""
    datas = []
    for dia, mes, ano in _RX_DATA_BR.findall(texto or ""):
        try:
            datas.append(dt.date(int(ano), int(mes), int(dia)).isoformat())
        except ValueError:
            continue
    return max(datas) if datas else ""


def _preencher_detalhes(achado, ex):
    """Dados extraídos entram em detalhes (o painel lê de lá); campos já
    preenchidos pelo coletor (ex.: banca do selecao) têm precedência."""
    det = achado.detalhes
    for campo in ("inscricoes_inicio", "inscricoes_fim", "site_inscricao"):
        if ex.get(campo) and not det.get(campo):
            det[campo] = ex[campo]
    if ex.get("banca") and not det.get("banca"):
        det["banca"] = ex["banca"]


def triar(candidatos, pendentes_antigos, cfg, hoje=None):
    """candidatos: [(achado, categoria, termos)] novos desta coleta.
    pendentes_antigos: [(achado, categoria, termos, meta)] da fila persistida.
    """
    r = Resultado()
    hoje = hoje or tempo.hoje()
    expira_dias = cfg.get("pendentes_expira_dias", 30)

    fila = [(a, c, t, None) for a, c, t in candidatos]
    fila += [(a, c, t, m) for a, c, t, m in pendentes_antigos]

    for achado, categoria, termos, meta in fila:
        de_pendentes = meta is not None
        cargo = termos[0] if termos else ""
        forte = categoria in ("tributario", "controle")
        area = categoria if forte else "outra"
        texto_norm = normalizar(
            f"{achado.titulo}\n{achado.cargo_texto}\n{achado.detalhes.get('trecho', '')}"
        )

        # O extrator foi calibrado para o artigo estereotipado de notícia. Em
        # diário oficial bruto (qd/sigpub/domsc) o texto mistura vários atos e
        # "inscrição" aparece em contexto alheio (dívida ativa, concurso
        # homologado): datas dali descartariam abertura real pela guarda de
        # prazo vencido. Fora das fontes de notícia, extração neutra.
        ex = (
            extrator.extrair(achado, termos, hoje=hoje)
            if achado.fonte in FONTES_DE_NOTICIA
            else extrator.vazio()
        )
        veredito_r, motivo_r = regras.triar(achado, categoria, termos, extracao=ex, hoje=hoje)
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

        def _publicar(classe, motivo, origem, extras=None):
            """Publica com a área resolvida e o cartão preenchido; devolve a
            linha de destino para o relatório."""
            categoria_final = categoria if forte else _area_do_ambiguo(cargo, texto_norm)
            area_final = categoria_final if categoria_final != "conferir" else "outra"
            if (extras or {}).get("inscricoes") and not ex["inscricoes_texto"]:
                ex["inscricoes_texto"] = extras["inscricoes"]
                ex["inscricoes_fim"] = _fim_do_texto_de_inscricao(extras["inscricoes"])
            _preencher_detalhes(achado, ex)
            achado.detalhes["ia"] = _veredito(
                classe, area_final, cargo, motivo, origem,
                extracao=ex, cadastro_reserva=ex["cr_somente"],
            )
            r.publicar.append((achado, categoria_final, termos, de_pendentes))
            return f"publicado ({origem}: {classe})"

        def _encerrado():
            """Guarda: abertura com fim extraído no passado não é aviso."""
            return (
                ex["inscricoes_fim"]
                and ex["inscricoes_fim"] < hoje.isoformat()
                and veredito_r != "suspensao"
            )

        if veredito_r == "descarte":
            classe = "irrelevante" if "documento" in motivo_r else "andamento"
            r.descartar.append(
                (achado, _veredito(classe, area, "", motivo_r, "regras"), de_pendentes)
            )
            linha["destino"] = "descartado (regras)"
            r.relatorio.append(linha)
            continue

        if veredito_r in ("abertura", "suspensao"):
            if veredito_r == "abertura" and _encerrado():
                r.descartar.append(
                    (
                        achado,
                        _veredito(
                            "andamento", area, "",
                            f"inscrições já encerradas em {ex['inscricoes_fim']}",
                            "extrator",
                        ),
                        de_pendentes,
                    )
                )
                linha["destino"] = "descartado (inscrições encerradas)"
            else:
                linha["destino"] = _publicar(veredito_r, motivo_r, "regras")
            r.relatorio.append(linha)
            continue

        # Incerto pelas regras: verificação profunda no texto integral.
        # Exceção: retificação sem data legível NÃO passa pelo verificador —
        # o artigo de retificação tem a mesma anatomia de um de abertura e
        # seria promovido errado; vai direto para a fila.
        if motivo_r.startswith("retificação"):
            veredito_v, motivo_v, extras = "incerto", motivo_r, {}
        else:
            veredito_v, motivo_v, extras = verificador.verificar(achado, termos)
        linha["verificador"] = f"{veredito_v}: {motivo_v}"

        if veredito_v == "abertura":
            # o período que o verificador leu do texto integral também vale
            # para a guarda de prazo vencido
            if (extras or {}).get("inscricoes") and not ex["inscricoes_fim"]:
                ex["inscricoes_fim"] = _fim_do_texto_de_inscricao(extras["inscricoes"])
            if _encerrado():
                r.descartar.append(
                    (
                        achado,
                        _veredito(
                            "andamento", area, "",
                            f"inscrições já encerradas em {ex['inscricoes_fim']}",
                            "extrator",
                        ),
                        de_pendentes,
                    )
                )
                linha["destino"] = "descartado (inscrições encerradas)"
            else:
                linha["destino"] = _publicar("abertura", motivo_v, "verificador", extras)
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
