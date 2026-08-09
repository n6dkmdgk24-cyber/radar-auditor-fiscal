"""Data e hora sempre no fuso de Brasília.

O GitHub Actions roda em UTC: entre 21h e 0h de Brasília o runner já está no
dia seguinte, e os carimbos (descoberto_em, descartado_em, enfileirado_em)
nasciam um dia à frente do que o painel exibia — além de deslocar em 3h o
corte "inscrições encerradas". Todo o radar passa a usar estas funções.
"""

import datetime as dt
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    FUSO = ZoneInfo("America/Sao_Paulo")
except ZoneInfoNotFoundError:  # runner sem base de fusos: UTC-3 fixo
    # o Brasil não adota horário de verão desde 2019 (Decreto 9.772/2019),
    # então o deslocamento constante vale enquanto isso não mudar
    FUSO = dt.timezone(dt.timedelta(hours=-3), "America/Sao_Paulo")


def agora() -> dt.datetime:
    return dt.datetime.now(FUSO)


def hoje() -> dt.date:
    return agora().date()


def carimbo_data() -> str:
    """AAAA-MM-DD."""
    return hoje().isoformat()


def carimbo_hora() -> str:
    """AAAA-MM-DDTHH:MM:SS."""
    return agora().strftime("%Y-%m-%dT%H:%M:%S")
