"""Digest do radar por e-mail (SMTP)."""

import html
import os
import smtplib
import time
from email.message import EmailMessage

CATEGORIAS_ORDEM = ("tributario", "controle", "conferir")
CATEGORIAS_TITULO = {
    "tributario": "Tributário",
    "controle": "Controle",
    "conferir": "Conferir",
}


def _local(achado):
    """Monta 'órgão (município/UF)', com fallback para o que estiver disponível."""
    partes = " / ".join(x for x in (achado.municipio, achado.uf) if x)
    if achado.orgao and partes:
        return f"{achado.orgao} ({partes})"
    return achado.orgao or partes or "local n/d"


def _falha_resumo(traceback_str):
    linhas = [l for l in (traceback_str or "").strip().splitlines() if l.strip()]
    return linhas[-1] if linhas else "erro sem detalhe"


def _credenciais():
    host = os.environ.get("SMTP_HOST")
    user = os.environ.get("SMTP_USER")
    senha = os.environ.get("SMTP_PASS")
    para_bruto = os.environ.get("EMAIL_PARA")
    destinatarios = [p.strip() for p in (para_bruto or "").split(",") if p.strip()]
    if not (host and user and senha and destinatarios):
        return None
    return {
        "host": host,
        "porta": int(os.environ.get("SMTP_PORT", "587")),
        "user": user,
        "senha": senha,
        "de": os.environ.get("EMAIL_DE") or user,
        "para": destinatarios,
    }


def _texto_plano(novos, falhas):
    linhas = []
    for categoria in CATEGORIAS_ORDEM:
        itens = [(a, termos) for a, cat, termos in novos if cat == categoria]
        if not itens:
            continue
        titulo_secao = CATEGORIAS_TITULO[categoria]
        linhas.append(titulo_secao.upper())
        linhas.append("-" * len(titulo_secao))
        for a, termos in itens:
            linhas.append(f"* {a.titulo}")
            linhas.append(f"  local: {_local(a)}")
            linhas.append(f"  termos: {', '.join(termos)}")
            linhas.append(f"  fonte: {a.fonte}")
            linhas.append(f"  link: {a.url}")
            linhas.append("")
    if falhas:
        linhas.append("FALHAS DE COLETA")
        linhas.append("-" * len("FALHAS DE COLETA"))
        for fonte, tb in falhas:
            linhas.append(f"* {fonte}: {_falha_resumo(tb)}")
        linhas.append("")
    if not linhas:
        linhas.append("Sem novidades nem falhas.")
    return "\n".join(linhas)


def _html_lista(novos, falhas):
    partes = [
        '<div style="font-family: Arial, Helvetica, sans-serif; font-size: 14px; color: #222;">'
    ]
    corpo_vazio = True
    for categoria in CATEGORIAS_ORDEM:
        itens = [(a, termos) for a, cat, termos in novos if cat == categoria]
        if not itens:
            continue
        corpo_vazio = False
        partes.append(f"<h3>{html.escape(CATEGORIAS_TITULO[categoria])}</h3>")
        partes.append('<ul style="margin: 0 0 16px 0; padding-left: 20px;">')
        for a, termos in itens:
            url = html.escape(a.url, quote=True)
            partes.append(
                "<li style=\"margin-bottom: 8px;\">"
                f'<a href="{url}"><strong>{html.escape(a.titulo)}</strong></a><br>'
                f"{html.escape(_local(a))}<br>"
                f"termos: {html.escape(', '.join(termos))}<br>"
                f"fonte: {html.escape(a.fonte)}"
                "</li>"
            )
        partes.append("</ul>")
    if falhas:
        corpo_vazio = False
        partes.append("<h3>Falhas de coleta</h3>")
        partes.append('<ul style="margin: 0 0 16px 0; padding-left: 20px;">')
        for fonte, tb in falhas:
            partes.append(
                f"<li><strong>{html.escape(fonte)}</strong>: "
                f"{html.escape(_falha_resumo(tb))}</li>"
            )
        partes.append("</ul>")
    if corpo_vazio:
        partes.append("<p>Sem novidades nem falhas.</p>")
    partes.append("</div>")
    return "".join(partes)


def _html(novos, falhas):
    return (
        "<html><body>"
        '<h2 style="font-family: Arial, Helvetica, sans-serif;">Radar Auditor Fiscal</h2>'
        f"{_html_lista(novos, falhas)}"
        "</body></html>"
    )


def enviar(novos, falhas, cfg):
    """Envia o digest por e-mail; só dispara se houver novidades ou falhas.

    Credenciais via ambiente: SMTP_HOST, SMTP_PORT (padrão 587, STARTTLS),
    SMTP_USER, SMTP_PASS, EMAIL_PARA (um ou mais separados por vírgula) e
    EMAIL_DE (padrão = SMTP_USER). Faltando o essencial, apenas avisa e retorna.
    """
    if not novos and not falhas:
        return

    cred = _credenciais()
    if cred is None:
        print(
            "[email] aviso: credenciais SMTP incompletas "
            "(SMTP_HOST/SMTP_USER/SMTP_PASS/EMAIL_PARA) — e-mail não enviado"
        )
        return

    hoje = time.strftime("%d.%m.%Y")
    assunto = f"Radar Auditor Fiscal — {len(novos)} novidade(s) em {hoje}"

    msg = EmailMessage()
    msg["Subject"] = assunto
    msg["From"] = cred["de"]
    msg["To"] = ", ".join(cred["para"])
    msg.set_content(_texto_plano(novos, falhas))
    msg.add_alternative(_html(novos, falhas), subtype="html")

    with smtplib.SMTP(cred["host"], cred["porta"], timeout=30) as smtp:
        smtp.starttls()
        smtp.login(cred["user"], cred["senha"])
        smtp.send_message(msg)
