from dataclasses import dataclass, field


@dataclass
class Achado:
    """Um possível concurso encontrado por um coletor, antes do filtro."""

    fonte: str                  # qd | pci | cnb | dou | ...
    titulo: str                 # título da notícia ou descrição curta do diário
    url: str                    # link canônico (notícia, edital ou PDF do diário)
    cargo_texto: str = ""       # texto adicional onde procurar o cargo (resumo, trecho do diário)
    orgao: str = ""             # ex.: Prefeitura de Cezarina
    municipio: str = ""         # quando souber
    uf: str = ""                # sigla, quando souber
    data_publicacao: str = ""   # AAAA-MM-DD quando conhecida
    detalhes: dict = field(default_factory=dict)  # extras por fonte (banca, vagas, inscricoes_fim AAAA-MM-DD...)
