# 6 — Catch Up: recuperar o que você perdeu

> **Documento:** `BABBLE-MAN-001` — capítulo 6 — v1.0 — aprovado
> **Software descrito:** Babble 0.3.0 · **Data:** 2026-11-11 · **Pai:** [Manual do Usuário do Babble](README.md)

O `Catch Up` é a resposta do Babble para a pergunta que toda rede social deixa para trás: *o que eu perdi?*

## 6.1 O que é

O `Catch Up` é uma **timeline separada**, que não é a sua `Home`. A diferença está em duas decisões que ele toma por você:

1. **Ele volta no tempo.** Você escolhe uma janela — a última hora, as últimas 6 horas, desde o seu último catch-up — e o Babble percorre a sua `Home` para trás até lá.
2. **Ele ranqueia.** O que encontra não vem em ordem cronológica: vem ordenado por quanto vale a sua atenção.

O resultado é gravado como um **instantâneo** (*snapshot*): um arquivo local, por conta, que não muda mais. Você pode voltar a ele dias depois e ver exatamente o mesmo conjunto de publicações, mesmo que o feed já tenha seguido em frente.

Tudo é calculado no seu computador. O Babble fala com o servidor apenas pela API de cliente — o mesmo caminho que usa para ler a `Home`.

O lugar `Catch Up` fica logo depois de `Home` na barra lateral. Se você não o quer à vista, Preferências → Timelines → Sidebar → `Catch Up` o esconde.

## 6.2 Fazer um catch-up

**Objetivo:** ver um resumo do que você deixou de ler.

**Pré-condições:** você está conectado à sua conta.

1. Clique em `Catch Up` na barra lateral.
2. A página inicial mostra o título `Catch Up` e a descrição *"A separate timeline for your followings, offering a high-level view at a glance"*.
3. Escolha o período no botão do canto superior direito (ou no menu inicial): `Last hour`, `Last 3 hours`, `Last 6 hours`, `Last 12 hours`, `Last 24 hours`, `Since the last catch-up` ou `As far back as possible`.
4. Clique em `_Catch Up`.

**Resultado:** o Babble percorre a timeline e mostra o progresso — `Scanning timeline…`, com a contagem `"{n} posts evaluated"` e o intervalo `({n} h back)`. Ao terminar, aparecem os resultados.

**Se der errado:** o botão `_Cancel` interrompe a varredura e mostra o que já foi juntado. O catch-up consome até cerca de 30 requisições ao servidor, e servidores com limite de taxa podem responder `The server is limiting requests, so this catch-up is incomplete. Try again in {n} minutes.`

## 6.3 A grade de destaques

O primeiro lugar da página é uma grade de *destaques*: um conjunto de cartões de tamanho variado, escolhido pelo ranqueamento. Cada cartão é um tipo diferente de informação:

| Tipo de cartão | O que mostra |
|---|---|
| Publicação | O post, com as contagens de respostas, impulsos e favoritos, e quem impulsionou (`{conta} boosted`, `{conta} and {n} other boosted`). |
| Notícia | Um link em alta, com o rótulo `News` e um botão `Open in Browser`. |
| Hashtag | Uma tag em alta, com o rótulo `Hashtag`, o nome `#tag`, a linha de uso e a marca `Following` se você segue a tag. |

Publicações que estão em alta no momento recebem o selo `Trending`.

O botão `Show More` expande a grade — quantas páginas aparecem é lembrado para cada instantâneo.

## 6.4 A lista de todas as publicações

Abaixo da grade, a seção `All posts` lista **todas** as publicações do instantâneo, uma por linha, de forma compacta: avatar, nome, uma linha de resumo (o texto do aviso de conteúdo, quando há um; o corpo, quando não), até três miniaturas, as contagens de respostas, impulsos e favoritos, a hora relativa e a marca `Thread` nas publicações que iniciaram um fio.

Clicar em uma linha abre o fio.

Acima da lista há dois controles:

- **Uma fileira de pontos** — um ponto por publicação, em ordem cronológica, mostrando a distribuição no tempo. Os pontos que passam pelos filtros atuais ficam destacados. O leitor de tela anuncia `Posts over time` e `"{mostrados} of {total}"`.
- **Uma fileira de avatares** — os autores, com a contagem de publicações de cada um, do mais ativo para o menos.

## 6.5 Filtrar e ordenar

O botão de opções da lista (`List Options`) abre um painel com três controles:

### `Category`

Filtra por tipo de publicação. As categorias são `All`, `Original`, `Replies`, `Boosts`, `Quotes`, `Followed tags`, `Groups` e `Filtered`. Cada uma mostra a contagem.

### `Author`

Um seletor com `All authors` e, abaixo, cada autor com a contagem de publicações.

### `Sort by`

| Ordenação | O que significa |
|---|---|
| `Time` | Cronológica. É a padrão, do mais antigo para o mais novo. |
| `Replies` | Quantas respostas a publicação atraiu. |
| `Likes` | Quantos favoritos. |
| `Boosts` | Quantos impulsos. |
| `Density` | Concentração de conversa — quantas pessoas falaram sobre o mesmo assunto. |
| `Relevance` | O que o ranqueamento achou mais relevante. |

Duas chaves alternam o sentido: `Ascending` e `Descending`. E há o interruptor `Group by author`, que junta as publicações do mesmo autor.

O botão `_Reset Filters` volta tudo ao padrão.

Cada mudança é confirmada por um toast que descreve o que mudou — por exemplo, *"Showing boosts, most likes first, grouped by authors"*.

## 6.6 Os instantâneos anteriores

O menu de período tem uma seção `Previous catch-ups`, com os três últimos instantâneos da conta. Cada um é rotulado `{início} – {fim} · {n} publicações}` e tem um botão de remoção (`Remove This Catch-Up`).

Remover exige confirmação: `Remove This Catch-Up?` — *"It will be deleted from this device."*

Abrir um instantâneo antigo não refaz a varredura: ele simplesmente reabre o arquivo já gravado.

## 6.7 Os avisos que o Catch Up pode exibir

| Faixa | O que significa |
|---|---|
| `Covers {intervalo} ({n} posts)` | A cobertura foi menor do que a janela pedida — o servidor entrega no máximo 800 publicações da `Home`. |
| `Only {n} post in {intervalo}` | Houve pouco movimento no período. A faixa oferece `Catch up on 24 hours`. |
| `The server is limiting requests…` | A varredura foi interrompida pelo limite de taxa do servidor. |
| `This catch-up could not be saved…` | O instantâneo não pôde ser gravado e não estará lá da próxima vez. |

O rodapé do menu de período avisa sobre o limite de 800: *"Your server might only show a maximum of 800 posts in the Home timeline regardless of the time range."*

## 6.8 Como o ranqueamento funciona

Esta seção é conceitual: ela explica o critério para que você entenda o que está no topo da grade.

O ranqueamento é uma **porta, função por função,** do modelo "For You" do Elk — um cliente Mastodon cuja ordenação é uma transcrição do ranqueador de código aberto da X (o `home-mixer` do `x-algorithm`). O Babble não treina modelo nenhum: ele calcula, no seu computador, um conjunto de *cabeças de ação* (responder, favoritar, impulsionar, permanecer na tela, velocidade de interação, alcance, volume do autor) e as combina com os pesos que a X usa em produção.

Três regras merecem menção:

- **Seguidor mútuo.** Uma publicação original de alguém que você segue e que segue você ganha um impulso forte na cabeça de respostas.
- **Vídeo.** O sinal de qualidade de vídeo só conta para vídeos com mais de 10 segundos, e para autores com poucos seguidores.
- **Diversidade.** Depois da pontuação, o Babble reordena para não mostrar cinco respostas do mesmo fio nem o mesmo autor o tempo todo.

A ordenação por `Relevance` na lista de todas as publicações usa exatamente essa pontuação. As demais ordenações — `Replies`, `Likes`, `Boosts`, `Density` — são contagens diretas, sem modelo.

## 6.9 Resumo

| Pergunta | Resposta |
|---|---|
| Perdi mensagens, como recupero? | `Catch Up` na barra lateral. |
| Quanto tempo ele volta? | De 1 a 24 horas, desde o último catch-up, ou o máximo que o servidor der. |
| Ele muda com o tempo? | Não. O resultado é gravado como instantâneo. |
| Onde fica guardado? | No seu computador, por conta. |
| Custa requisições ao servidor? | Sim, até cerca de 30 por catch-up. |
