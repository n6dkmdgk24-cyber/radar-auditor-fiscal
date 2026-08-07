import dataclasses
import hashlib
import json
import re
import time
from pathlib import Path

from .filtro import normalizar
from .modelos import Achado


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
        ano = (achado.data_publicacao or "")[:4] or time.strftime("%Y")
        k_ente = "c:" + self._slug(f"{ente}-{achado.uf}-{categoria}-{ano}")
        return [k_url, k_ente]

    def ja_visto(self, achado, categoria) -> bool:
        return any(k in self._vistos for k in self._chaves(achado, categoria))

    def ja_visto_ente(self, achado, categoria) -> bool:
        """Só a chave ente+UF+categoria+ano — para item vindo da fila de
        pendentes, cuja própria URL já foi marcada no enfileiramento."""
        return self._chaves(achado, categoria)[1] in self._vistos

    def marcar(self, achado, categoria):
        agora = time.strftime("%Y-%m-%dT%H:%M:%S")
        for k in self._chaves(achado, categoria):
            self._vistos[k] = agora

    def marcar_url(self, achado):
        """Marca só a URL (item enfileirado): impede a re-coleta sem reservar
        a chave do ente — outro item do mesmo concurso ainda pode publicar."""
        self._vistos["u:" + hashlib.sha1(achado.url.encode()).hexdigest()[:16]] = (
            time.strftime("%Y-%m-%dT%H:%M:%S")
        )

    def registrar_concurso(self, achado, categoria, termos):
        self.concursos.append(
            {
                "descoberto_em": time.strftime("%Y-%m-%d"),
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
                "descartado_em": time.strftime("%Y-%m-%d"),
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
                {"executado_em": time.strftime("%Y-%m-%dT%H:%M:%S"), "itens": linhas},
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )

    # ---- manutenção ----------------------------------------------------
    def expirar(self, dias: int):
        limite = time.strftime(
            "%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - dias * 86400)
        )
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
