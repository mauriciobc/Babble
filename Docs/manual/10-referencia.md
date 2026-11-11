# 10 — Referência: atalhos, menus, mensagens e limites

> **Documento:** `BABBLE-MAN-001` — capítulo 10 — v1.0 — rascunho para revisão
> **Software descrito:** Babble 0.3.0 · **Data:** 2026-11-11 · **Pai:** [Manual do Usuário do Babble](README.md)

Este capítulo é material de consulta. Ele não se lê do começo ao fim: você procura a linha de que precisa.

## 10.1 Atalhos de teclado

A lista completa, atualizada pelo próprio aplicativo, abre com **Ctrl+?** (item `Keyboard Shortcuts` do menu principal).

### `General`

| Ação | Atalho |
|---|---|
| `Preferences` | **Ctrl+,** |
| `Keyboard Shortcuts` | **Ctrl+?** |
| `Help` | **F1** |
| `Main Menu` | **F10** |
| `Close Window` | **Ctrl+W** |
| `Quit` | **Ctrl+Q** |

### `Timelines`

| Ação | Atalho |
|---|---|
| `New Post` | **Ctrl+N** |
| `Reload` | **Ctrl+R**, **F5** |
| `Toggle Sidebar` | **F9** |
| `Go Back` | **Alt+Left** |
| `Search` | **Ctrl+F**, **Ctrl+K** |
| `Go to Home` | **Ctrl+1**, **Alt+Home** |
| `Go to Notifications` | **Ctrl+2**, **Ctrl+Shift+N** |
| `Go to Conversations` | **Ctrl+3** |
| `Go to Search` | **Ctrl+4** |
| `Go to Favorites` | **Ctrl+5** |
| `Go to Bookmarks` | **Ctrl+6** |
| `Go to Hashtags` | **Ctrl+7** |
| `Go to Explore` | **Ctrl+8** |
| `Go to Local` | **Ctrl+9** |
| `Scroll Page Down` | **Page Down** |
| `Scroll Page Up` | **Page Up** |
| `Increase Text Size` | **Ctrl++**, **Ctrl+=**, **Ctrl+Teclado +** |
| `Decrease Text Size` | **Ctrl+-**, **Ctrl+Teclado −** |
| `Reset Text Size` | **Ctrl+0**, **Ctrl+Teclado 0** |

### `Catch Up`

| Ação | Atalho |
|---|---|
| `Next Post` | **j** |
| `Previous Post` | **k** |
| `Next Author` | **l** |
| `Previous Author` | **h** |
| `Open Post` | **Return** |
| `Scroll to Top` | **.** |

### `Web Column`

| Ação | Atalho |
|---|---|
| `Hide Web Column` | **Escape**, **Ctrl+W** |
| `Back` | **Alt+Left** |
| `Forward` | **Alt+Right** |
| `Reload Page` | **Ctrl+R**, **F5** |

### `Composer`

| Ação | Atalho |
|---|---|
| `Send Post` | **Ctrl+Return** |
| `Close Composer` | **Escape** |

### Atalhos dentro do visualizador de mídia

| Ação | Atalho |
|---|---|
| Anexo anterior / seguinte | **←**, **→** |
| Tela cheia | **F** |
| Fechar | **Escape** |
| Aproximar / afastar | **+**, **-** |
| Ajustar à janela | **0** |

### Sobre o `Escape`

O `Escape` **não** é um atalho global do aplicativo, e isso é deliberado: como atalho de aplicação, ele seria resolvido antes do widget em foco e engoliria o `Escape` que fecha um diálogo, dispensa a lista de menções e faz as outras coisas que o `Escape` deve fazer. Por isso o "voltar" é **Alt+Left**.

## 10.2 O menu principal

O botão de três linhas, no topo da barra lateral, abre o menu principal. O mesmo menu aparece na tela de login e na faixa de controles das páginas.

| Item | Para que serve |
|---|---|
| `Open Profile` | Abre o seu próprio perfil. |
| `Announcements` | Os avisos publicados pelo seu servidor. |
| `Follow Requests` | Quem pede para te seguir. |
| `Mutes & Blocks` | Suas contas, domínios e conversas silenciados ou bloqueados. |
| `Draft Posts` | Os rascunhos guardados no servidor. |
| `Scheduled Posts` | As publicações agendadas no servidor. |
| `Collections` | Coleções de contas recomendadas pelo servidor (Mastodon 4.6+). |
| `#FediWrapped` | O seu relatório anual (o *Wrapstodon*). |
| `Admin Dashboard` | O painel de moderação. Só aparece para quem tem papel de administrador. |
| `Preferences` | Abre as Preferências. |
| `Plugins` | Abre a página de plugins. |
| `Keyboard Shortcuts` | Abre a lista de atalhos. |
| `Help` | Abre a ajuda do aplicativo. |
| `About Babble` | Sobre o aplicativo. |

**Não estão no menu principal:** `New Post`, `Reload` e `Quit`. Os dois primeiros têm botão no cabeçalho e atalho; o terceiro tem **Ctrl+Q**.

Plugin ativos podem acrescentar itens próprios ao menu principal.

## 10.3 O menu de login

Na tela de boas-vindas, o menu principal mostra apenas: `Preferences`, `Plugins`, `Keyboard Shortcuts`, `Help` e `About Babble`. As demais ações exigem uma conta conectada.

## 10.4 Onde ficam as outras listas

| Lista | Onde abrir |
|---|---|
| Itens de uma publicação | O botão `More Options`, no canto da publicação. Ver [2.5](02-leitura.md#25-o-menu-de-uma-publicação). |
| Itens de um perfil | O botão `More Options`, na página de perfil. Ver [2.8](02-leitura.md#28-o-perfil-de-outra-pessoa). |
| Opções das notificações | O botão de engrenagem no cabeçalho da página `Notifications`. |
| Opções do Catch Up | O botão `List Options`, no cabeçalho da lista de publicações. |
| Opções do visualizador de mídia | O botão `More Actions`, no cabeçalho do visualizador. |
| Modo da barra lateral | O botão `Sidebar`, no rodapé da barra lateral. |

## 10.5 Rótulos da interface em inglês e em português

O manual cita os rótulos como o código-fonte os define, em inglês. A tabela abaixo relaciona cada um deles à tradução em português (pt-BR) disponível no aplicativo.

**Rótulos que não aparecem na tabela não têm tradução em português (pt-BR) no aplicativo 0.3.0** — nesse caso, a interface mostra o texto em inglês. A tradução do aplicativo está incompleta em 0.3.0, e essa tabela cresce a cada versão.

| Rótulo em inglês | Em português (pt-BR) |
|---|---|
| `1 day` | 1 dia |
| `1 hour` | 1 hora |
| `30 minutes` | 30 minutos |
| `7 days` | 7 dias |
| `ALT` | ALT |
| `Accept` | Aceitar |
| `Account` | Conta |
| `Accounts` | Contas |
| `Add Field` | Adicionar campo |
| `Add Members` | Adicionar membros |
| `Add content warning text` | Adicione o texto do aviso de conteúdo |
| `Add to Lists` | Adicionar às listas |
| `Added to Collections` | Adicionado a Coleções |
| `Admin Dashboard` | Painel de administração |
| `Advanced Search` | Pesquisa avançada |
| `After` | Depois |
| `All` | Todas |
| `All authors` | Todos os autores |
| `All posts` | Todas as publicações |
| `Also Requiring` | Exigindo também |
| `Always` | Sempre |
| `Always Load` | Sempre carregar |
| `Animated GIF` | GIF animado |
| `Announcements` | Anúncios |
| `Annual Reports` | Relatórios anuais |
| `Appearance` | Aparência |
| `As far back as possible` | O mais longe possível |
| `Ascending` | Crescente |
| `Ask Each Time` | Perguntar a cada vez |
| `Attach Media` | Anexar mídia |
| `Attachment` | Anexo |
| `Attachment removed` | Anexo removido |
| `Audio` | Áudio |
| `Author` | Autor |
| `Automated` | Automatizada |
| `Automated Account` | Conta automatizada |
| `Avatar` | Avatar |
| `Avatar Description` | Descrição do avatar |
| `Back` | Voltar |
| `Background` | Segundo plano |
| `Before` | Antes |
| `Bio` | Biografia |
| `Blocked` | Bloqueado |
| `Blocks you` | Bloqueia você |
| `Bookmark` | Salvar |
| `Bookmarks` | Itens salvos |
| `Boost` | Impulsionar |
| `Boost With Visibility` | Impulsionar com visibilidade |
| `Boost removed` | Impulso removido |
| `Boosts` | Impulsos |
| `Boosts and Reminders` | Impulsos e lembretes |
| `Bots` | Robôs |
| `Box` | Caixa |
| `C_reate` | C_riar |
| `Catch Up` | Catch Up |
| `Catch up on 24 hours` | Atualizar as últimas 24 horas |
| `Category` | Categoria |
| `Change Time` | Alterar horário |
| `Characters Left` | Caracteres restantes |
| `Choose…` | Escolher… |
| `Close` | _Fechar |
| `Close Composer` | Fechar editor |
| `Close Window` | Fechar janela |
| `Closed` | Encerrada |
| `Collection Updates` | Atualizações de Coleção |
| `Collections` | Coleções |
| `Composer` | Editor |
| `Containing` | Contendo |
| `Content Warning` | Aviso de conteúdo |
| `Conversation deleted` | Conversa excluída |
| `Conversations` | Conversas |
| `Convert into a Draft` | Converter em rascunho |
| `Copy URL` | Copiar URL |
| `Could Not Load Image` | Não foi possível carregar a imagem |
| `Could Not Load Media` | Não foi possível carregar a mídia |
| `Could Not Load Notifications` | Não foi possível carregar as notificações |
| `Could Not Load Thread` | Não foi possível carregar a conversa |
| `Could not change quote policy` | Não foi possível alterar a política de citação |
| `Could not mark as read` | Não foi possível marcar como lido |
| `Could not mute conversation` | Não foi possível silenciar a conversa |
| `Could not open media` | Não foi possível abrir os meios de comunicação |
| `Could not paste the files` | Não foi possível colar os arquivos |
| `Could not paste the image` | Não foi possível colar a imagem |
| `Could not pin post` | Não foi possível fixar a publicação |
| `Could not save media` | Não foi possível salvar a mídia |
| `Could not save notification settings` | Não foi possível salvar as configurações de notificação |
| `Could not take a screenshot` | Não foi possível tirar uma captura de tela |
| `Could not translate` | Não foi possível traduzir |
| `Could not unmute conversation` | Não foi possível deixar de silenciar a conversa |
| `Could not unpin post` | Não foi possível desafixar a publicação |
| `Could not upload media` | Não foi possível enviar a mídia |
| `Custom` | Personalizados |
| `Dark` | Escuro |
| `Dates` | Datas |
| `Decrease Text Size` | Diminuir tamanho do texto |
| `Default Language` | Idioma padrão |
| `Default Visibility` | Visibilidade padrão |
| `Defaults for New Posts` | Padrões para novas publicações |
| `Delete` | Excluir |
| `Density` | Densidade |
| `Descending` | Decrescente |
| `Description` | Descrição |
| `Desktop Notifications` | Notificações da área de trabalho |
| `Direct` | Direta |
| `Discard Draft` | Descartar rascunho |
| `Dismiss` | Dispensar |
| `Dismiss Notification` | Dispensar notificação |
| `Display Name` | Nome de exibição |
| `Domain Blocked` | Domínio bloqueado |
| `Domains` | Domínios |
| `Draft Posts` | Rascunhos |
| `Duration` | Duração |
| `During` | Durante |
| `Edit` | Editar |
| `Edit List` | Editar lista |
| `Edit Post` | Editar publicação |
| `Edit Profile` | Editar perfil |
| `Edit Scheduled Post` | Editar publicação agendada |
| `Edits` | Edições |
| `Embed` | Incorporação |
| `Emoji` | Emoji |
| `End of the previous post` | Fim da publicação anterior |
| `Every 30 seconds` | A cada 30 segundos |
| `Every 5 minutes` | A cada 5 minutos |
| `Every minute` | A cada minuto |
| `Everyone` | Todos |
| `Everywhere` | Todo lugar |
| `Excluding` | Excluindo |
| `Exit Fullscreen` | Sair da tela cheia |
| `Explore` | Explorar |
| `Favorite` | Favoritar |
| `Favorites` | Favoritos |
| `Featured by the server staff` | Destacado pela equipe do servidor |
| `Federated` | Federada |
| `Filter` | Filtrar |
| `Filtered` | Filtrada |
| `Filters` | Filtros |
| `Fit to Window` | Ajustar à janela |
| `Follow` | Seguir |
| `Follow Back` | Seguir de volta |
| `Follow Requests` | Pedidos para seguir |
| `Follow request sent` | Pedido para seguir enviado |
| `Followed` | Seguida |
| `Followed by people you follow` | Seguido por pessoas que você segue |
| `Followed tags` | Hashtags seguidas |
| `Followers` | Seguidores |
| `Followers only` | Somente seguidores |
| `Following` | Seguindo |
| `Follows` | Seguidores |
| `Follows you` | Segue você |
| `For You` | Para você |
| `Forward` | Avançar |
| `From User` | Do usuário |
| `Fullscreen` | Tela cheia |
| `General` | Geral |
| `Go Back` | Voltar |
| `Go to Bookmarks` | Ir a Marcadores |
| `Go to Conversations` | Ir a Conversas |
| `Go to Explore` | Ir a Explorar |
| `Go to Favorites` | Ir a Favoritos |
| `Go to Hashtags` | Ir a Hashtags |
| `Go to Home` | Ir para o Início |
| `Go to Local` | Ir a Local |
| `Go to Notifications` | Ir para Notificações |
| `Go to Search` | Ir a Pesquisa |
| `Group` | Grupo |
| `Group by author` | Agrupar por autor |
| `Groups` | Grupos |
| `Hashtag` | Hashtag |
| `Hashtags` | Hashtags |
| `Header Description` | Descrição do cabeçalho |
| `Header Image` | Imagem de cabeçalho |
| `Help` | Ajuda |
| `Hide` | Ocultar |
| `Hide Content` | Ocultar conteúdo |
| `Hide Followers and Following` | Ocultar seguidores e seguindo |
| `Hide Members from Home` | Ocultar membros do Início |
| `Hide Notifications` | Ocultar notificações |
| `Hide Sensitive Content` | Ocultar conteúdo sensível |
| `Hide Web Column` | Ocultar coluna web |
| `Home` | Início |
| `Home Timeline` | Linha do tempo inicial |
| `Ignore` | Ignorar |
| `Image` | Imagem |
| `Image copied` | Imagem copiada |
| `Images and Emojis` | Imagens e emojis |
| `Images, Videos and Audio` | Imagens, vídeos e áudio |
| `In Memoriam` | In memoriam |
| `Include Replies in Media Tab` | Incluir respostas na aba Mídia |
| `Included With Babble` | Incluído com o Babble |
| `Increase Text Size` | Aumentar tamanho do texto |
| `Insert Emoji` | Inserir emoji |
| `Installed by You` | Instalados por você |
| `Keep Open for a Thread` | Manter aberto para um fio |
| `Keyboard Shortcuts` | Atalhos de teclado |
| `Language` | Idioma |
| `Large` | Grande |
| `Last hour` | Última hora |
| `Library (Favorites, Bookmarks, Own Posts)` | Biblioteca (favoritos, itens salvos, publicações próprias) |
| `Light` | Claro |
| `Likes` | Favoritos |
| `Link` | Link |
| `Link copied` | Link copiado |
| `Links` | Links |
| `List Options` | Opções da lista |
| `Lists` | Listas |
| `Live Updates` | Atualizações ao vivo |
| `Loading Notifications` | Carregando notificações |
| `Loading Thread` | Carregando conversa |
| `Local` | Local |
| `Local Only` | Somente local |
| `Locked` | Trancada |
| `Main Menu` | Menu principal |
| `Mark All as Read` | Marcar todos como lidos |
| `Mark Media as Sensitive` | Marcar mídia como sensível |
| `Media` | Mídia |
| `Media Still Processing` | A mídia ainda está sendo processada |
| `Media accepted by this server` | Mídia aceita por este servidor |
| `Members` | Membros |
| `Members of the List` | Membros da lista |
| `Mention` | Mencionar |
| `Mentions` | Menções |
| `Moderated Accounts` | Contas moderadas |
| `Moderation` | Moderação |
| `Moderation Warnings` | Advertências da moderação |
| `More Actions` | Mais ações |
| `More Options` | Mais opções |
| `Mute failed` | Falha ao silenciar |
| `Muted` | Silenciada |
| `Mutes & Blocks` | Silenciados e bloqueados |
| `Mutuals` | Mútuos |
| `Never` | Nunca |
| `Never Load` | Nunca carregar |
| `New Accounts` | Contas novas |
| `New Direct Message` | Nova mensagem direta |
| `New Followers` | Novos seguidores |
| `New List` | Nova lista |
| `New Multi-Hashtag Timeline` | Nova linha do tempo de várias hashtags |
| `New Post` | Nova publicação |
| `New Posts` | Novas publicações |
| `New Reports` | Novas denúncias |
| `New Sign-Ups` | Novos cadastros |
| `News` | Notícias |
| `Next Attachment` | Próximo anexo |
| `Next Author` | Próximo autor |
| `Next Post` | Próxima publicação |
| `No Bookmarks Yet` | Nenhum item salvo ainda |
| `No Favorites Yet` | Nenhum favorito ainda |
| `No Follow Requests` | Nenhum pedido para seguir |
| `No Media` | Nenhuma mídia |
| `No Notifications` | Nenhuma notificação |
| `No Posts With This Hashtag` | Nenhuma publicação com esta hashtag |
| `No Posts Yet` | Nenhuma publicação ainda |
| `No Posts in This List` | Nenhuma publicação nesta lista |
| `No Results` | Nenhum resultado |
| `No messages` | Nenhuma mensagem |
| `Nobody` | Ninguém |
| `Normal` | Normal |
| `Not on Metered Connections` | Não em conexões medidas |
| `Notification Options` | Opções de notificação |
| `Notification Settings` | Configurações de notificação |
| `Notification request accepted` | Pedido de notificação aceito |
| `Notification request dismissed` | Pedido de notificação dispensado |
| `Notification settings updated` | Configurações de notificação atualizadas |
| `Notifications` | Notificações |
| `Off` | Desligado |
| `On your profile` | No seu perfil |
| `Only Posts with Content Warnings` | Somente publicações com aviso de conteúdo |
| `Only Replies` | Somente respostas |
| `Only me` | Somente eu |
| `Open Post` | Abrir publicação |
| `Open in Browser` | Abrir no navegador |
| `Original` | Original |
| `Other Popular Servers` | Outros servidores populares |
| `Page Actions` | Ações da página |
| `Pasted image` | Imagem colada |
| `Pause Animation` | Pausar animação |
| `People Not Following You` | Pessoas que não seguem você |
| `People You Don’t Follow` | Pessoas que você não segue |
| `People You Follow` | Pessoas que você segue |
| `Pin to Sidebar` | Fixar na barra lateral |
| `Pinned` | Fixadas |
| `Play Animation` | Reproduzir animação |
| `Plugins` | Plugins |
| `Plugins Are Unavailable` | Plugins indisponíveis |
| `Poll` | Enquete |
| `Poll Results` | Resultados de enquetes |
| `Poll choices must be unique` | As opções da enquete devem ser diferentes |
| `Polls` | Enquetes |
| `Popular lately` | Popular recentemente |
| `Popular on the server` | Popular no servidor |
| `Post Edits` | Edições de publicações |
| `Post Language` | Idioma da publicação |
| `Post Visibility` | Visibilidade da publicação |
| `Post attached as a quote` | Publicação anexada como citação |
| `Post deleted` | Publicação excluída |
| `Post edited` | Publicação editada |
| `Post is too long` | A publicação é muito longa |
| `Post scheduled` | Publicação agendada |
| `Post sent` | Publicação enviada |
| `Posting` | Publicação |
| `Posts` | Publicações |
| `Posts over time` | Publicações ao longo do tempo |
| `Preferences` | Preferências |
| `Preview Not Available` | Pré-visualização indisponível |
| `Previous Attachment` | Anexo anterior |
| `Previous Author` | Autor anterior |
| `Previous Post` | Publicação anterior |
| `Previous catch-ups` | Catch Ups anteriores |
| `Profile` | Perfil |
| `Public` | Público |
| `Query` | Consulta |
| `Quit` | Sair |
| `Quote` | Citar |
| `Quote Edits` | Edições de citações |
| `Quote Post` | Citar publicação |
| `Quotes` | Citações |
| `Read More` | Ler mais |
| `Reading` | Leitura |
| `Recently Used` | Usados recentemente |
| `Recently used` | Usada recentemente |
| `Refresh` | Atualização |
| `Refresh Results` | Atualizar resultados |
| `Refresh failed` | Falha ao atualizar |
| `Reject` | Rejeitar |
| `Relevance` | Relevância |
| `Reload` | Recarregar |
| `Reload Page` | Recarregar página |
| `Remove Account` | Remover conta |
| `Remove Attachment` | Remover anexo |
| `Remove This Catch-Up` | Remover este Catch Up |
| `Replies` | Respostas |
| `Reply` | Responder |
| `Report sent` | Denúncia enviada |
| `Reports` | Denúncias |
| `Request accepted` | Pedido aceito |
| `Request rejected` | Pedido rejeitado |
| `Requested` | Solicitado |
| `Require Follow Requests` | Exigir pedidos para seguir |
| `Reschedule` | Reagendar |
| `Reset Text Size` | Redefinir tamanho do texto |
| `Restore` | Restaurar |
| `Save As…` | Salvar como… |
| `Save Media` | Salvar mídia |
| `Scanning timeline…` | Analisando a linha do tempo… |
| `Schedule (Ctrl+Enter)` | Agendar (Ctrl+Enter) |
| `Scheduled Posts` | Publicações agendadas |
| `Scroll Page Down` | Rolar página para baixo |
| `Scroll Page Up` | Rolar página para cima |
| `Scroll to Top` | Rolar para o topo |
| `Search` | Pesquisar |
| `Search Custom Emoji` | Pesquisar emoji personalizado |
| `Search Emoji` | Pesquisar emoji |
| `Search In` | Pesquisar em |
| `Send Post` | Enviar publicação |
| `Server` | Servidor |
| `Severed Relationships` | Relações rompidas |
| `Show Content` | Mostrar conteúdo |
| `Show Featured Tab` | Mostrar aba Destaques |
| `Show Less` | Mostrar menos |
| `Show Media Tab` | Mostrar aba Mídia |
| `Show More` | Mostrar mais |
| `Show Next Boosts` | Mostrar próximos impulsos |
| `Show Previous Boosts` | Mostrar impulsos anteriores |
| `Show Replies To` | Mostrar respostas para |
| `Show Sensitive Content` | Mostrar conteúdo sensível |
| `Show or Hide Description` | Mostrar ou ocultar descrição |
| `Sidebar` | Barra lateral |
| `Sign-Ups` | Cadastros |
| `Similar to people you followed` | Semelhante a pessoas que você seguiu |
| `Since the last catch-up` | Desde o último Catch Up |
| `Small` | Pequeno |
| `Sort by` | Ordenar por |
| `Suggest Account to Others` | Sugerir conta a outras pessoas |
| `Suggested Accounts` | Contas sugeridas |
| `Suggested Hashtags` | Hashtags sugeridas |
| `Suspended` | Suspensa |
| `System` | Sistema |
| `System language` | Idioma do sistema |
| `Take Screenshot` | Tirar Captura de Tela |
| `This conversation has no messages` | Esta conversa não tem mensagens |
| `Thread` | Conversa |
| `Time` | Hora |
| `Timelines` | Linhas do tempo |
| `Title` | Título |
| `Toggle Fit and Actual Size` | Alternar entre ajustado e tamanho real |
| `Toggle Sidebar` | Alternar barra lateral |
| `Trending` | Em alta |
| `URL copied` | URL copiada |
| `Unblock` | Desbloquear |
| `Unblock Domain` | Desbloquear domínio |
| `Unfollow` | Deixar de seguir |
| `Unlisted` | Não listado |
| `Unmute` | Deixar de silenciar |
| `Unpin from Sidebar` | Desafixar da barra lateral |
| `Unread` | Não lido |
| `Unsolicited Private Mentions` | Menções privadas não solicitadas |
| `Unverified Handle` | Identificador não verificado |
| `Verified Link` | Link verificado |
| `Video` | Vídeo |
| `View` | Ver |
| `Visible for all` | Visível para todos |
| `Visible for your followers only` | Visível somente para seus seguidores |
| `Vote failed` | Falha ao votar |
| `Warn` | Avisar |
| `Web Column` | Coluna web |
| `Welcome to Babble` | Boas-vindas ao Babble |
| `Window` | Janela |
| `Withdraw Request` | Retirar pedido |
| `You have an unsent draft` | Você tem um rascunho não enviado |
| `You interacted with them before` | Você já interagiu com esta conta |
| `Your vote` | Seu voto |
| `You’re offline` | Você está off-line |
| `Zoom In` | Aumentar zoom |
| `Zoom Out` | Diminuir zoom |
| `_Accept` | _Aceitar |
| `_Add Choice` | _Adicionar opção |
| `_Announcements` | A_núncios |
| `_Anyone` | _Qualquer pessoa |
| `_Back to Voting` | _Voltar à votação |
| `_Cancel` | _Cancelar |
| `_Catch Up` | _Catch Up |
| `_Clear` | _Limpar |
| `_Clear Schedule` | _Remover agendamento |
| `_Follow` | _Seguir |
| `_Follow Back` | _Seguir de volta |
| `_Load` | Ca_rregar |
| `_New List` | _Nova lista |
| `_Nobody` | _Ninguém |
| `_Open` | _Abrir |
| `_Open in Browser` | _Abrir no navegador |
| `_Post` | _Publicar |
| `_Reject` | _Rejeitar |
| `_Remember This Choice` | _Lembrar esta escolha |
| `_Reset Filters` | _Redefinir filtros |
| `_Save` | _Salvar |
| `_Schedule` | _Agendar |
| `_Try Again` | _Tentar novamente |
| `_Unfollow` | _Deixar de seguir |
| `_View` | _Ver |
| `_View Reports` | _Ver denúncias |
| `_Vote` | _Votar |
| `following` | seguindo |
| `media` | mídia |

## 10.6 Mensagens e avisos

As mensagens do aplicativo são as que o servidor devolve, mais as do próprio Babble. As tabelas abaixo são de consulta.

### Login e sessão

| Mensagem | Onde aparece |
|---|---|
| `Remove Account?` | Ao remover uma conta do aparelho. |
| `The login for @conta will be removed from this device.` | O corpo dessa confirmação. |
| `Enter Authorization Code…` | O botão que permite colar o código à mão. |
| `Restart Babble to use the new language` | O aviso depois de escolher o idioma da interface. |

### Publicação

| Mensagem | Significado |
|---|---|
| `Post sent` | A publicação saiu. |
| `Post edited` | A publicação foi editada. |
| `Post scheduled` | A publicação foi agendada. |
| `Post deleted` | A publicação foi apagada; o toast oferece `Undo`. |
| `Post is too long` | O texto passou do limite do servidor. |
| `Add content warning text` | O aviso de conteúdo está ligado e vazio. |
| `Add at least two poll choices` | A enquete precisa de duas opções. |
| `Poll choices must be unique` | Duas opções com o mesmo texto. |
| `A poll cannot be combined with media` | Enquete e anexo não convivem. |
| `Could not load post for editing` | O servidor não devolveu a publicação para edição. |
| `Vote failed` / `Refresh failed` | Falha ao votar ou ao atualizar o resultado. |
| `Could not translate` | O servidor não conseguiu traduzir. |
| `Could not mute conversation` / `Could not unmute conversation` | Falha ao silenciar ou reativar um fio. |
| `Could not pin post` / `Could not unpin post` | Falha ao fixar uma publicação no perfil. |
| `Could not change quote policy` | Falha ao mudar quem pode citar. |

### Mídia

| Mensagem | Significado |
|---|---|
| `Attachment removed` | O anexo foi removido; o toast oferece `Undo`. |
| `Only images, videos and audio can be attached` | Tipo de arquivo não suportado. |
| `A post can have at most {n} attachments` | Limite de anexos do servidor. |
| `A video or audio file must be the only attachment` | Vídeo e áudio vão sozinhos. |
| `Wait for the post to be sent to attach files` | Há uma publicação em andamento. |
| `Could not paste the image` / `Could not paste the files` | A área de transferência não tinha nada utilizável. |
| `Could not take a screenshot` | O portal recusou a captura. |
| `Could not open the file picker` | O seletor de arquivos não abriu. |
| `“{nome}” is too large for this server ({limite} is the limit)` | O arquivo passa do limite do servidor. |
| `The server is still processing the media` | O servidor está processando; o Babble espera até dois minutos. |
| `The server did not accept the media` | O servidor recusou o arquivo. |
| `Could not upload media` | Falha no envio. |
| `Could not save media` | Falha ao gravar o arquivo. |
| `Saved “{nome}”` | O arquivo foi salvo; o toast oferece `Show in Folder`. |
| `Could not open media` | O "Abrir com…" não encontrou um aplicativo. |
| `Media Still Processing` | O servidor ainda está processando o arquivo. |
| `Preview Not Available` | Esse tipo de anexo não pode ser mostrado aqui. |
| `Could Not Load Image` / `Could Not Load Media` | A mídia não carregou. |
| `Media Not Stored by Your Server` | O seu servidor não tem cópia, e você recusou carregar do servidor de origem. |
| `Loading media from other servers is turned off` | A configuração `Never Load` está ativa. |
| `Load Media From Another Server?` | O pedido de consentimento. |
| `Your server has no copy of this media. Loading it from {host} lets that server see your IP address and that you viewed it.` | O corpo do pedido de consentimento. |

### Notificações e mensagens

| Mensagem | Significado |
|---|---|
| `Request accepted` / `Request rejected` | Resposta a um pedido para seguir. |
| `Notification request accepted` / `Notification request dismissed` | Decisão sobre uma notificação retida. |
| `Notification settings updated` | As regras do servidor foram salvas. |
| `Could not save notification settings` | Falha ao salvar. |
| `Could not mark as read` | Falha ao marcar a conversa como lida. |
| `Conversation deleted` | A conversa saiu da sua caixa. |
| `This conversation has no messages` | A conversa está vazia. |

### Contato e moderação

| Mensagem | Significado |
|---|---|
| `Mute failed` / `Muted {conta}` | Silenciar a conta. |
| `Blocked {conta}` | Bloquear a conta. |
| `Report sent` | A denúncia foi enviada. |
| `Link copied` | O endereço foi copiado. |
| `URL copied` / `Image copied` | No visualizador de mídia. |
| `Copy “{…}” Post Link?` | A confirmação antes de copiar o link de uma publicação privada. |

### Plugin

| Mensagem | Significado |
|---|---|
| `Plugins Are Unavailable` | O `libpeas 2` não está instalado. |
| `Babble needs libpeas 2 and its Python loader to load plugins` | O corpo da mensagem. |

### Catch Up

| Mensagem | Significado |
|---|---|
| `Scanning timeline…` | A varredura está em andamento. |
| `The server is limiting requests, so this catch-up is incomplete. Try again in {n} minutes.` | O servidor limitou a taxa de requisições. |
| `The server limited requests, so this catch-up is incomplete.` | O mesmo aviso, sem prazo. |
| `This catch-up could not be saved, so it will not be here next time.` | O instantâneo não pôde ser gravado. |
| `Remove This Catch-Up?` | Ao remover um instantâneo. |
| `It will be deleted from this device.` | O corpo dessa confirmação. |
| `At most {n} hashtags can be combined` | Limite de hashtags de uma timeline combinada. |

### Conexão

| Mensagem | Significado |
|---|---|
| `You’re offline` | A faixa mostrada quando a rede cai. |

## 10.7 Limites

Os limites vêm do servidor, e o Babble respeita o que o servidor declara.

| Limite | Origem | Valor típico |
|---|---|---|
| Caracteres por publicação | `max_characters` do servidor | 500 |
| Caracteres por URL | `characters_reserved_per_url` do servidor | 23 |
| Anexos por publicação | `max_media_attachments` do servidor | 4 |
| Caracteres da descrição da mídia | `description_limit` do servidor | 1500 |
| Opções de uma enquete | `max_options` do servidor | 4 |
| Duração de uma enquete | `min_expiration` e `max_expiration` do servidor | 30 minutos a 7 dias |
| Tamanho máximo de um anexo | O servidor | varia por tipo |
| Publicações da `Home` por página | O servidor | 800 |
| Hashtags por timeline combinada | O Babble | 5 |
| Rascunhos locais guardados | O Babble | 5 |
| Instantâneos de Catch Up guardados | O Babble | os três últimos, listados no menu |

### Recursos que dependem da versão do servidor

| Recurso | Versão mínima do Mastodon (API) |
|---|---|
| Coleções | 10 (4.6) |
| Abas do perfil | 8 (4.4) |
| Texto alternativo do avatar e da capa | 9 (4.6.1) |
| Endosso | 6 (4.0) |
| Timeline de link | 2 (3.5) |
| Estado do relatório anual | 8 (4.4) |
| Política de notificações | 2 (3.5) |
| Filtro com desfoque | 5 (3.7) |
| Citações | 7 (4.3) |
| Endossos | 6 (4.0) |

Quando o servidor não tem o recurso, o Babble **esconde** a opção em vez de mostrar algo quebrado. A lista completa e atualizada está em `Docs/mastodon-compat.md`.
