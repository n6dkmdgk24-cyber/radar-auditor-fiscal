import dataclasses
import datetime as dt
import hashlib
import json
import re
from pathlib import Path

from . import tempo
from .filtro import normalizar
from .modelos import Achado

# A chave de deduplicação usa a FAMÍLIA da categoria, não a categoria exibida:
# o mesmo concurso pode sair "conferir" por um artigo (cargo fiscal genérico
# sem evidência tributária) e "tributario" por outro, e eram dois cartões.
_FAMILIA = {"tributario": "fiscal", "conferir": "fiscal", "controle": "controle"}

# Entes distintos da mesma cidade (Prefeitura × Câmara × autarquia) não podem
# compartilhar chave. "Município de X" e "Prefeitura de X" são o mesmo ente;
# "Câmara de X" e "SAAE de X" não são (revisão de 6.8.2026, caso Barretos/SP).
_TIPOS_DE_ENTE = (
    ("camara", r"\bcamara\b"),
    ("saae", r"\bsaae\b|\bsamae\b|\bautarquia\b"),
    ("prefeitura", r"\bprefeitura\b|\bmunicipio\b"),
)


def _tipo_de_ente(orgao, titulo=""):
    alvo = normalizar(f"{orgao} {titulo}")
    for tipo, padrao in _TIPOS_DE_ENTE:
        if re.search(padrao, alvo):
            return tipo
    return "outro"


class Estado:
    """Persistência em data/: cursors por fonte, deduplicação e registro do painel.

    - estado.json    -> {"cursors": {fonte: {...}}}
    - vistos.json    -> {chave: timestamp} para deduplicação
    - concursos.json -> lista acumulada de achados relevantes (alimenta o painel)
    - descartados.json -> registro auditável do que a triagem rejeitou
    - pendentes.json -> fila fail-closed: itens sem veredito aguardando IA
    - relatorio.json -> decisões da última execução, item a item
    """

    def __init__(self, data_dir):
        self.dir = Path(data_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._estado = self._ler("estado.json", {"cursors": {}})
        self._vistos = self._ler("vistos.json", {})
        self.concursos = self._ler("concursos.json", [])
        self.descartados = self._ler("descartados.json", [])
        self._pendentes = self._ler("pendentes.json", [])

    def _ler(self, nome, padrao):
        arq = self.dir / nome
        if arq.exists():
            return json.loads(arq.read_text(encoding="utf-8"))
        return padrao

    def cursor(self, fonte: str) -> dict:
        return self._estado["cursors"].setdefault(fonte, {})

    # ---- deduplicação -------------------------------------------------
    @staticmethod
    def _slug(s: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", normalizar(s)).strip("-")

    def _chaves(self, achado, categoria):
        k_url = "u:" + hashlib.sha1(achado.url.encode()).hexdigest()[:16]
        ente = achado.municipio or achado.orgao or achado.titulo[:60]
        ano = (achado.data_publicacao or "")[:4] or tempo.carimbo_data()[:4]
        familia = _FAMILIA.get(categoria, categoria)
        tipo = _tipo_de_ente(achado.orgao, achado.titulo)
        k_ente = "c:" + self._slug(f"{tipo}-{ente}-{achado.uf}-{familia}-{ano}")
        return [k_url, k_ente]

    def ja_visto(self, achado, categoria) -> bool:
        return any(k in self._vistos for k in self._chaves(achado, categoria))

    def ja_visto_ente(self, achado, categoria) -> bool:
        """Só a chave ente+UF+categoria+ano — para item vindo da fila de
        pendentes, cuja própria URL já foi marcada no enfileiramento."""
        return self._chaves(achado, categoria)[1] in self._vistos

    def marcar(self, achado, categoria):
        agora = tempo.carimbo_hora()
        for k in self._chaves(achado, categoria):
            self._vistos[k] = agora

    def marcar_url(self, achado):
        """Marca só a URL (item enfileirado): impede a re-coleta sem reservar
        a chave do ente — outro item do mesmo concurso ainda pode publicar."""
        self._vistos["u:" + hashlib.sha1(achado.url.encode()).hexdigest()[:16]] = (
            tempo.carimbo_hora()
        )

    def _chave_ente_registro(self, c):
        ente = c.get("municipio") or c.get("orgao") or c.get("titulo", "")[:60]
        ano = (c.get("data_publicacao") or c.get("descoberto_em") or "")[:4] or tempo.carimbo_data()[:4]
        familia = _FAMILIA.get(c["categoria"], c["categoria"])
        tipo = _tipo_de_ente(c.get("orgao", ""), c.get("titulo", ""))
        return "c:" + self._slug(f"{tipo}-{ente}-{c.get('uf', '')}-{familia}-{ano}")

    def chaves_de_ente_atuais(self):
        """Chaves de ente de todos os cartões registrados — usada para
        ressincronizar vistos.json quando um campo da chave muda."""
        return {self._chave_ente_registro(c) for c in self.concursos}

    def cartao_do_ente(self, achado, categoria):
        """Cartão já publicado com a mesma chave de ente, ou None."""
        alvo = self._chaves(achado, categoria)[1]
        for c in self.concursos:
            if self._chave_ente_registro(c) == alvo:
                return c
        return None

    def atualizar_concurso(self, achado, categoria, hoje=None):
        """Prorrogação do MESMO certame: carimba o prazo novo no cartão já
        publicado em vez de criar outro (caso TCE-SP, 7.8.2026).

        Só vale como prorrogação quando o novo período começa até o fim do
        prazo antigo. Período que começa DEPOIS do encerramento é outro
        certame (ou reabertura), e aí o item precisa virar cartão próprio —
        quem decide isso é o chamador, por `eh_prorrogacao`.
        """
        det_novo = achado.detalhes or {}
        fim = det_novo.get("inscricoes_fim", "")
        if not fim:
            return False
        c = self.cartao_do_ente(achado, categoria)
        if c is None:
            return False
        det = c.setdefault("detalhes", {})
        antigo = det.get("inscricoes_fim") or ""
        if antigo >= fim:
            return False
        inicio_novo = det_novo.get("inscricoes_inicio") or ""
        if antigo and inicio_novo and inicio_novo > antigo:
            return False  # começa depois do fim do anterior: outro certame
        det["inscricoes_fim"] = fim
        if inicio_novo and not det.get("inscricoes_inicio"):
            det["inscricoes_inicio"] = inicio_novo
        ia = det.setdefault("ia", {})
        ia_novo = det_novo.get("ia") or {}
        if ia_novo.get("inscricoes"):
            ia["inscricoes"] = ia_novo["inscricoes"]
        ia["prazo_atualizado_em"] = tempo.carimbo_data()
        return True

    def eh_certame_novo(self, achado, categoria):
        """True quando o item tem período de inscrição que COMEÇA depois do
        fim registrado no cartão do mesmo ente — reabertura meses depois ou
        segundo certame do ano. Nesse caso a duplicata é aparente e o item
        precisa virar cartão próprio, em vez de sumir no `continue`."""
        inicio = (achado.detalhes or {}).get("inscricoes_inicio", "")
        if not inicio:
            return False
        c = self.cartao_do_ente(achado, categoria)
        if c is None:
            return False
        antigo = (c.get("detalhes") or {}).get("inscricoes_fim") or ""
        return bool(antigo) and inicio > antigo

    def registrar_concurso(self, achado, categoria, termos):
        self.concursos.append(
            {
                "descoberto_em": tempo.carimbo_data(),
                "categoria": categoria,
                "termos": termos,
                "fonte": achado.fonte,
                "titulo": achado.titulo,
                "url": achado.url,
                "orgao": achado.orgao,
                "municipio": achado.municipio,
                "uf": achado.uf,
                "data_publicacao": achado.data_publicacao,
                "detalhes": achado.detalhes,
            }
        )

    def registrar_descartado(self, achado, veredito):
        self.descartados.append(
            {
                "descartado_em": tempo.carimbo_data(),
                "veredito": veredito,
                "fonte": achado.fonte,
                "titulo": achado.titulo,
                "url": achado.url,
                "municipio": achado.municipio,
                "uf": achado.uf,
                "trecho": achado.detalhes.get("trecho", ""),
            }
        )

    # ---- memória de falhas de coletor (anti-spam de avisos) ------------
    def falhas_anteriores(self):
        return set(self._estado.get("fontes_com_falha", []))

    def registrar_falhas(self, fontes):
        self._estado["fontes_com_falha"] = sorted(fontes)

    # ---- fila de pendentes (fail-closed) -------------------------------
    def pendentes_carregados(self):
        """Devolve [(Achado, categoria, termos, meta)] da fila persistida."""
        itens = []
        for p in self._pendentes:
            itens.append(
                (
                    Achado(**p["achado"]),
                    p["categoria"],
                    p["termos"],
                    {"enfileirado_em": p["enfileirado_em"], "tentativas": p["tentativas"]},
                )
            )
        return itens

    def definir_pendentes(self, pendentes):
        """Substitui a fila por [(Achado, categoria, termos, meta)]."""
        self._pendentes = [
            {
                "enfileirado_em": meta["enfileirado_em"],
                "tentativas": meta["tentativas"],
                "categoria": categoria,
                "termos": termos,
                "achado": dataclasses.asdict(achado),
            }
            for achado, categoria, termos, meta in pendentes
        ]

    @property
    def total_pendentes(self):
        return len(self._pendentes)

    def registrar_relatorio(self, linhas):
        """Grava as decisões da última execução (sobrescreve)."""
        (self.dir / "relatorio.json").write_text(
            json.dumps(
                {"executado_em": tempo.carimbo_hora(), "itens": linhas},
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )

    # ---- manutenção ----------------------------------------------------
    def expirar(self, dias: int):
        limite = (tempo.agora() - dt.timedelta(days=dias)).strftime("%Y-%m-%dT%H:%M:%S")
        self._vistos = {k: v for k, v in self._vistos.items() if v >= limite}

    def salvar(self):
        (self.dir / "estado.json").write_text(
            json.dumps(self._estado, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        (self.dir / "vistos.json").write_text(
            json.dumps(self._vistos, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        (self.dir / "concursos.json").write_text(
            json.dumps(self.concursos, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        (self.dir / "descartados.json").write_text(
            json.dumps(self.descartados, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        (self.dir / "pendentes.json").write_text(
            json.dumps(self._pendentes, ensure_ascii=False, indent=1), encoding="utf-8"
        )
