# 8 — Preferências: referência completa

> **Documento:** `BABBLE-MAN-001` — capítulo 8 — v1.0 — rascunho para revisão
> **Software descrito:** Babble 0.3.0 · **Data:** 2026-11-11 · **Pai:** [Manual do Usuário do Babble](README.md)

Este capítulo é de **referência**: ele lista cada preferência, o que ela faz e quando ela tem efeito. Não há passo a passo aqui — para isso, veja os capítulos de função.

## 8.1 Abrir e usar as Preferências

Abra com **Ctrl+,** ou pelo item `Preferences` do menu principal.

A janela de Preferências tem uma **caixa de busca**: digite e as páginas se filtram. Isso é a maneira mais rápida de achar uma preferência cujo nome você não lembra.

As páginas são `Appearance`, `Reading`, `Timelines`, `Posting` e `Notifications`. A página `Posting` só existe quando há uma conta conectada.

## 8.2 Página `Appearance`

### Grupo principal

| Preferência | Subtítulo | Controle | Efeito |
|---|---|---|---|
| `Language` | Takes effect the next time Babble starts | Lista: `System language`, `English`, `Deutsch`, `Español`, `Français`, `Português (Brasil)` | O idioma de toda a interface. **Só vale na próxima vez que o aplicativo iniciar.** |
| `Color Scheme` | — | Lista: `System`, `Light`, `Dark` | O esquema de cores. |
| `Text Size` | Set the text size used in the interface | Lista: `Small`, `Normal`, `Large` | O tamanho do texto da interface. Há atalhos para isso: **Ctrl++**, **Ctrl+-** e **Ctrl+0**. |
| `Large Line Height` | Increase the line height in posts | Interruptor | Linhas mais espaçadas no corpo das publicações. |
| `Dimmed Trivial Notifications` | Notifications like favorites and boosts will be less visible | Interruptor | Favoritos, impulsos e seguidores aparecem com menos destaque na página de notificações. |
| `Translucent Sidebar` | Blur the desktop behind the sidebar. Needs a compositor that supports background blur | Interruptor | Fundo desfocado na barra lateral. Depende do compositor. |

### Grupo `Images and Emojis`

| Preferência | Controle | Efeito |
|---|---|---|
| `Darkened Images in Dark Mode` | Interruptor | Escurece as imagens no tema escuro. |
| `Large Custom Emojis` | Interruptor | Emojis customizados maiores. |
| `Custom Emoji Zoom on Hover` | Interruptor | O emoji customizado cresce um pouco ao passar o cursor. |

## 8.3 Página `Reading`

### Grupo `Posts`

| Preferência | Subtítulo | Controle | Efeito |
|---|---|---|---|
| `Collapsed Long Posts` | — | Interruptor (ligado) | Publicações longas e quase só de links ou hashtags vêm recolhidas, com o botão `Show More`. |
| `Spoilers` | Reveal posts with a content warning | Interruptor | **Ligado**, o conteúdo de um post com aviso de conteúdo vem revelado. Desligado, vem escondido. |
| `Link Preview Cards` | — | Interruptor (ligado) | Mostra o cartão de pré-visualização dos links. |
| `Bluesky Posts` | Bluesky links open in Babble, which contacts Bluesky’s servers | Interruptor (ligado) | Abre posts do Bluesky dentro do aplicativo. Desligado, abre no navegador. **Ligado, o aplicativo contata os servidores do Bluesky.** |
| `Interaction Counters` | Reply, boost and favorite counts on post actions | Interruptor (ligado) | Mostra as contagens na barra de ações. |
| `Post Actions on Timelines` | Actions stay available in each post’s menu | Interruptor (desligado) | Desligado, a barra de ações some das timelines e os botões vão para o menu `More Options` do post. |

### Grupo `Media`

| Preferência | Subtítulo | Controle | Efeito |
|---|---|---|---|
| `Letterboxed Media` | Scale down and letterbox media to fill the image containers instead of cropping them | Interruptor | A mídia é encaixada inteira, com tarjas, em vez de ser cortada. |
| `Photo Viewer` | How a photo opens in the viewer: as a box over the window, or filling it | Duas opções: `Box`, `Window` | Como a imagem abre no visualizador: como uma caixa sobre a janela, ou preenchendo-a. |
| `Media From Other Servers` | Loading media your server has no copy of lets the author’s server see your IP address | Lista: `Ask Each Time`, `Always Load`, `Never Load` | O consentimento para mídia que o seu servidor não tem. Ver [5.7](05-midia.md#57-mídia-que-está-em-outro-servidor). |
| `Sensitive Media` | Reveal sensitive media instead of hiding it behind a warning | Interruptor | Revela a mídia sensível automaticamente. |
| `Autoplay Animated Media` | Play animated GIFs by themselves. Reduced motion and power saving always stop them | Interruptor (ligado) | Os GIFs animados tocam sozinhos. |
| `Play Animated GIFs in Timelines` | Plays one at a time. Uses more memory | Interruptor (desligado) | O GIF mais visível da timeline toca, um de cada vez. |
| `Load Media Automatically` | For video, audio and animated GIFs. Images always load | Lista: `Always`, `Not on Metered Connections`, `Never` | Quando a mídia pesada é baixada. |

### Grupo `Home Timeline`

| Preferência | Subtítulo | Controle | Efeito |
|---|---|---|---|
| `Hidden Replies From Non-Follows` | On the home timeline, hide posts that are replies from accounts you do not follow | Interruptor | Esconde respostas de quem você não segue, na `Home`. |
| `Hidden Boosts on Home` | On the home timeline, show only posts and replies by the people you follow, without their boosts | Interruptor | Esconde os impulsos, na `Home`. |
| `Remembered Reading Position` | Reopen the home timeline at the post you were reading, with a button for newer posts | Interruptor (ligado) | A `Home` reabre onde você parou. |

### Grupo `Links`

| Preferência | Subtítulo | Controle | Efeito |
|---|---|---|---|
| `Tracking Parameter Removal` | This option can lead to broken links when enabled | Interruptor (ligado) | Remove parâmetros de rastreamento dos links. Pode quebrá-los. |
| `Remote Links in Babble` | Open links to posts and profiles on other servers inside the app | Interruptor (ligado) | Links de post e de perfil de outros servidores abrem no Babble. |
| `Web Links Beside the Timeline` | Show other links in a column instead of the browser. Pages keep no cookies once Babble closes | Interruptor | Só aparece quando o WebKitGTK está instalado. Abre páginas numa coluna ao lado da timeline. |
| `JavaScript on Web Pages` | Many sites do not work without it | Interruptor | JavaScript na coluna web. Desligar aumenta a segurança e quebra muitos sites. |
| `Copied Link Suggestions` | Check the clipboard for post and profile links when Babble is focused | Interruptor (ligado) | Oferece abrir o post ou o perfil copiado. |

### Grupo `Filters`

Aparece quando há uma conta conectada, com a descrição *"Hide or warn about posts that match keywords, on every device you use"*.

| Item | Efeito |
|---|---|
| `Manage Filters` | Abre o diálogo `Filters`, onde você cria, edita e apaga filtros. Ver [2.7](02-leitura.md#27-filtros-do-servidor). |

## 8.4 Página `Timelines`

### Grupo `Live Updates`

| Preferência | Subtítulo | Controle | Efeito |
|---|---|---|---|
| `Live Timelines` | Receive new posts and notifications in real time | Interruptor (ligado) | Conexão permanente com o servidor. |
| `Live Public Timelines` | This will increase memory usage when browsing high traffic timelines | Interruptor | Atualização ao vivo também nas timelines públicas (`Local` e `Federated`). Depende da anterior. |

### Grupo `Refresh`

A descrição do grupo é *"Used while live updates are off or unavailable"*.

| Preferência | Controle | Efeito |
|---|---|---|
| `Automatic Refresh` | Lista: `Off`, `Every 30 seconds`, `Every minute`, `Every 5 minutes` | A periodicidade do recarregamento. |

### Grupo `Sidebar`

A descrição do grupo é *"Choose which timelines the sidebar offers"*.

| Preferência | Subtítulo | Controle | Efeito |
|---|---|---|---|
| `Catch Up` | Summarize what you missed in your home timeline | Interruptor | Mostra ou esconde o lugar `Catch Up`. |
| `Explore` | Show posts trending on your server | Interruptor | Mostra ou esconde o lugar `Explore`. |
| `Local Timeline` | Show posts from your server | Interruptor | Mostra ou esconde o lugar `Local`. |
| `Federated Timeline` | Show posts from all known servers | Interruptor | Mostra ou esconde o lugar `Federated`. |
| `Disabled Timelines` | Your server has turned these timelines off, but this account may still read them | Interruptor | Só aparece quando o servidor desativou de fato alguma timeline. Reabilita as timelines restritas que esta conta ainda pode ler. |

### Grupo `Boosts`

| Preferência | Subtítulo | Controle | Efeito |
|---|---|---|---|
| `Boosts Carousel` | When a page of posts is mostly boosts, fold them into a horizontal carousel instead of one row each. Applies to timelines opened afterwards | Interruptor (ligado) | Agrupa os impulsos num carrossel horizontal. |
| `Single Boost per Post` | Show only the first boost of a post that several people boosted. Applies to timelines opened afterwards | Interruptor (ligado) | Mostra só o primeiro impulso de cada publicação. |

## 8.5 Página `Posting`

Só existe com uma conta conectada. O grupo `Defaults for New Posts` tem a descrição *"Changes are also saved to your server preferences"*.

### Grupo `Defaults for New Posts`

| Preferência | Controle | Efeito |
|---|---|---|
| `Default Visibility` | Lista: `Public`, `Unlisted`, `Followers only`, `Direct` | A visibilidade inicial do compositor. **Gravada no servidor.** |
| `Default Quote Policy` | Lista: `Everyone`, `Followers`, `Only me` | Quem pode citar suas publicações. Só em Mastodon 4.5+. **Gravada no servidor.** |
| `Default Language` | Lista: `System language` e cerca de 60 idiomas | O idioma inicial do compositor. **Gravado no servidor.** |
| `Media Marked Sensitive` | Interruptor | Marca a mídia nova como sensível. |

### Grupo `Boosts and Reminders`

| Preferência | Subtítulo | Controle | Efeito |
|---|---|---|---|
| `Advanced Boost Dialog` | Change boost visibility, quote and confirm boosting | Interruptor | Abre a caixa `Boost With Visibility` antes de impulsionar. |
| `Old Post Reply Warning` | — | Interruptor (ligado) | Avisa ao responder a um post com mais de 90 dias. |
| `Private Post Link Warning` | — | Interruptor (ligado) | Avisa antes de copiar o link de uma publicação privada. |
| `Missing Alt Text Boost Warning` | — | Interruptor (ligado) | Avisa antes de impulsionar um post com mídia sem descrição. |

### Atalho para as preferências do servidor

O item `Server Preferences`, no fim da página, abre `https://{seu-servidor}/settings/preferences` no navegador. É lá que o servidor guarda as próprias preferências de publicação — as mesmas que o Babble escreve quando você muda o valor padrão de visibilidade, idioma ou política de citação.

## 8.6 Página `Notifications`

### Grupo `Desktop Notifications`

A descrição do grupo é *"Announced while the window is not focused"*.

| Preferência | Subtítulo | Efeito |
|---|---|---|
| `Mentions` | — | Notifica menções. |
| `New Followers` | — | Notifica novos seguidores. |
| `Follow Requests` | — | Notifica pedidos para seguir. |
| `Favorites` | — | Notifica favoritos. |
| `Boosts` | — | Notifica impulsos. |
| `Poll Results` | Polls you voted in or created | Notifica o fim de enquetes em que você votou ou criou. |
| `New Posts` | From accounts you turned notifications on for | Notifica publicações de contas com notificação ligada. |
| `Edits` | Posts you boosted that were edited | Notifica edições de publicações que você impulsionou. |
| `Quotes` | — | Notifica citações. |
| `Quote Edits` | Posts you quoted that were edited | Notifica edições de citações suas. |
| `New Sign-Ups` | Server administrators only | Notifica contas novas. Só para administradores. |
| `New Reports` | Server administrators only | Notifica relatos de moderação. Só para administradores. |
| `Grouped Notifications` | Combine notifications of the same type and post into one | Agrupa em uma só as notificações do mesmo tipo sobre a mesma publicação. |

### Grupo `Server`

Só aparece em Mastodon 4.3 ou posteriores.

| Item | Subtítulo | Efeito |
|---|---|---|
| `Notification Filtering` | Choose whose notifications reach you | Abre o diálogo `Notification Settings`, com as regras do servidor. Ver [3.3](03-notificacoes-e-mensagens.md#33-as-regras-do-servidor-sobre-notificações). |

### Grupo `Background`

| Preferência | Subtítulo | Efeito |
|---|---|---|
| `Background Mode` | Keep running after the window is closed to deliver notifications | O aplicativo continua rodando com a janela fechada. |
| `Quick Post Shortcut` | Open the composer from anywhere with a system-wide shortcut, which can be changed in the system settings | Registra um atalho global do sistema que abre o compositor. A linha só aparece se o portal de atalhos globais existir. |

## 8.7 Preferências que não têm botão

Algumas opções do aplicativo não têm um interruptor na janela de Preferências, porque são ajustadas por outro caminho:

| Opção | Onde se ajusta |
|---|---|
| Modo da barra lateral (`Full Sidebar`, `Icons Only`, `Hidden`) | O botão `Sidebar`, no rodapé da barra lateral. |
| Lista de contas e conta ativa | O botão da conta, no alto da barra lateral. |
| Plugins ligados | `Plugins`, no menu principal. |
| Pastas e hashtags fixadas | A estrela, no cabeçalho da página de cada uma. |
| Volume do vídeo e do áudio | O visualizador de mídia. |
| Recentes da busca | A página `Search`. |
| Rascunhos locais | O compositor, ao fechá-lo. |
