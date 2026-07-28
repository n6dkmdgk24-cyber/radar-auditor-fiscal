"""Coletores de fontes.

Contrato: cada módulo expõe

    def coletar(cfg: dict, cursor: dict, desde_padrao: datetime.date) -> list[Achado]

- cfg: config.yaml carregado.
- cursor: dict mutável e persistido por fonte; o coletor lê o próprio marco
  (ex.: cursor.get("scraped_since")) com fallback em desde_padrao e o atualiza
  ao final. Em dry-run/backtest o main passa um dict descartável.
- desde_padrao: data inicial da janela quando não há cursor.

O coletor NÃO filtra por cargo (isso é do Filtro) — apenas restringe ao que a
fonte oferece (ex.: consultas por frase no QD/DOU) e preenche cargo_texto com
o texto útil para classificação. Erros devem estourar (o main captura e avisa).
"""
