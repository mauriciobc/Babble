# 2 — Leitura: timelines, fios, perfis e busca

> **Documento:** `BABBLE-MAN-001` — capítulo 2 — v1.0 — aprovado
> **Software descrito:** Babble 0.3.0 · **Data:** 2026-11-11 · **Pai:** [Manual do Usuário do Babble](README.md)

Este capítulo é o centro do manual: as seis respostas à pergunta "de onde vem o que eu leio".

## 2.1 A barra lateral

A barra lateral é a lista de destinos do aplicativo. Cada item é um *lugar* (*place*): um destino nomeado, com uma página que o Babble constrói na primeira vez em que você abre e guarda durante a sessão, para que a rolagem e o conteúdo carregado continuem onde estavam.

| Lugar | O que mostra |
|---|---|
| `Home` | As publicações das contas que você segue, mais os impulsos delas. |
| `Catch Up` | O resumo ranqueado do que você deixou de ler. Ver [capítulo 6](06-catch-up.md). |
| `Notifications` | Menções, impulsos, favoritos, seguidores e o resto. Ver [capítulo 3](03-notificacoes-e-mensagens.md). |
| `Conversations` | As mensagens diretas. Ver [capítulo 3](03-notificacoes-e-mensagens.md). |
| `Search` | Busca de contas, hashtags e publicações. |
| `Favorites` | As publicações que você favoritou. |
| `Bookmarks` | As publicações que você salvou. |
| `Hashtags` | As hashtags que você segue e as timelines de hashtags combinadas. |
| `Explore` | O que está em alta no seu servidor. |
| `Local` | A timeline pública **do seu servidor**. |
| `Federated` | A timeline pública de **todos os servidores** conhecidos. |
| `Lists` | As suas listas de contas. |
| `Pinned` | As listas e hashtags que você fixou, numa seção à parte. |

**Atalho para chegar lá:** **Ctrl+1** a **Ctrl+9** saltam entre `Home`, `Notifications`, `Conversations`, `Search`, `Favorites`, `Bookmarks`, `Hashtags`, `Explore` e `Local`.

### Como mudar o tamanho da barra lateral

**Objetivo:** ganhar espaço para a timeline, ou manter os rótulos à vista.

**Pré-condições:** a barra lateral está visível.

1. Clique no botão `Sidebar`, no rodapé da barra lateral.
2. Escolha um dos três modos:
   - `Full Sidebar` — ícones **e** rótulos.
   - `Icons Only` — uma faixa estreita de ícones de 64 pixels.
   - `Hidden` — a barra lateral some da tela.

**Resultado:** a mudança é imediata. Com a barra oculta, os botões `Sidebar`, `Main Menu`, `New Post` e `Notifications` passam a aparecer no cabeçalho de cada página — o mesmo conjunto de controles, no mesmo lugar.

**Se der errado:** em janelas estreitas (menos de 601 pixels), a barra lateral fecha sozinha para dar espaço ao conteúdo; **F9** a alterna.

### Como fixar uma lista ou uma hashtag na barra lateral

**Objetivo:** deixar uma lista ou hashtag sempre à mão.

**Pré-condições:** você já tem a lista ou a hashtag.

1. Abra a lista ou a hashtag.
2. Clique no botão de estrela, no cabeçalho da página. A dica dele é `Pin to Sidebar`.
3. Para desfazer, clique de novo no mesmo botão (`Unpin from Sidebar`).

**Resultado:** o item aparece numa seção `Pinned`, no fim da barra lateral. Uma lista fixada mostra o título real da lista; uma hashtag fixada mostra `#nome`.

**Se der errado:** se a estrela não aparecer no cabeçalho, a página ainda não terminou de carregar — volte para a `Home` e abra a lista de novo. Para desfazer, clique na mesma estrela (`Unpin from Sidebar`).

## 2.2 A timeline `Home`

`Home` é a timeline das publicações das contas que você segue. A ordem é cronológica, da mais recente para a mais antiga, e é a única página que atualiza ao vivo por padrão.

A rolagem não tem fim: ao chegar perto do fim, o Babble busca a página seguinte. Ao puxar a página para baixo no topo, ele recarrega na hora.

Quando chegam publicações novas enquanto você está rolado mais para baixo, aparece no topo o botão `N New Posts` (ou `N New Post`, no singular). Clicar nele leva ao topo.

### Opções que filtram a `Home`

Duas preferências mudam o que aparece na `Home`, e só nela:

- **Hidden Replies From Non-Follows** (`hide-replies-non-follows`) — esconde as respostas vindas de contas que você não segue. É a opção mais útil para quem quer a `Home` como uma conversa entre contas que acompanha.
- **Hidden Boosts on Home** (`hide-boosts-home`) — esconde os impulsos e deixa só o que as contas que você segue escreveram.

Uma terceira, **Remembered Reading Position** (`resume-reading-position`), faz a `Home` reabrir exatamente onde você parou, com um botão para as publicações mais novas. Ela vem ligada por padrão.

### Posição de leitura

Com a posição de leitura ligada, ao reabrir a `Home` o Babble rola até a publicação que você estava lendo. Um botão `N New Posts` aparece para você pular para o que chegou depois.

### Como recarregar a página

Há quatro caminhos, e todos fazem a mesma coisa:

| Caminho | Quando usar |
|---|---|
| Puxar a página para baixo no topo | Você está no topo e quer atualizar agora. |
| **Ctrl+R** ou **F5** | Você está em qualquer lugar da página. |
| `Reload`, pelo menu principal | O mesmo, pelo teclado, para quem prefere o menu. |
| Recarregamento automático | Preferências → Timelines → `Automatic Refresh`: `Off`, `Every 30 seconds`, `Every minute` ou `Every 5 minutes`. Vale enquanto a atualização ao vivo está desligada ou indisponível. |

## 2.3 Abrir um fio

**Objetivo:** ler uma publicação e as respostas a ela, com o contexto acima.

**Pré-condições:** você está em uma timeline ou em um perfil.

1. Clique na publicação. Ela abre sozinha na página do fio.
2. Leia de cima para baixo: o post original fica no centro, os ancestrais acima dele e as respostas aninhadas abaixo.
3. Use **Alt+Left** para voltar à página de onde você veio.

**Resultado:** a página do fio mostra o post original com mais contexto do que a timeline mostra. O cabeçalho da publicação indica a posição dentro do fio (`n/total`), o que ajuda a saber se você perdeu alguma coisa.

**Se der errado:** a página do fio mostra `Loading Thread` enquanto busca e `Could Not Load Thread` se falhar. Nesse caso, volte e tente de novo; se o servidor estiver sobrecarregado, espere alguns segundos.

## 2.4 Interagir com uma publicação

Cada publicação tem uma barra de ações na parte de baixo, com cinco botões:

| Botão | O que faz | Observações |
|---|---|---|
| `Reply` | Abre o compositor com uma resposta. | Preenche `@conta ` e herda a visibilidade do post original. Se o post tiver aviso de conteúdo, o compositor abre atrás do mesmo aviso. |
| `Boost` | Republica a publicação. | É um botão de dois estados: clicar de novo desfaz. Ao desfazer, aparece um toast `Boost removed` com `Undo`. Publicações diretas não podem ser impulsionadas. |
| `Quote` | Publica um comentário junto com a citação. | Só aparece quando o servidor permite e quando o post pode ser citado. |
| `Favorite` | Marca como favorito. | Privado. |
| `Bookmark` | Salva para ler depois. | Privado. |

As contagens (`12 boosts`, `4 favorites`) só aparecem com a preferência **Interaction Counters** ligada.

Quando **Post Actions on Timelines** está desligada — o padrão — a barra de ações some das timelines e os botões migram para o menu `More Options` da publicação, como uma linha de ícones. Nas páginas de fio, a barra aparece sempre.

### Antes de impulsionar

Duas permissões podem pedir a sua confirmação antes do impulso:

- **Advanced Boost Dialog** (`advanced-boost-dialog`) — abre a caixa `Boost With Visibility`, onde você escolhe quem vê o impulso: `Visible for all`, `Visible for all, but not in public timelines` ou `Visible for your followers only`. É onde você também decide entre `Boost` e `Quote`.
- **Missing Alt Text Boost Warning** (`boost-alt-text-reminder`) — se o post tem mídia sem descrição, o Babble avisa: *"Some of your followers might not be able to understand it."* Você pode marcar `Don't remind me again`.

### Responder a um post antigo

Respostas a publicações com mais de 90 dias pedem confirmação: `Reply to an Old Post?` — *"This post is {idade} old. You can still reply, but it may no longer be relevant."*. A preferência `Old Post Reply Warning` desliga esse aviso.

## 2.5 O menu de uma publicação

O botão `More Options`, no canto superior direito de cada publicação, abre um menu que se monta a cada abertura — o conteúdo depende do post e de quem você é. Os itens que aparecem:

### Links

| Item | O que faz |
|---|---|
| `View Post` | Abre o fio. |
| `Open in Browser` | Abre a URL da publicação no navegador. |
| `Copy Link` | Copia a URL. Para publicações privadas ou diretas, aparece antes uma confirmação, avisando que o link só mostra a publicação para quem foi mencionado (e, conforme a visibilidade, para os seus seguidores). |
| `View on Bluesky` | Só em publicações vindas do Bluesky pela ponte *Bridgy Fed*. |
| `View Stats` | Abre a aba com quem favoritou, quem impulsionou e quem citou. Não aparece em publicações diretas que não são suas. |
| `View Edit History` | Só se a publicação já foi editada. |

### Publicação

| Item | O que faz |
|---|---|
| `Translate` / `Show Original` | Traduz a publicação para o idioma da interface. `Show Original` volta ao texto original. |
| `Mute Conversation` / `Unmute Conversation` | Silencia o fio. Só nas suas publicações e nas que mencionam você. |
| `Pin to Profile` / `Unpin From Profile` | Fixa a publicação no seu perfil. Só nas suas publicações públicas ou não listadas. |
| `Who Can Quote` | Quem pode citar: `_Anyone`, `My Followers` ou `Nobody`. Só nas suas publicações. |
| `Remove My Post From Quote` | Retira a sua publicação de uma citação de outra pessoa. |

### Suas publicações

| Item | O que faz |
|---|---|
| `Edit` | Abre a publicação no compositor, com o texto e a mídia editáveis. |
| `Delete and Redraft` | Apaga a publicação e copia texto, anexos e enquete para um novo rascunho. |
| `Delete` | Apaga a publicação. Ela some na hora, e um toast `Post deleted` oferece `Undo` — a remoção só vai ao servidor quando o toast expira. |

### Publicações de outras pessoas

| Item | O que faz |
|---|---|
| `Mention {conta}` | Abre o compositor com a menção. |
| `Mute {conta}` | Silencia a conta. Um toast `Muted {conta}` oferece `Undo`. |
| `Block {conta}` | Bloqueia a conta, depois de uma confirmação. |
| `Report Post…` | Abre o diálogo de denúncia. |

Plugins ativos podem acrescentar itens próprios a este menu.

## 2.6 Avisos de conteúdo e publicações longas

### Avisos de conteúdo

Uma publicação com aviso de conteúdo esconde o texto atrás de uma barra com o aviso e um botão. O botão diz `Show Content` enquanto o texto está escondido e `Hide Content` depois de revelado; o texto aparece deslizando.

Por padrão, o conteúdo **já vem revelado**. Quem prefere o contrário desliga a preferência **Spoilers** (`show-spoilers`), em Preferências → Reading. O estado é lembrado por publicação: se você abriu uma, ela fica aberta.

O botão também serve para ocultar de novo, sem esperar o fim da rolagem.

### Publicações longas

Publicações que passam de 700 caracteres, ou de 14 quebras de linha, ou que são quase só links ou hashtags, são recolhidas. O botão `Show More` expande; `Show Less` recolhe de novo. A preferência **Collapsed Long Posts** (`collapse-long-posts`) controla o recolhimento; ela vem ligada.

### Traduzir uma publicação

O item `Translate` no menu da publicação pede ao servidor uma tradução para o idioma da interface, e o texto traduzido aparece abaixo do original, com a nota `Translated from {idioma} using {provedor}`. O botão `Show Original` volta ao texto original. Se o servidor não conseguir traduzir, aparece `Could not translate`.

Nem todo servidor traduz: os provedores de tradução são configurados pelo administrador da instância.

## 2.7 Filtros do servidor

O seu servidor pode aplicar filtros automáticos, e o Babble os respeita. Um filtro tem um **contexto** — `Home`, `Public`, `Notifications`, `Threads`, `Account` ou `Conversations` — e uma **ação**:

| Ação | O que aparece |
|---|---|
| `Hide` | A publicação some da página. |
| `Warn` | A publicação vira uma barra `Filtered: {nome do filtro}` com um botão `Show Anyway`. |
| `Blur` | A publicação continua legível, mas a mídia fica coberta. |

Se mais de um filtro casar, o mais forte vence: `Hide` sobre `Warn`, e `Warn` sobre `Blur`. O `Blur` só se aplica quando **todos** os filtros que casaram pedem blur.

Você gerencia os filtros em Preferências → Reading → `Manage Filters`, ou pelo item de menu que o servidor oferece. Lá você cria filtros com título, palavras-chave, contexto e ação, e define quando expiram.

## 2.8 O perfil de outra pessoa

**Objetivo:** ver o que uma pessoa publica e o que ela fez com as suas publicações.

**Pré-condições:** você está em uma publicação ou em um resultado de busca.

1. Clique no nome ou no avatar do autor. O perfil abre como uma folha sobre a janela.
2. Use as abas no topo: `Profile`, `Posts`, `Replies`, `Media`, `Featured`, `Likes`, `Follows`, `Followers`. Quais abas existem depende do servidor e das escolhas da conta.
3. O botão na barra inferior é a relação: `Follow`, `Unfollow`, `Follow Back`, `Withdraw Request`, `Unblock` ou `Unblock Domain`.

**Resultado:** o perfil abre como uma folha sobre a janela, com o cabeçalho, as abas e o botão de relação. Clicar em `Follow` passa a conta a aparecer na sua `Home`.

**Se der errado:** se o perfil não carregar, o servidor pode estar lento ou limitando requisições: feche a folha e abra de novo. Se aparecer a mensagem `This user has chosen to not make this information available`, a conta escondeu os contadores de seguidores — o botão `Follow` continua funcionando. Veja [11.12](11-solucao-de-problemas.md#1112-um-recurso-não-aparece).

**O que o cabeçalho mostra:** avatar e imagem de capa (clicáveis, abrem no visualizador), nome, `@conta`, bio, campos de perfil, links verificados (`Verified Link`), `Joined {data}`, `Last post {data}` e a nota de migração de conta, quando existe. Uma bio longa ganha um `Show More` / `Show Less`.

**Selos que aparecem no perfil:** `In Memoriam`, `Suspended`, `Unverified Handle`, `Locked`, `Automated`, `Group`, `Requested`, `Mutuals`, `Follows you`, `Blocks you`, `Blocked`, `Domain Blocked` e `Muted`.

**Contadores clicáveis:** `following` e o número de seguidores abrem listas com os títulos `Followers` e `Following`.

**O menu `More Options` do perfil:**

| Item | O que faz |
|---|---|
| `Mention` | Abre o compositor com `@conta`. |
| `Direct Message` | Abre o compositor com a menção e a visibilidade `Direct`. |
| `Copy Handle` | Copia o `@conta`. |
| `Copy Link` | Copia a URL do perfil. |
| `Open in Browser` | Abre o perfil no navegador. |
| `Receive Post Notifications` | Liga ou desliga as notificações de publicações dessa conta. |
| `Feature on Profile` | Destaque a conta no seu perfil. |
| `Show Boosts` / `Hide Boosts` | Mostra ou esconde os impulsos no seu perfil. |
| `Add or Remove from Lists…` | Abre a folha `Add to Lists`. |
| `Private Note…` | Abre uma nota privada, que só você vê. |
| `Remove from Followers` | Remove a conta de quem te segue. |
| `Mute…` / `Unmute` | Silencia a conta, com escolha de prazo (`Never`, `Hours`, `Days`) e a opção `Hide Notifications`. |
| `Block…` / `Unblock` | Bloqueia a conta, depois de uma confirmação. |
| `Block Domain…` / `Unblock Domain` | Bloqueia o servidor inteiro. |
| `Report…` | Abre o diálogo de denúncia, na seção `Moderation`. |

## 2.9 O seu próprio perfil

`Open Profile`, no menu principal, abre o seu perfil. A diferença em relação ao perfil dos outros: o botão da barra inferior vira `Edit Profile`, e o menu `More Options` perde os itens que só fazem sentido para o outro (`Follow`, `Mention`, `Direct Message`).

O diálogo `Edit Profile` tem os seguintes grupos:

- **Images** — `Avatar`, `Avatar Description`, `Header Image`, `Header Description`, cada um com um botão `Choose…`.
- **About** — `Display Name` e `Bio`.
- **Profile Fields** — campos personalizados, com `Add Field`.
- **Privacy and Discovery** — `Require Follow Requests`, `Automated Account`, `Suggest Account to Others`, `Include Public Posts in Search Results`, `Hide Followers and Following`.
- **Posting Defaults** — `Default Visibility`, `Mark Media as Sensitive`, `Default Language`.
- **Profile Tabs** — `Show Featured Tab`, `Show Media Tab`, `Include Replies in Media Tab` (servidores Mastodon 4.6 ou posteriores).

Os botões são `_Cancel` e `_Save`.

## 2.10 Listas

Uma **lista** é um conjunto nomeado de contas, com uma timeline só dela. As listas são a forma mais direta de acompanhar um grupo — uma equipe, um projeto, um assunto — sem misturar com o resto do feed.

### Como criar uma lista

**Objetivo:** ter uma timeline própria para um grupo de contas.

**Pré-condições:** você está na página `Lists`.

1. Clique em `New List`, no cabeçalho da página — ou no botão `_New List` da página vazia.
2. Escreva o `Title`.
3. Em `Show Replies To`, escolha quem aparece junto: `Members of the List`, `People You Follow` ou `Nobody`.
4. Ligue `Hide Members from Home` se quiser que as publicações dos membros apareçam **apenas** na lista, e não também na `Home`.
5. Em `Members`, use `Add Members` para buscar entre as contas que você segue e adicioná-las.
6. Clique em `_Save` (ou `C_reate`, em uma lista nova).

**Resultado:** a lista aparece na página `Lists` e pode ser fixada na barra lateral.

**Se der errado:** se a lista não aparecer depois de salva, o servidor pode ter recusado o título — tente um título diferente. Para corrigir, use `Edit List` na própria lista. A lista vazia é normal: ela só ganha publicações depois que você adicionar membros.

### A timeline de uma lista

A página de uma lista tem `Edit List` e o botão de fixar no cabeçalho. Quando está vazia, ela diz `No Posts in This List` e explica: *"Add members to the list to see their posts"*.

Para remover uma lista, use o botão de lixeira na página `Lists`: a confirmação avisa que *"“{título}” will be deleted. The posts of its members are not affected."*

### Como adicionar uma conta a uma lista

Pelo perfil da pessoa: `Add or Remove from Lists…` abre a folha `Add to Lists`, onde as listas que já contêm a conta aparecem marcadas. Marcar e desmarcar tem efeito imediato; `Close` apenas fecha a folha.

## 2.11 Hashtags

### A página de uma hashtag

Clicar em uma hashtag dentro de uma publicação abre a página dela, com a timeline de quem a usa. No cabeçalho:

- `Follow` / `Unfollow` — seguir a hashtag. As publicações de uma hashtag seguida passam a aparecer na sua `Home`, e a fonte do post indica `From hashtag {tag}`.
- A estrela `Pin to Sidebar` — fixa a hashtag na barra lateral.
- `More Options` — `Feature on Profile` e `Remove from Featured Tags`.

Uma linha de uso mostra o tamanho da tag: `"{posts} · {pessoas}, last {dias} days"`.

Quando a página está vazia: `No Posts With This Hashtag` — *"No one has posted anything with this tag yet"*.

### A página `Hashtags`

Ela tem duas seções:

- **Followed Hashtags** — as hashtags que você segue, cada uma com a estrela de fixar e um botão `_Unfollow`.
- **Multi-Hashtag Timelines** — as timelines salvas que combinam hashtags.

### Como combinar hashtags

**Objetivo:** uma timeline que reúna hashtags, com condições de inclusão e exclusão.

**Pré-condições:** você está na página `Hashtags`.

1. Clique em `New Multi-Hashtag Timeline`, no cabeçalho.
2. Escreva o `Title`.
3. Ligue `Local Only` para restringir a posts do seu servidor.
4. Em `Hashtags`, adicione as hashtags da timeline — a primeira é a principal.
5. Em `Also Requiring`, adicione as hashtags que a publicação precisa ter **além** das da primeira.
6. Em `Excluding`, adicione as hashtags que **excluem** a publicação.
7. Clique em `_Save`.

**Resultado:** a timeline aparece em `Hashtags` e pode ser fixada na barra lateral.

**Se der errado:** se nada for salvo, o servidor recusou — confira se as três listas somam no máximo 5 hashtags, o limite do aplicativo. O aviso `At most {n} hashtags can be combined` aparece quando o limite é ultrapassado.

O limite é de 5 hashtags somadas nas três listas; ao ultrapassá-lo, o aplicativo avisa `At most {n} hashtags can be combined`.

## 2.12 Explorar

`Explore` mostra o que o seu servidor considera em alta, em quatro abas:

| Aba | O que mostra |
|---|---|
| `Posts` | As publicações em alta. |
| `Hashtags` | As hashtags em alta, com um gráfico de uso. |
| `News` | Os links mais compartilhados, como cartões de pré-visualização. |
| `For You` | Contas que o servidor sugere que você siga, **com o motivo**: `Featured by the server staff`, `You interacted with them before`, `Popular lately`, `Popular on the server`, `Similar to people you followed`, `Followed by people you follow`. |

A aba `News` é a que rende mais: clicar num cartão abre a **timeline do link**, que reúne todas as publicações que mencionam aquele link — nem toda a web, só o que a federação está falando sobre ele. O botão `Open in Browser` no cabeçalho abre o link no navegador.

A aba `For You` tem um botão `_Follow` por conta e um botão de dispensar. A preferência **Explore**, em Preferências → Timelines → Sidebar, esconde esse lugar da barra lateral.

## 2.13 Busca

**Objetivo:** encontrar contas, hashtags e publicações.

**Pré-condições:** você está em qualquer página.

1. Clique em `Search` na barra lateral, ou pressione **Ctrl+F** (**Ctrl+K** funciona também).
2. Digite na caixa de busca.
3. Os resultados aparecem agrupados em `Accounts`, `Hashtags` e `Posts`. Cada grupo tem um botão `See All …`.

**Resultado:** a página `Search` mostra os três grupos. Se nada casar, `No Results` — *"Nothing matched “{termo}”"*.

**Se der errado:** se a página mostrar `Search Failed`, o servidor não respondeu. Tente de novo, ou recarregue a janela com **Ctrl+R**. Se a busca por operadores não trouxer nada, o motivo é quase sempre o servidor: nem todo servidor implementa todos os operadores. Veja [2.13](#213-busca).

### Operadores de busca

A busca do Mastodon aceita operadores no texto, e o Babble os documenta na página inicial da busca:

| Operador | O que faz | Exemplo |
|---|---|---|
| `from:` | Restringe a um autor. | `from:ana` |
| `has:` | Restringe ao tipo de conteúdo. | `has:media` |
| `is:` | Restringe ao tipo de publicação. | `is:reply` |
| `language:` | Restringe ao idioma. | `language:pt` |
| `before:` | Antes de uma data. | `before:2026-01-01` |
| `after:` | Depois de uma data. | `after:2026-01-01` |
| `during:` | Em uma data. | `during:2026-01-01` |
| `in:` | Restringe a um conjunto. | `in:bookmarks` |

Um prefixo `-` nega o operador: `-is:reply` esconde as respostas.

**Nem todo servidor implementa todos os operadores.** É por isso que a página de busca diz *"Search operators may not work on every server"*.

### A busca avançada

O botão `Advanced Search`, no cabeçalho da página de busca, abre um construtor de consulta com campos: `Query`, `From User`, `Only Replies`, `Only Posts with Content Warnings`; o grupo `Containing` (`Media`, `Poll`, `Embed`); o grupo `Timelines` (`Search In`: `Everywhere`, `Library (Favorites, Bookmarks, Own Posts)`, `Public`, e `Language Code`); e o grupo `Dates` (`Before`, `During`, `After`).

O aviso do diálogo é honesto: *"Advanced search may not work on every server. Invert an option by prefixing it with “-”, for example -is:reply hides replies."*

### Buscas recentes

A página inicial da busca guarda as buscas recentes, com um botão `_Clear` para apagá-las todas e um botão por item para remover uma só.

### Colar um link

Colar no campo de busca o endereço de uma publicação ou de um perfil resolve o endereço e abre o conteúdo correspondente — dentro do Babble.

## 2.14 Favoritos, itens salvos, `Local` e `Federated`

- **`Favorites`** — as publicações que você favoritou. Vazia: `No Favorites Yet`.
- **`Bookmarks`** — as publicações que você salvou. Vazia: `No Bookmarks Yet`.
- **`Local`** — a timeline pública do seu servidor: o que os usuários do seu servidor publicam, e o que ele recebe dos outros. Vazia: `No Posts Yet`.
- **`Federated`** — a timeline pública de todos os servidores que o seu conhece. Vazia: `No Posts Yet`.

`Favorites` e `Bookmarks` são paginadas pelo cabeçalho `Link` do servidor, o que significa que rolando até o fim você percorre a lista inteira, sem lacunas.

## 2.15 Publicações repetidas: o carrossel de impulsos

Quando uma página é composta principalmente de impulsos, o Babble as agrupa num carrossel horizontal em vez de uma linha cada. As setas do carrossel dizem `Show Previous Boosts` e `Show Next Boosts`.

Dois controles independentes:

- **Boosts Carousel** (`boosts-carousel`) — agrupa os impulsos. Vem ligado.
- **Single Boost per Post** (`dedupe-boosts`) — mostra apenas o primeiro impulso de uma publicação que várias pessoas impulsionaram. Vem ligado.

As duas se aplicam às páginas abertas **depois** da mudança: recarregue a página para ver o efeito.

## 2.16 Resumo: onde cada coisa está

| Se você quer… | Vá a |
|---|---|
| Ler o que os seus seguidos publicaram | `Home` |
| Ver o que o seu servidor publica | `Local` |
| Ver o que a federação inteira publica | `Federated` |
| Recuperar o que você perdeu | `Catch Up` |
| Saber quem mencionou você | `Notifications` |
| Ler mensagens diretas | `Conversations` |
| Reler algo que você favoritou | `Favorites` |
| Reler algo que você salvou | `Bookmarks` |
| Acompanhar um assunto | `Hashtags` |
| Acompanhar um grupo | `Lists` |
| Descobrir o que está em alta | `Explore` |
| Encontrar uma conta, uma tag ou um post | `Search` |
