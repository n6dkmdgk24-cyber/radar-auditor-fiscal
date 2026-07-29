"""Coletor do Diário Oficial dos Municípios de Santa Catarina (DOM/SC, CIGA).

Busca pública em https://diariomunicipal.sc.gov.br/ (framework Yii, roteamento
via r=). O formulário de pesquisa avançada (r=site/consulta) é um POST que o
Yii responde com 302 para GET ?r=site/index&q=<frase> data:[<início>T03:00:00Z
TO <fim>T02:59:59Z] — horário embutido em UTC correspondente a 00:00/23:59:59
em BRT (Brasil não tem horário de verão desde 2019, por isso o deslocamento
fixo de -03:00). Testado e confirmado via curl em 29.7.2026.

O campo "categoria" do formulário (a opção existente chama-se "Edital", não
"Editais") NÃO restringe de fato os resultados: uma busca com
categoria:"Concursos" devolveu, nos 10 primeiros itens, apenas 1 realmente
da categoria Concursos — os demais eram Decretos, Leis, Portarias etc. Por
isso a busca aqui é sempre livre, sem esse parâmetro (o próprio texto de
confirmação da busca ecoa a categoria pedida, mas o motor não filtra por
ela). Paginação por &AtoASolrDocument_page=N, 10 itens por página; a partir
de uma certa profundidade (~página 20) o motor devolve 404 mesmo havendo
mais publicações no contador — tratado aqui como fim da paginação, não erro.

O RSS do site (view=rss) foi descartado: a descrição de cada item é sempre
o cabeçalho do documento (CNPJ, endereço), nunca o trecho com o termo
buscado, diferente da página HTML de resultados, que devolve o excerto em
torno do termo pesquisado — é dessa página que vem o trecho exigido.
"""

import datetime as dt
import html as html_lib
import re
import time

import requests

from ..filtro import _frase_para_regex, normalizar
from ..modelos import Achado

BASE = "https://diariomunicipal.sc.gov.br/"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}
TAM_PAGINA = 10
LIMITE_ITENS = 80
TENTATIVAS = 3
TAMANHO_TRECHO = 300

RX_TAGS = re.compile(r"</?[^>]+>")
# Cada resultado é <h4><a href="/atos/ID">TÍTULO</a></h4><span class="quiet">
# N.º ID - DD/MM/AAAA [HH:MM] - [<span class="label">Autopublicação</span> -]
# CATEGORIA - ENTIDADE<br></span> ... <p>TRECHO (até o próximo <h4> ou fim).
# O <p> nunca é fechado (a página usa "<p/>" solto) e o link antes dele varia
# (às vezes tem "[Imprimir Extrato]" entre o link do original e o <p>), por
# isso o meio do padrão pula tudo até o primeiro "<p>" em vez de exigir um
# link específico.
RX_ITEM = re.compile(
    r'<h4><a href="(/atos/\d+)">(.*?)</a></h4>'
    r'<span class="quiet">N\.º\s*\d+\s*-\s*(\d{2}/\d{2}/\d{4})(?:\s+\d{2}:\d{2})?\s*-\s*'
    r'(?:<span class="label">[^<]*</span>\s*-\s*)?'
    r'([^<]*?)\s*-\s*([^<]*?)<br></span>'
    r'.*?<p>'
    r'(.*?)(?=<h4><a href="/atos/|$)',
    re.DOTALL,
)
RX_MUNICIPIO = re.compile(
    r"^(?:Prefeitura(?:\s+Municipal)?|C[aâ]mara(?:\s+Municipal)?(?:\s+de\s+Vereadores)?)\s+de\s+(.+)$",
    re.IGNORECASE,
)


def _limpar(texto):
    return " ".join(RX_TAGS.sub(" ", html_lib.unescape(texto)).split())


def _janela(de, ate):
    inicio = f"{de.isoformat()}T03:00:00Z"
    fim = f"{(ate + dt.timedelta(days=1)).isoformat()}T02:59:59Z"
    return f"data:[{inicio} TO {fim}]"


def _buscar(params):
    """GET com retry (3 tentativas, backoff). 404 é fim de paginação, não erro."""
    for tentativa in range(1, TENTATIVAS + 1):
        try:
            resp = requests.get(BASE, params=params, headers=HEADERS, timeout=30)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as e:
            if tentativa == TENTATIVAS:
                raise
            print(f"[domsc] aviso: busca falhou ({e!r}), tentativa {tentativa}/{TENTATIVAS}")
            time.sleep(5 * tentativa)


def _recorte(texto_limpo, rx_frase, tamanho=TAMANHO_TRECHO):
    """~300 chars em torno da 1ª ocorrência da frase buscada (já em texto_limpo)."""
    m = rx_frase.search(normalizar(texto_limpo))
    if not m:
        return texto_limpo[:tamanho]
    meio = (m.start() + m.end()) // 2
    ini = max(0, meio - tamanho // 2)
    return texto_limpo[ini : ini + tamanho]


def _municipio_de(entidade):
    m = RX_MUNICIPIO.match(entidade)
    return m.group(1).strip() if m else ""


def coletar(cfg, cursor, desde_padrao):
    hoje = dt.date.today()
    de = desde_padrao
    if cursor.get("ultima_data"):
        de = max(de, dt.date.fromisoformat(cursor["ultima_data"]))

    achados, vistos = [], set()
    limite_atingido = False
    for frase in cfg["consultas_cargo"]:
        if limite_atingido:
            break
        rx_frase = _frase_para_regex(frase)
        q = f'"{frase}" {_janela(de, hoje)}'
        pagina = 1
        while True:
            corpo = _buscar({"r": "site/index", "q": q, "AtoASolrDocument_page": pagina})
            time.sleep(1)
            if corpo is None:
                break  # fim da paginação (limite de profundidade do motor de busca)
            itens = RX_ITEM.findall(corpo)
            if not itens:
                break
            for url_rel, titulo_bruto, data_br, categoria, entidade, snippet_bruto in itens:
                url = BASE.rstrip("/") + url_rel
                if url in vistos:
                    continue
                vistos.add(url)
                titulo_ato = _limpar(titulo_bruto)
                entidade_limpa = _limpar(entidade)
                snippet = _limpar(snippet_bruto)
                data_iso = dt.datetime.strptime(data_br, "%d/%m/%Y").strftime("%Y-%m-%d")
                achados.append(
                    Achado(
                        fonte="domsc",
                        titulo=f"{entidade_limpa} — {titulo_ato} ({data_iso})",
                        url=url,
                        cargo_texto=f"{titulo_ato}\n{snippet}",
                        orgao=entidade_limpa,
                        municipio=_municipio_de(entidade_limpa),
                        uf="SC",
                        data_publicacao=data_iso,
                        detalhes={
                            "categoria": _limpar(categoria),
                            "trecho": _recorte(snippet, rx_frase),
                            "contexto_fraco": True,
                        },
                    )
                )
                if len(achados) >= LIMITE_ITENS:
                    print(f"[domsc] aviso: limite de {LIMITE_ITENS} itens atingido, busca interrompida")
                    limite_atingido = True
                    break
            if limite_atingido or len(itens) < TAM_PAGINA:
                break
            pagina += 1

    cursor["ultima_data"] = hoje.isoformat()
    return achados
