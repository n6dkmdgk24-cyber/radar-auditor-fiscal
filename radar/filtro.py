import re
import unicodedata


def normalizar(texto: str) -> str:
    """Minúsculas, sem acento, hifens tipográficos viram '-'."""
    t = unicodedata.normalize("NFKD", texto or "")
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.lower()
    return re.sub(r"[‐-―]", "-", t)


def _frase_para_regex(frase: str) -> re.Pattern:
    palavras = [re.escape(p) for p in normalizar(frase).split()]
    return re.compile(r"\b" + r"[-\s]+".join(palavras) + r"\b")


class Filtro:
    """Classifica um texto em tributario | controle | conferir | None.

    Os homônimos (exclusao) são mascarados do texto antes do teste de
    inclusão, para que "auditor-fiscal do trabalho" não dispare
    "auditor fiscal" nem "fiscal de obras" dispare "fiscal".
    """

    CATEGORIAS = ("tributario", "controle", "conferir")

    def __init__(self, termos_cfg: dict):
        self.exclusao = [_frase_para_regex(f) for f in termos_cfg.get("exclusao", [])]
        self.grupos = [
            (cat, [(_frase_para_regex(f), f) for f in termos_cfg.get(cat, [])])
            for cat in self.CATEGORIAS
        ]

    def classificar(self, texto: str):
        """Retorna (categoria, termos_casados); (None, []) se irrelevante."""
        t = normalizar(texto)
        for rx in self.exclusao:
            t = rx.sub(" ", t)
        for categoria, regras in self.grupos:
            casados = [frase for rx, frase in regras if rx.search(t)]
            if casados:
                return categoria, casados
        return None, []
