"""Envio de avisos via bot do Telegram.

Credenciais via ambiente: TELEGRAM_TOKEN e TELEGRAM_CHAT_ID. Se ausentes,
apenas avisa e retorna (não é erro de execução do radar).
"""

import os
import time

import requests

API_URL = "https://api.telegram.org/bot{token}/sendMessage"
TIMEOUT = 30
LIMITE_INDIVIDUAL = 12
PAUSA_ENTRE_ENVIOS = 0.5

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}

EMOJI_CATEGORIA = {
    "tributario": "💰",
    "controle": "🎯",
    "conferir": "❓",
}


def _escapar(texto):
    """Escapa &, < e > para uso seguro com parse_mode=HTML."""
    texto = str(texto or "")
    texto = texto.replace("&", "&amp;")
    texto = texto.replace("<", "&lt;")
    texto = texto.replace(">", "&gt;")
    return texto


def _local(achado):
    municipio, uf = achado.municipio, achado.uf
    if municipio and uf:
        return f"{municipio}/{uf}"
    return municipio or uf


def _mensagem_achado(achado, categoria, termos):
    emoji = EMOJI_CATEGORIA.get(categoria, "❓")
    linhas = [f"{emoji} <b>{_escapar(achado.titulo)}</b>", ""]
    if achado.orgao:
        linhas.append(f"Órgão: {_escapar(achado.orgao)}")
    local = _local(achado)
    if local:
        linhas.append(f"Local: {_escapar(local)}")
    linhas.append(f"Termos: {_escapar(', '.join(termos))}")
    linhas.append(f"Fonte: {_escapar(achado.fonte)}")
    linhas.append(_escapar(achado.url))
    return "\n".join(linhas)


def _mensagem_resumo(resto):
    linhas = [f"📋 Mais {len(resto)} achado(s) nesta execução:", ""]
    for achado, categoria, _termos in resto:
        emoji = EMOJI_CATEGORIA.get(categoria, "❓")
        linhas.append(f"{emoji} {_escapar(achado.titulo)} — {_escapar(achado.url)}")
    return "\n".join(linhas)


def _linha_erro(traceback_str):
    """Última linha não vazia do traceback (tipo + mensagem da exceção),
    que é a informação útil do erro sem o stack inteiro."""
    linhas = [l for l in traceback_str.strip().splitlines() if l.strip()]
    return linhas[-1] if linhas else "erro desconhecido"


def _mensagem_falhas(falhas):
    if len(falhas) == 1:
        fonte, tb = falhas[0]
        return (
            f"⚠️ Radar: coletor {_escapar(fonte)} falhou nesta execução\n"
            f"{_escapar(_linha_erro(tb))}"
        )
    linhas = [f"⚠️ Radar: {len(falhas)} coletores falharam nesta execução:", ""]
    for fonte, tb in falhas:
        linhas.append(f"• {_escapar(fonte)}: {_escapar(_linha_erro(tb))}")
    return "\n".join(linhas)


def _enviar_mensagem(token, chat_id, texto):
    url = API_URL.format(token=token)
    resp = requests.post(
        url,
        data={"chat_id": chat_id, "text": texto, "parse_mode": "HTML"},
        headers=HEADERS,
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    corpo = resp.json()
    if not corpo.get("ok"):
        raise RuntimeError(f"API do Telegram retornou erro: {corpo}")


def enviar(novos, falhas, cfg):
    """Envia um aviso por achado novo (resumindo o excedente acima de 12)
    e uma mensagem consolidada de falhas de coletor, se houver."""
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("[telegram] aviso: TELEGRAM_TOKEN/TELEGRAM_CHAT_ID ausentes no ambiente, envio pulado")
        return

    mensagens = []
    if novos:
        individuais = novos[:LIMITE_INDIVIDUAL]
        resto = novos[LIMITE_INDIVIDUAL:]
        for achado, categoria, termos in individuais:
            mensagens.append(_mensagem_achado(achado, categoria, termos))
        if resto:
            mensagens.append(_mensagem_resumo(resto))
    if falhas:
        mensagens.append(_mensagem_falhas(falhas))

    for msg in mensagens:
        _enviar_mensagem(token, chat_id, msg)
        time.sleep(PAUSA_ENTRE_ENVIOS)
