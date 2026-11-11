# 3 — Notificações e mensagens diretas

> **Documento:** `BABBLE-MAN-001` — capítulo 3 — v1.0 — rascunho para revisão
> **Software descrito:** Babble 0.3.0 · **Data:** 2026-11-11 · **Pai:** [Manual do Usuário do Babble](README.md)

Notificações são o que o servidor avisa que aconteceu com você: alguém mencionou sua publicação, alguém impulsionou, alguém pediu para te seguir. Este capítulo cobre a caixa de notificações, as mensagens diretas e as três caixas de administração da sua conta — pedidos para seguir, silenciamentos e bloqueios.

## 3.1 A página `Notifications`

**Objetivo:** ver tudo o que o servidor registrou sobre você.

**Pré-condições:** você está com a sua conta conectada.

1. Clique em `Notifications` na barra lateral, ou pressione **Ctrl+2** (**Ctrl+Shift+N** também serve).
2. Use as fichas no topo para alternar entre `All` e `Mentions` — a segunda mostra só o que menciona você.

**Resultado:** a página lista as notificações, da mais recente para a mais antiga, com o avatar, um ícone do tipo, um resumo em texto (`{conta} favorited your post`, `{conta} boosted your post`, `{conta} followed you`, `{conta} mentioned you`, `{conta}’s poll has ended`, `{conta} quoted your post`, `{conta} edited a post you interacted with`, e assim por diante), a hora relativa e um botão `Dismiss Notification`.

**Estados possíveis:** `Loading Notifications` durante a carga, `Could Not Load Notifications` em caso de falha, e `No Notifications` — *"Mentions, boosts and follows will appear here"* — quando não há nada.

### Notificações agrupadas

Quando várias pessoas fazem a mesma coisa com a mesma publicação, o Babble as junta em uma linha só. A linha mostra um leque de avatares sobrepostos e um contador `+N`; um botão expande o grupo e mostra quem é. Clicar em uma pessoa abre o perfil dela.

### O conteúdo anexado

Notificações que se referem a uma publicação trazem um retrato dela: autor, texto, um botão `Read More` quando o texto é longo, o resultado da enquete, e até quatro miniaturas. Publicações filtradas pelo servidor aparecem como `Filtered: {nome do filtro}`, com `Show Anyway`. Mídia sensível é anunciada como `{n} sensitive attachment(s)`.

### Ações dentro da notificação

Algumas notificações trazem botões próprios, para você responder sem sair da página:

| Tipo de notificação | Botões |
|---|---|
| Pedido para seguir | `_Reject` e `_Accept` — depois, um toast `Request accepted` ou `Request rejected`. |
| Nova pessoa que te segue | `_Follow Back` (ou `Following`, se já for seguida, ou `Follow request sent`, se a conta for bloqueada). |
| Relatos de moderação | `_View Reports` — abre o painel. |
| Relatórios anuais | `_View` — abre o relatório. |

## 3.2 Filtrar o que aparece

O botão de engrenagem no canto superior direito da página (`Notification Options`) tem um submenu `Filter`, com uma marca de verificação por tipo. Os tipos são:

`Mentions` · `New Posts` · `Boosts` · `Favorites` · `Follows` · `Follow Requests` · `Polls` · `Post Edits` · `Quotes` · `Quote Edits` · `Sign-Ups` · `Reports` · `Severed Relationships` · `Moderation Warnings` · `Annual Reports` · `Added to Collections` · `Collection Updates`

O filtro é do **Babble**: esconde a notificação da sua caixa. Ele não impede o servidor de enviar.

### Notificações no desktop

Quando a janela do Babble não está em foco, o aplicativo pode mostrar uma notificação do sistema. Isso é controlado em Preferências → Notifications, com um botão por tipo:

`Mentions` · `New Followers` · `Follow Requests` · `Favorites` · `Boosts` · `Poll Results` · `New Posts` · `Edits` · `Quotes` · `Quote Edits` · `New Sign-Ups` · `New Reports`

As três últimas só dizem respeito a administradores de servidor.

A opção **Grouped Notifications** (`group-desktop-notifications`) junta, numa notificação só, várias do mesmo tipo sobre a mesma publicação.

As notificações de favoritos, impulsos e seguidores podem ser exibidas com menos destaque — a opção **Dimmed Trivial Notifications**, em Preferências → Appearance, as torna visualmente mais discretas, sem escondê-las.

Botões de ação aparecem na própria notificação do sistema: `Favorite`, `Boost` e `Follow Back`.

## 3.3 As regras do servidor sobre notificações

A partir do Mastodon 4.3, o próprio servidor pode decidir quais notificações chegam até você, e por conta de quem. Isso é diferente do filtro do Babble: é o servidor que deixa de enviar.

A configuração fica em Preferências → Notifications → `Notification Filtering`, que abre o diálogo `Notification Settings`.

### Filter Notifications From

Para cada classe de remetente, você escolhe entre três respostas:

| Classe | O que significa |
|---|---|
| `People You Don’t Follow` | Contas que você não segue. |
| `People Not Following You` | Contas que não seguem você, ou que começaram a seguir você há menos de três dias. |
| `New Accounts` | Contas criadas nos últimos 30 dias. |
| `Unsolicited Private Mentions` | Menções diretas de quem não é seguido por você — a não ser em resposta a uma menção sua. |
| `Moderated Accounts` | Contas limitadas pelos administradores do servidor. |
| `Bots` | Contas marcadas como automatizadas. |

| Resposta | O que acontece |
|---|---|
| `Accept` | A notificação chega normalmente. |
| `Filter` | A notificação fica numa caixa separada, esperando a sua decisão. |
| `Ignore` | A notificação é descartada. |

O diálogo explica a diferença: *"Filtered notifications wait in a separate inbox until you accept them. Ignored ones are discarded."*

### Show Notifications For

A segunda parte do diálogo é uma lista de interruptores, um por tipo de notificação — a mesma lista do submenu `Filter` da [seção 3.2](#32-filtrar-o-que-aparece).

As alterações só são enviadas ao servidor quando você fecha o diálogo, e apenas se alguma coisa mudou. O aplicativo confirma com `Notification settings updated`.

**Servidores antigos:** se o seu servidor não tem esse recurso, o item diz `This server does not support filtering notifications`.

### A caixa das notificações retidas

As notificações que você deixou em `Filter` esperam numa página própria. O Babble mostra um aviso no topo da página de notificações com um botão `View` que leva a ela.

Cada item dessa caixa tem `Accept` e `Dismiss`. Aceitar entrega as notificações; dispensar as descarta. Depois da decisão, um toast confirma: `Notification request accepted` ou `Notification request dismissed`.

## 3.4 Mensagens diretas

**Objetivo:** trocar mensagens privadas com outra pessoa.

**Pré-condições:** você está em qualquer página.

1. Clique em `Conversations` na barra lateral, ou pressione **Ctrl+3**.
2. Clique em uma conversa para abri-la. A conversa é um fio: as mensagens ficam em ordem, e a última mensagem é a que você vê no resumo da lista.
3. Para começar uma conversa nova, clique em `New Direct Message`, no cabeçalho da página. O compositor abre com a visibilidade `Direct`; digite o `@conta` de quem recebe e escreva a mensagem.

**Resultado:** a conversa aparece na lista, com um ponto de não lida quando há mensagem nova. Ao abrir uma conversa, ela é marcada como lida.

**Se der errado:** uma conversa sem nenhuma mensagem mostra um aviso `This conversation has no messages`.

### O menu de uma conversa

O botão no fim de cada linha da lista abre um menu:

| Item | O que faz |
|---|---|
| `Mark as Read` | Marca a conversa como lida. |
| `Delete Conversation` | Apaga a conversa da sua caixa. A confirmação avisa: *"The conversation is removed from your inbox. Other participants keep their copy."* |

### O resumo de cada conversa

A linha mostra até três avatares, os nomes, a hora relativa e um resumo da última mensagem. O resumo é o corpo da mensagem, ou `CW: {aviso}` quando há aviso de conteúdo, ou `{n} attachment(s)` quando há anexos, ou `No messages` quando a conversa está vazia.

## 3.5 Pedidos para seguir

**Objetivo:** aceitar ou recusar quem pede para te seguir.

**Pré-condições:** sua conta pode estar bloqueada — é essa configuração que faz aparecerem pedidos.

1. Abra o menu principal e escolha `Follow Requests`.
2. Clique em `Accept` ou `Reject` ao lado de cada pessoa.

**Resultado:** a pessoa passa a seguir você (ou não). A página fica vazia com `No Follow Requests` — *"Requests appear here when your account is locked."*

Os mesmos botões aparecem nas notificações de pedido, sem precisar abrir a página.

## 3.6 Silenciados e bloqueados

**Objetivo:** ver e reverter as contas, os domínios e as conversas que você silenciou ou bloqueou.

**Pré-condições:** você está na sua conta.

1. Abra o menu principal e escolha `Mutes & Blocks`.
2. Escolha uma das três abas:
   - `Muted` — as contas silenciadas. Cada linha tem `Unmute`.
   - `Blocked` — as contas bloqueadas. Cada linha tem `Unblock`.
   - `Domains` — os servidores bloqueados inteiramente. Cada linha tem `Unblock`.

**Resultado:** a ação é imediata e a linha some da lista.

Para silenciar ou bloquear, use o menu da publicação (`Mute {conta}`, `Block {conta}`) ou o menu do perfil (`Mute…`, `Block…`, `Block Domain…`).

## 3.7 Anúncios do servidor

**Objetivo:** ler os avisos que o administrador do seu servidor publica.

**Pré-condições:** o seu servidor publica anúncios.

1. Abra o menu principal e escolha `Announcements` — ou o item `_Announcements` no menu de opções das notificações.

**Resultado:** cada anúncio aparece como um cartão, com o conteúdo formatado, a data de publicação e de atualização e a data do evento. Os anúncios não lidos recebem um selo `Unread` e o botão `Mark as Read`. O cabeçalho tem `Mark All as Read`.

Se o seu servidor não publica anúncios, a página fica vazia.

## 3.8 Referência: tipos de notificação

| Tipo | Notificação do Babble | Notificação do desktop |
|---|---|---|
| Menção | `Mentions` | `Mentions` |
| Publicação de alguém que você segue | `New Posts` | `New Posts` |
| Impulso | `Boosts` | `Boosts` |
| Favorito | `Favorites` | `Favorites` |
| Novo seguidor | `Follows` | `New Followers` |
| Pedido para seguir | `Follow Requests` | `Follow Requests` |
| Fim de enquete | `Polls` | `Poll Results` |
| Edição de uma publicação com a qual você interagiu | `Post Edits` | `Edits` |
| Citação | `Quotes` | `Quotes` |
| Edição de uma citação sua | `Quote Edits` | `Quote Edits` |
| Nova conta no servidor | `Sign-Ups` | `New Sign-Ups` |
| Relato de moderação | `Reports` | `New Reports` |
| Relação desfeita | `Severed Relationships` | — |
| Aviso de moderação | `Moderation Warnings` | — |
| Relatório anual | `Annual Reports` | — |
| Entrada em uma coleção | `Added to Collections` | — |
| Atualização de uma coleção | `Collection Updates` | — |
