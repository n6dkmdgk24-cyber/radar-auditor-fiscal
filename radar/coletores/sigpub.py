"""Coletor do SIGPub — Diário Oficial dos Municípios, várias associações estaduais.

O SIGPub (Vox Tecnologia) hospeda o diário oficial associativo de ~21 estados
em diariomunicipal.com.br/<base>/, cada um com seu próprio formulário de
busca mas o MESMO motor por trás. Busca pública em
https://www.diariomunicipal.com.br/<base>/pesquisar, formulário GET sem
login (name="busca_avancada"). Os campos relevantes são
busca_avancada[texto] (palavra-chave), busca_avancada[dataInicio]/[dataFim]
(dd/mm/aaaa, intervalo precisa ser inferior a 6 meses) e busca_avancada[_token]
(CSRF do formulário, obtido uma vez por sessão via GET simples e reaproveitado
em todas as consultas seguintes daquela base). A paginação usa
busca_avancada[page]=N, 11 resultados por página.

A busca do SIGPub NÃO é por frase exata: é uma correspondência solta dos
termos da palavra-chave (confirmado buscando "auditor fiscal" e encontrando
matérias com "auditoria" e "fiscalizar" em contextos não relacionados, sem a
frase completa). Por isso cada resultado tem sua matéria completa buscada
(GET <origem>/<base>/load/<hash>, hash e base extraídos do próprio href da
linha — nunca reconstruídos a partir do nome da base pesquisada, para não
quebrar se algum item vier com prefixo diferente) e o trecho de ~300
caracteres é montado a partir da primeira ocorrência real de alguma frase de
cfg['consultas_cargo'] no texto integral, e não do que a busca "disse" ter
encontrado.

BASES: lidas de cfg['sigpub_bases'] (lista de slugs, ex. ["amp", "famurs",
"amupe"]); default ("amp",) quando a chave não existe, preservando a
cobertura exclusiva do Paraná que já existia. A primeira base da lista roda
em TODA execução (preserva a frequência atual de quem já está em produção);
as demais giram em rodízio — até EXTRAS_POR_EXECUCAO_PADRAO bases extras por
execução (cfg['sigpub_extras_por_execucao'] sobrescreve, inclusive para 0 e
desligar o rodízio), com a posição guardada em cursor['proximo_extra'].

CUSTO MEDIDO AO VIVO em 13.8.2026, coletor já reescrito, janela real de 2
dias, cfg['consultas_cargo'] com as 10 frases de produção (cada busca custa
~7-32s no servidor do SIGPub, independente do tamanho da janela de datas —
uma busca de 1 dia custou o mesmo que uma de 90 — e cada matéria baixada
para ler o texto integral soma mais ~1-2s):
  amp    (PR)  41 achados brutos em 386s (maior associação da lista)
  famurs (RS)  15 achados brutos em 177s
  amupe  (PE)  19 achados brutos em 180s
  agm    (GO)  13 achados brutos em 118s
As 8 bases restantes (aprece, ama, famep, arom, femurn, aam, amr, famup) e
fgm não couberam no tempo desta sessão — o padrão acima (~2-6,5 min por
base) generaliza para elas. Rodar TODAS as 13 bases em toda execução
custaria ~35-45 min só de sigpub; por isso o rodízio. Com
EXTRAS_POR_EXECUCAO_PADRAO=1 (amp sempre + 1 extra), o coletor soma
~8-13 min por execução (contra ~6,5 min de hoje, só com amp) — cabe nos
"poucos minutos" pedidos, mas quase dobra o tempo atual; se isso for demais,
cfg['sigpub_extras_por_execucao']=0 desliga o rodízio sem tirar nenhuma
base de sigpub_bases (só a prioritária roda, igual a hoje).

Cada base tem cursor próprio em cursor[base] (sub-chave "ultima_data"); uma
base fora do ar (rede ou token) não impede as outras nem avança o próprio
cursor nem trava a posição do rodízio (fica para tentar de novo na próxima
vez que entrar no rodízio, mas a vez passa adiante mesmo assim).

BASES VERIFICADAS AO VIVO em 13.8.2026 (token OK, formulário
busca_avancada presente, busca real por termo genérico devolveu linhas E a
página inicial tem edição com data recente — só responder 200 não bastou,
ver adiante os quatro casos que passaram nesse primeiro filtro e caíram no
segundo). A UF de cada uma foi conferida no dropdown "Município (Entidade)"
do próprio formulário (nome da associação e/ou município reconhecível
daquele estado — ex. "Cachoeira do Sul" só existe no RS, "Potiguar" só
nomeia o RN), NUNCA pela sigla ou pelo meu palpite; ver UF_DA_BASE.
  - amp    (PR) — já em produção antes desta revisão.
  - famurs (RS) — confirmado por "Cachoeira do Sul" e "AMOP...Potiguar"-like
    (regional gaúcha) no dropdown.
  - amupe  (PE) — nome da associação: "Associação Municipalista de
    Pernambuco".
  - agm    (GO) — dropdown dominado por municípios de Goiás (região do rio
    Meia Ponte).
  - aprece (CE) — nome da associação: "Associação dos Municípios do Estado
    do Ceará".
  - ama    (AL) — dropdown cita "Agência de Promoção de Investimentos de
    Maceió".
  - famep  (PA) — dropdown cita Barcarena/PA.
  - arom   (RO) — nome da associação: "Associação Rondoniense de
    Municípios".
  - femurn (RN) — dropdown cita "AMOP - Associação dos Municípios do Oeste
    Potiguar" (Potiguar = gentílico do RN).
  - aam    (AM) — nome da associação: "Associação Amazonense de
    Municípios".
  - amr    (RR) — nome da associação: "Associação de Câmaras e Vereadores
    do Estado de Roraima".
  - famup  (PB) — dropdown cita "Consórcio... de Segurança Pública da
    Paraíba".
  - fgm    — ALIVE, mas NÃO é Sergipe como a lista de prioridade original
    supunha: o próprio dropdown do formulário lista municípios de Goiás
    (a 1ª entidade já é "Câmara Municipal de Bom Jesus de Goiás", cidade
    que só existe em GO). Mantido na lista com uf="GO" — cobertura extra
    de Goiás, não de Sergipe. Sergipe segue SEM base confirmada.

BASES TESTADAS E DESCARTADAS (respondem 200 e têm o formulário — passariam
num teste que só checasse status HTTP — mas o diário está parado ou vazio,
sem valor para o radar; termo de teste "portaria", genérico, qualquer
diário ativo tem centenas):
  - ms    (seria MS) — página inicial só tem edição até 30.10.2020; busca
    de "portaria" em 7 dias e de "concurso"/"concurso público" em 178 dias
    (máximo aceito pelo formulário) devolveu "Nenhum registro encontrado"
    nas três.
  - appm  (seria PI) — mesmo padrão: última edição 10.2.2020, "portaria" em
    7 dias vazia.
  - amurc (seria BA) — última edição 22.5.2013, "portaria" em 7 dias vazia.
  - bahia (seria BA) — página inicial sem NENHUMA data reconhecível; busca
    de "portaria" vazia mesmo na janela máxima de 178 dias. As duas
    tentativas de cobrir a Bahia falharam; o estado ficou sem base.

PENDÊNCIA: MS, PI, BA e SE ficaram sem base viva confirmada nesta rodada
(ver acima). O catálogo completo do SIGPub tem outras bases fora da lista
de prioridade do Danilo (ex. /apm/ /aemerj/) que não foram verificadas —
ficam de fora até alguém checar.
"""

import datetime as dt
import re
import time

import requests
from bs4 import BeautifulSoup

from ..filtro import _frase_para_regex, normalizar
from ..modelos import Achado

ORIGEM = "https://www.diariomunicipal.com.br"
BASES_PADRAO = ("amp",)  # só o Paraná, igual ao coletor original — usado quando cfg não tem 'sigpub_bases'

# Bases extras (além da primeira/prioritária) processadas por execução, em
# rodízio — ver docstring do módulo para a conta de tempo por consulta.
EXTRAS_POR_EXECUCAO_PADRAO = 1

# UF confirmada ao vivo (dropdown "Município (Entidade)" do formulário de
# busca de cada base) em 13.8.2026 — só as bases verificadas entram aqui;
# uma base ausente deste dicionário fica com uf="" (fail-closed, nunca
# chuta). "fgm" é GO de verdade, não SE (ver docstring do módulo).
UF_DA_BASE = {
    "amp": "PR",
    "famurs": "RS",
    "amupe": "PE",
    "agm": "GO",
    "aprece": "CE",
    "ama": "AL",
    "famep": "PA",
    "arom": "RO",
    "femurn": "RN",
    "aam": "AM",
    "amr": "RR",
    "famup": "PB",
    "fgm": "GO",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9",
}
TENTATIVAS = 3
JANELA_MAX_DIAS = 179  # o formulário rejeita intervalo de datas >= 6 meses
LIMITE_ITENS = 80  # por base, por execução
POR_PAGINA = 11  # observado no datatable do SIGPub

# Caminho completo (com a base do próprio href, não a base pesquisada — ver
# docstring) para reconstruir a URL e servir de chave de dedup.
RX_CAMINHO_LOAD = re.compile(r"/[A-Za-z0-9_-]+/load/[0-9A-Fa-f]+")
RX_TOKEN = re.compile(r'name="busca_avancada\[_token\]"\s+value="([^"]*)"')
RX_ENTIDADE_MUNICIPIO = re.compile(
    r"^(?:PREFEITURA(?:\s+MUNICIPAL)?\s+DE|MUNIC[ÍI]PIO\s+DE|C[ÂA]MARA(?:\s+MUNICIPAL)?\s+DE)\s+(.+)$",
    re.IGNORECASE,
)


def _get_com_retry(sessao, url, params=None):
    for tentativa in range(1, TENTATIVAS + 1):
        try:
            resp = sessao.get(url, params=params, headers=HEADERS, timeout=45)
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            if tentativa == TENTATIVAS:
                raise
            print(f"[sigpub] aviso: requisição falhou ({e!r}), tentativa {tentativa}/{TENTATIVAS}")
            time.sleep(3 * tentativa)


def _sessao_e_token(base):
    sessao = requests.Session()
    resp = _get_com_retry(sessao, f"{ORIGEM}/{base}/pesquisar")
    m = RX_TOKEN.search(resp.text)
    if not m:
        raise RuntimeError(f"token CSRF não encontrado no formulário de busca do SIGPub (base '{base}')")
    return sessao, m.group(1)


PREPOSICOES_MINUSCULAS = {"de", "do", "da", "dos", "das", "e"}


def _municipio_de_entidade(entidade):
    m = RX_ENTIDADE_MUNICIPIO.match(entidade.strip())
    if not m:
        return ""
    nome = m.group(1).strip()
    if not nome.isupper():
        # já vem com caixa correta (a maioria das bases, ex. amp/agm): não
        # mexe, para não estragar preposição minúscula de verdade — .title()
        # cego virava "Aparecida Do Rio Doce" e "Foz Do Iguaçu" (achados reais
        # de agm/PR, 13.8.2026), quando a fonte já escreve "do"/"de" certo
        return nome
    # entidade em CAIXA ALTA (caso real: base amr/RR, "CAMARA MUNICIPAL DE
    # UIRAMUTA") — sem reformatar, o município saía todo maiúsculo
    return " ".join(
        p.lower() if p.lower() in PREPOSICOES_MINUSCULAS else p.capitalize()
        for p in nome.split()
    )


def _linhas_resultado(html):
    sopa = BeautifulSoup(html, "html.parser")
    tabela = sopa.find("table", id="datatable")
    if not tabela or not tabela.find("tbody"):
        return []
    linhas = []
    for tr in tabela.find("tbody").find_all("tr"):
        celulas = tr.find_all("td")
        if len(celulas) < 4:
            continue
        link = celulas[0].find("a")
        m_caminho = RX_CAMINHO_LOAD.search(link["href"]) if link and link.get("href") else None
        if not m_caminho:
            continue
        linhas.append(
            {
                "caminho": m_caminho.group(0),  # ex.: "/amp/load/0249EEE1" — vem do href, não da base pesquisada
                "entidade": celulas[0].get_text(strip=True),
                "titulo": celulas[1].get_text(strip=True),
                "orgao": celulas[2].get_text(strip=True),
                "data": celulas[3].get_text(strip=True),  # dd-mm-aaaa
            }
        )
    return linhas


def _data_iso(data_br):
    try:
        return dt.datetime.strptime(data_br, "%d-%m-%Y").strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return ""


def _texto_materia(sessao, caminho):
    resp = _get_com_retry(sessao, f"{ORIGEM}{caminho}")
    sopa = BeautifulSoup(resp.text, "html.parser")
    corpo = sopa.find(id="materia")
    return corpo.get_text(" ", strip=True) if corpo else sopa.get_text(" ", strip=True)


def _melhor_trecho(texto, rx_cargos):
    """~300 chars ao redor da 1ª frase de cargo achada no texto integral, senão o início dele."""
    t = normalizar(texto)
    for rx in rx_cargos:
        m = rx.search(t)
        if m:
            ini = max(0, m.start() - 150)
            return " ".join(texto[ini : m.end() + 150].split())[:300]
    return " ".join(texto.split())[:300]


def _coletar_base(base, cfg, cursor_base, desde_padrao, hoje, rx_cargos):
    """Coleta uma única base do SIGPub. Levanta RuntimeError/RequestException
    quando a base falha POR INTEIRO (token, ou todas as frases sem resposta) —
    quem chama decide se isso derruba a execução (ver `coletar`); em qualquer
    caso, cursor_base só é tocado no fim, depois de todas as frases rodarem,
    então uma falha total não avança a data e a base tenta de novo do mesmo
    ponto na próxima vez que entrar no rodízio."""
    de = desde_padrao
    if cursor_base.get("ultima_data"):
        de = max(de, dt.date.fromisoformat(cursor_base["ultima_data"]))
    if (hoje - de).days > JANELA_MAX_DIAS:
        de = hoje - dt.timedelta(days=JANELA_MAX_DIAS)
        print(f"[sigpub:{base}] aviso: janela maior que {JANELA_MAX_DIAS} dias, limitada a partir de {de.isoformat()}")

    sessao, token = _sessao_e_token(base)
    url_pesquisar = f"{ORIGEM}/{base}/pesquisar"
    uf = UF_DA_BASE.get(base, "")  # base sem UF confirmada ao vivo: fica vazio, nunca chuta (regra fail-closed)

    achados, vistos, falhas = [], set(), []
    limite_atingido = False
    for frase in cfg["consultas_cargo"]:
        if limite_atingido:
            break
        params_base = {
            "busca_avancada[texto]": frase,
            "busca_avancada[dataInicio]": de.strftime("%d/%m/%Y"),
            "busca_avancada[dataFim]": hoje.strftime("%d/%m/%Y"),
            "busca_avancada[_token]": token,
        }
        pagina = 1
        try:
            while True:
                params = dict(params_base)
                if pagina > 1:
                    params["busca_avancada[page]"] = pagina
                resp = _get_com_retry(sessao, url_pesquisar, params=params)
                linhas = _linhas_resultado(resp.text)
                if not linhas:
                    break
                for linha in linhas:
                    if linha["caminho"] in vistos:
                        continue
                    vistos.add(linha["caminho"])
                    if len(achados) >= LIMITE_ITENS:
                        print(f"[sigpub:{base}] aviso: limite de {LIMITE_ITENS} itens atingido, coleta interrompida")
                        limite_atingido = True
                        break
                    time.sleep(1)
                    try:
                        texto = _texto_materia(sessao, linha["caminho"])
                    except requests.RequestException as e:
                        print(f"[sigpub:{base}] aviso: matéria {linha['caminho']} inacessível ({e!r}), pulando")
                        continue
                    trecho = _melhor_trecho(texto, rx_cargos)
                    data_iso = _data_iso(linha["data"])
                    achados.append(
                        Achado(
                            fonte="sigpub",
                            titulo=f"{linha['entidade']} — {linha['titulo']} ({data_iso or linha['data']})",
                            url=f"{ORIGEM}{linha['caminho']}",
                            cargo_texto=texto[:3000],
                            orgao=linha["entidade"],
                            municipio=_municipio_de_entidade(linha["entidade"]),
                            uf=uf,
                            data_publicacao=data_iso,
                            detalhes={
                                "trecho": trecho,
                                "contexto_fraco": True,
                                "orgao_interno": linha["orgao"],
                                "sigpub_base": base,
                            },
                        )
                    )
                if limite_atingido:
                    break
                if len(linhas) < POR_PAGINA:
                    break
                pagina += 1
                time.sleep(1)
        except requests.RequestException as e:
            falhas.append(f"'{frase}': {e!r}")
            continue
        time.sleep(1)

    if falhas and len(falhas) == len(cfg["consultas_cargo"]):
        raise RuntimeError(f"todas as {len(falhas)} consultas à base '{base}' falharam; primeira: {falhas[0]}")
    if falhas:
        print(f"[sigpub:{base}] aviso: {len(falhas)} de {len(cfg['consultas_cargo'])} consultas falharam")

    cursor_base["ultima_data"] = hoje.isoformat()
    return achados


def coletar(cfg, cursor, desde_padrao):
    bases = list(cfg.get("sigpub_bases") or BASES_PADRAO)
    if not bases:
        return []
    hoje = dt.date.today()
    rx_cargos = [_frase_para_regex(c) for c in cfg["consultas_cargo"]]

    # a primeira base é a prioritária: roda toda execução (é o que preserva,
    # sem quebrar, a cobertura de hoje quando a lista tem só o Paraná). As
    # demais giram em rodízio, posição guardada no cursor.
    extras_disponiveis = bases[1:]
    extras_por_execucao = max(
        0, min(len(extras_disponiveis), cfg.get("sigpub_extras_por_execucao", EXTRAS_POR_EXECUCAO_PADRAO))
    )
    indice = cursor.get("proximo_extra", 0) % len(extras_disponiveis) if extras_disponiveis else 0
    extras_escolhidas = [
        extras_disponiveis[(indice + i) % len(extras_disponiveis)] for i in range(extras_por_execucao)
    ]
    escolhidas = [bases[0]] + extras_escolhidas

    achados = []
    bases_ok = 0
    for base in escolhidas:
        # dict local (não setdefault direto em cursor): uma base que nunca
        # teve sucesso não deixa entrada vazia sobrando no cursor persistido
        # — cursor[base] só é gravado quando _coletar_base chega ao fim.
        cursor_base = cursor.get(base, {})
        try:
            achados.extend(_coletar_base(base, cfg, cursor_base, desde_padrao, hoje, rx_cargos))
        except Exception as e:  # noqa: BLE001 — uma base fora do ar não pode travar as outras
            print(f"[sigpub] aviso: base '{base}' fora do ar nesta execução ({e!r}), pulando (cursor preservado)")
            continue
        cursor[base] = cursor_base
        bases_ok += 1

    if extras_disponiveis:
        cursor["proximo_extra"] = (indice + extras_por_execucao) % len(extras_disponiveis)

    if escolhidas and bases_ok == 0:
        raise RuntimeError(f"todas as {len(escolhidas)} bases do SIGPub processadas nesta execução falharam")
    return achados
