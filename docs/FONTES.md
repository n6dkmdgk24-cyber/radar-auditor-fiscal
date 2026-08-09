# De onde vêm os concursos — e o que ainda falta cobrir

Resposta à pergunta do Danilo (6.8.2026): *"de onde esses sites de concursos
tiram os concursos? qual é a base de dados que eles usam?"*

## Não existe uma base única

A resposta curta é decepcionante: **não há base de dados nacional de concursos
públicos**. Nenhum órgão federal consolida editais municipais. PCI Concursos e
Concursos no Brasil não consultam um cadastro — eles montam o cadastro deles,
por três caminhos:

1. **Redação humana lendo diários oficiais.** É o grosso do trabalho. O
   edital nasce publicado no diário oficial do próprio ente (ou no diário
   associativo do estado). Alguém lê, resume e publica a notícia.
2. **Assessoria das bancas.** Instituto/fundação organizadora manda o release
   quando o edital sai — é publicidade gratuita para a inscrição paga. Por
   isso a notícia aparece no portal quase junto com a abertura das inscrições.
3. **Rede de leitores.** Candidatos avisam os portais sobre editais da própria
   cidade.

Ou seja: o PCI é rápido porque tem redação e recebe release, não porque tem
acesso a algo que nós não temos. O que ele tem de estrutural é **volume de
gente**.

## O que o radar já faz

O radar **já bebe nas fontes primárias**, não só nos portais:

| Fonte | O que é | Situação |
|---|---|---|
| `qd` | API do Querido Diário (ONG Open Knowledge) — diários municipais raspados e indexados | funcionando, busca por frase |
| `sigpub` | Diário Oficial dos Municípios do Paraná (AMP/SIGPub) | funcionando |
| `domsc` | Diário Oficial dos Municípios de Santa Catarina | funcionando |
| `dou` | Diário Oficial da União, Seção 3 | funcionando |
| `selecao` | 6 bases de bancas na plataforma selecao.net.br | **403 anti-bot nos IPs do GitHub Actions** desde 29.7 (local funciona) |
| `pci`, `cnb` | portais de notícia | funcionando, hoje respondem por ~90% das descobertas |

A dependência dos portais é real e é o ponto fraco: se o PCI mudar o sitemap,
o radar perde a maior parte do fluxo.

## O que dá para acrescentar, em ordem de retorno

### 1. SIGPub dos outros estados (barato e imediato)
O `diariomunicipal.com.br` hospeda **21 diários associativos** (AMP/PR,
AMUPE/PE, FAMURS/RS, AMM/MG, APPM/PI, AMA/AL, FAMEP/PA, AGM/GO, AROM/RO,
FEMURN/RN, APRECE/CE, AMUPE, AMURC, AAM, ...). O coletor `sigpub` já lê o do
Paraná; apontá-lo para mais bases é **configuração, não código novo**.
Priorizando por proximidade: **SC (já temos por outro coletor), SP (não usa
SIGPub — usa o e-SAJ/portais próprios), MS (`/ms/`), RS (FAMURS)**.

Ganho: pega o edital **no dia da publicação**, antes de o portal noticiar, e
independe de a redação do PCI achar o município relevante.

### 2. Portais das bancas que realmente aparecem no nosso painel
Levantei os domínios de inscrição dos 56 cartões atuais. As recorrentes são
IBAM (`ibam-concursos.org.br` e `ibamsp-concursos.org.br`), Fundação FAFIPA,
Consulplan, Consulpam, IBGP, IBEPP, INBRASP, ITAME, INEPAM, AvançaSP, Objetivas,
VUNESP, Selecon, Unioeste, Access, INDEC. Testei o acesso automatizado:
FAFIPA, IBAM-SP, AvançaSP, Consulplan e Consulpam respondem 200 a um cliente
comum; VUNESP devolve 403 (mesmo perfil anti-bot do selecao.net.br).

Ganho: a banca publica **antes** do portal de notícias e traz o **PDF do
edital**, que é a fonte da verdade sobre atribuição do cargo — exatamente o
que faltou para decidir se o "Fiscal Municipal" de Ipuã era tributário (foi
preciso abrir o edital à mão para descobrir que é híbrido).

Custo: um coletor por banca, cada um com seu HTML. Sugiro começar por **FAFIPA
(PR, aparece 3× no painel) e IBAM (SP/nacional, 4×)**.

### 3. Link do edital no cartão
Já entregue nesta rodada em parte: o cartão traz o **site de inscrição**
quando o artigo o sustenta (44 dos 56 cartões). O PDF do edital em si só é
alcançável indo à página da banca — é o que o item 2 resolve.

### 4. INLABS (XML oficial do DOU)
A Imprensa Nacional distribui o DOU em XML pelo INLABS, com cadastro gratuito.
Substituiria a raspagem do `in.gov.br` (que oscila). Vale para concursos
federais; para municipal não ajuda.

## O que eu não recomendo

- **Raspar o PCI mais fundo** (páginas de listagem além do sitemap): aumenta a
  dependência de quem já é o ponto único de falha.
- **Comprar/assinar agregador**: o que se paga é a redação humana, e o radar
  precisa de precisão de cargo, não de volume de manchete.
- **IA para ler edital**: vetada por decisão sua, e a experiência de 30.7 a
  4.8 mostrou o custo de depender disso.

## Recomendação

Ordem que eu seguiria, se você aprovar:

1. **SIGPub multi-estado** (config + pequeno ajuste no coletor) — maior ganho
   por menor esforço, e ataca a dependência do PCI na raiz.
2. **Coletor da FAFIPA e do IBAM** — traz o edital em PDF e antecipa o aviso.
3. **Decidir o caso `selecao`**: o 403 nos IPs do GitHub Actions continua sem
   solução; as opções seguem sendo proxy, execução local por cron, ou aceitar
   a cobertura indireta via PCI.
