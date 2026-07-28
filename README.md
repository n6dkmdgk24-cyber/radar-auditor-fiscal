# Radar Auditor Fiscal

Monitor pessoal de concursos públicos para carreiras de **fiscalização tributária** (Receita Federal, SEFAZ estaduais, fiscos municipais/ISS) e de **controle** (TCU, TCEs, CGU, controladorias), nas 3 esferas e no Brasil inteiro — com atenção especial aos concursos **municipais**, que os grandes portais não avisam.

Roda 2× por dia no GitHub Actions, de graça, e avisa por **Telegram** (push), **e-mail** (digest) e um **painel** no GitHub Pages.

## Como funciona

```
coletores  ->  filtro por nomenclatura  ->  dedupe  ->  telegram + e-mail + painel
```

Camadas de coleta (v1) — nenhuma fonte sozinha basta; elas se sobrepõem de propósito:

| Fonte | O que cobre | Método |
|---|---|---|
| `qd` Querido Diário | Diários oficiais de ~955 municípios (todos os estados) | API pública, busca por frase |
| `pci` PCI Concursos | Notícias de concursos, inclusive prefeituras pequenas | Diff do sitemap de notícias |
| `cnb` Concursos no Brasil | Notícias de concursos, inclusive municipais | RSS |
| `dou` DOU Seção 3 | Editais federais (RFB, CGU, TCU...) | Busca do in.gov.br |

O filtro classifica cada achado em **tributário**, **controle** ou **conferir** (ambíguos como "Fiscal Municipal" — nunca descartados em silêncio) usando nomenclaturas levantadas em editais reais de 2019–2026. Homônimos (Auditor-Fiscal do Trabalho, fiscal de obras/posturas/sanitário/ambiental/trânsito) são mascarados antes do teste.

Regra de confiança por fonte: os selos fortes (Tributário/Controle) vêm das fontes de **notícia** (PCI, CNB, DOU), cujos títulos dizem explicitamente que um concurso abriu. Itens do **Querido Diário** são texto bruto de diário oficial (que mistura editais, autos de infração, nomeações e assinaturas de servidores) e por isso entram **sempre como "Conferir"**, com o trecho casado exibido e o marcador "🔎 possível abertura" quando há indício de edital de abertura perto do cargo — sinal de triagem, não veredito.

### Classificador com IA (opcional)

Se o secret `ANTHROPIC_API_KEY` existir, cada item "Conferir" passa por uma revisão final com **Claude Haiku 4.5** (saída estruturada em JSON), que decide: **abertura** na área-alvo (recupera o selo forte, com marcador 🤖), **andamento** (convocação/gabarito/nomeação — continua em Conferir) ou **irrelevante** (sai dos avisos, mas fica registrado em `data/descartados.json` — nada some em silêncio). Qualquer erro de API mantém o item em Conferir (fail-open); sem a chave, o radar funciona exatamente como antes.

Configuração: criar uma chave dedicada em [console.anthropic.com](https://console.anthropic.com) (Settings → API Keys), **definir um limite mensal de gasto** no console (ex.: US$ 5) e cadastrá-la como secret `ANTHROPIC_API_KEY` no repositório. Custo típico no volume deste radar (~5–15 itens/dia, ~1.400 tokens cada, a US$ 1/M de entrada e US$ 5/M de saída): **centavos de real por dia**. A chave nunca aparece no código nem nos arquivos commitados — vive só nos Actions Secrets (cifrados; mascarados nos logs).

## Uso local

```bash
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
./.venv/bin/python -m radar.main --dry-run              # não notifica nem salva estado
./.venv/bin/python -m radar.main --backtest-dias 30     # coleta retroativa (implica dry-run)
./.venv/bin/python -m radar.main --fontes qd,pci        # restringe fontes
./.venv/bin/python -m radar.main                        # execução real
```

## Configuração (uma vez)

1. **Repositório**: `gh auth login`, depois criar o repo público e dar push (`gh repo create radar-auditor-fiscal --public --source . --push`).
2. **Bot do Telegram**: falar com o @BotFather → `/newbot` → guardar o token. Criar um grupo com o bot + os dois interessados; pegar o `chat_id` acessando `https://api.telegram.org/bot<TOKEN>/getUpdates` após mandar uma mensagem no grupo (o id de grupo começa com `-`).
3. **Secrets** no repo (Settings → Secrets and variables → Actions): `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID` e, para o e-mail, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `EMAIL_PARA` (destinatários separados por vírgula) e opcionalmente `EMAIL_DE`. Gmail: usar senha de app com `smtp.gmail.com` porta 587.
4. **GitHub Pages**: Settings → Pages → Deploy from a branch → `main` / pasta `/docs`.
5. **Primeira execução**: aba Actions → workflow `radar` → Run workflow. As duas execuções diárias (08h00 e 19h30 de Brasília) já ficam agendadas.

## Limitações honestas

- **"Todos" absoluto não existe.** O Querido Diário cobre 955 dos 5.570 municípios; a rede principal para municipais são os portais PCI + Concursos no Brasil (cobertura comprovadamente ampla, mas editorial — não é diário oficial). Município minúsculo que só publica em mural ou jornal impresso escapa de qualquer sistema automatizado.
- O in.gov.br oscila com acesso automatizado; o coletor tenta com retries e, se falhar, o próprio run avisa no Telegram. Fallback futuro: INLABS (XML diário da Imprensa Nacional, cadastro gratuito).
- Scrapers quebram quando sites mudam. Falha de coletor nunca é silenciosa: chega aviso no Telegram e o log fica no Actions.
- v2 planejada: diários das associações municipalistas (AMP-PR via SIGPub, DOM/SC) e scraper único das bancas na plataforma `selecao.net.br` (FAFIPA, AOCP, FUNDEP, Quadrix, IDECAN etc.).

## Redundância externa recomendada (5 min)

Independente deste radar: cadastrar alerta gratuito no Enter Concursos (cargo/região) e entrar no canal @gcofiscais (Gran Fiscais) no Telegram.
