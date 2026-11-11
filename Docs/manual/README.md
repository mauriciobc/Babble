# Manual do Usuário do Babble

| | |
|---|---|
| **Título** | Manual do Usuário do Babble — leitura, escrita e integração com o GNOME |
| **Identificador / versão** | `BABBLE-MAN-001` — v1.0 — rascunho para revisão |
| **Data de emissão** | 2026-11-11 |
| **Autoria** | Equipe do Babble |
| **Aprovação** | [A DEFINIR: nome e data de quem aprova a publicação deste manual] |
| **Software descrito** | Babble 0.3.0 (`io.github.mauriciobc.Babble`) |
| **Normas de referência** | ISO/IEC 26514 (documentação de usuário); ISO/IEC/IEEE 15289 (identificação do item de informação) |
| **Escopo** | Cobre o uso do Babble 0.3.0 como cliente Mastodon para GNOME: entrar na conta, ler, interagir, publicar, tratar mídia, instalar e configurar plugins, e integrar o aplicativo ao ambiente de trabalho. **Não cobre** a instalação a partir do código-fonte, o desenvolvimento, a arquitetura interna, a API do Mastodon, a política de tradução nem o processo de publicação do projeto. A documentação técnica correspondente está em `Docs/building.md`, `Docs/features.md`, `Docs/mastodon-compat.md` e `Docs/desktop-integration.md`. |
| **Propósito** | Permitir que uma pessoa que usa o Babble pela primeira vez, ou que conhece o aplicativo por alto, execute as tarefas do dia a dia — ler o feed, responder, publicar, recuperar o que perdeu — sem depender de conhecimento não declarado. Destina-se ao usuário final; não é um documento de desenvolvimento. |
| **Público-alvo** | Pessoas usuárias do Babble em um desktop GNOME 51 ou posterior. |
| **Idioma** | Português (pt-BR). Os rótulos da interface são citados na forma original em inglês; o capítulo 10 traz o equivalente em português de cada rótulo citado. |
| **Rastreabilidade** | Capítulo → fonte no código: ver a tabela em [capítulo 12](12-glossario.md). |
| **Revisão** | A cada versão estável do aplicativo. Mudanças de interface que alterem um rótulo, um atalho ou um limite exigem revisão deste manual. |

## Sumário

| Capítulo | Título | Natureza da informação |
|---|---|---|
| [1](01-primeiros-passos.md) | Primeiros passos | Conceitual + procedural |
| [2](02-leitura.md) | Leitura: timelines, fios, perfis e busca | Conceitual + procedural |
| [3](03-notificacoes-e-mensagens.md) | Notificações e mensagens diretas | Conceitual + procedural |
| [4](04-escrita.md) | Escrita: o compositor, enquetes, citações e rascunhos | Conceitual + procedural |
| [5](05-midia.md) | Mídia: anexar, visualizar, salvar | Conceitual + procedural |
| [6](06-catch-up.md) | Catch Up: recuperar o que você perdeu | Conceitual + procedural |
| [7](07-integracao-com-o-desktop.md) | Integração com o ambiente GNOME | Conceitual + procedural |
| [8](08-configuracoes.md) | Preferências: referência completa | Referência |
| [9](09-plugins.md) | Plugins | Conceitual + procedural |
| [10](10-referencia.md) | Referência: atalhos, menus, mensagens e limites | Referência |
| [11](11-solucao-de-problemas.md) | Solução de problemas | Referência |
| [12](12-glossario.md) | Glossário, índice remissivo e suporte | Referência |

O manual separa três tipos de informação, como manda a ISO/IEC 26514:

- **Conceitual** — explica *o que é* e *por que*. Aparece nos capítulos 1 a 7 e 9, na forma de texto corrido.
- **Procedural** — passos numerados para *fazer* algo. Aparece em todas as seções marcadas **Como…**, com o formato *Objetivo / Pré-condições / Passos / Resultado / Se der errado*.
- **Referência** — dados consultáveis. Aparece nos capítulos 8, 10, 11 e 12, quase sempre em tabelas.

## Introdução

### Finalidade e público

Este manual acompanha o Babble 0.3.0, um cliente Mastodon para GNOME. Ele foi escrito para a pessoa que usa o aplicativo: quem quer saber *como* fazer alguma coisa com ele, e não como ele foi construído. Nenhum capítulo exige conhecimento de programação, de OAuth ou do protocolo do Mastodon.

### Escopo do produto, em uma página

O Babble é um cliente de uma rede social federada. Ele lê e publica em qualquer servidor Mastodon, e enxerga também o conteúdo que esse servidor troca com os outros servidores da federação — o que se vê como *timeline federada*.

Três blocos de função resumem o aplicativo:

1. **Leitura.** *Home* reúne as publicações das contas que você segue; *Local* mostra a timeline pública do seu servidor; *Federated* mostra a timeline pública de todos os servidores conhecidos. *Notifications* reúne menções, impulsos, favoritos e seguidores; *Conversations* guarda as mensagens diretas. *Catch Up* é a ferramenta de recuperação: ela volta no tempo e ranqueia o que você deixou de ler.
2. **Escrita.** O compositor checa ortografia, completa menções e hashtags, anexa mídia, monta enquetes, cita outras publicações, guarda rascunhos e agenda postagens.
3. **Ajustes.** Preferências, plugins e atalhos. Tudo que é preferência do aplicativo vive na janela de Preferências; tudo que é preferência do *servidor* (visibilidade padrão, idioma padrão) vive no servidor e segue você para outros clientes.

### Pré-requisitos

| Pré-requisito | Detalhe |
|---|---|
| Ambiente gráfico | GNOME 51 ou posterior (GTK 4.24+ e libadwaita 1.10+). O aplicativo também roda em outros ambientes GTK 4, mas os recursos portal e de fundo dependem do GNOME. |
| Servidor Mastodon | Uma conta em qualquer servidor Mastodon. Para testar, `mastodon.social` serve. |
| Serviço de chaveiro | Para guardar o token de acesso. Em GNOME, o GNOME Keyring. Sem ele o aplicativo avisa e não consegue guardar a sessão. |
| Navegador | Usado uma única vez, no login, para autorizar o aplicativo no servidor. |
| Rede | Necessária para o servidor; o aplicativo funciona sem rede e avisa quando ela volta. |

O aplicativo **não** é um navegador disfarçado nem uma central de alterações: ele fala com a API do Mastodon diretamente, e o navegador aparece apenas na autorização inicial e quando você abre um link fora do aplicativo.

### Convenções de notação

| Notação | Significado |
|---|---|
| `New Post` | Nome de um elemento da interface, exatamente como aparece no código-fonte (em inglês). O capítulo 10 traz o equivalente em português de cada um deles. |
| **Ctrl+N** | Combinação de teclas. Teclas modificadoras são escritas como aparecem no GNOME. |
| *Termo em itálico* | Conceito definido no [glossário](12-glossario.md). |
| `~/.local/share/babble/plugins` | Caminho literal no disco. |
| [capítulo 10](10-referencia.md) | Referência cruzada. |

**Por que os rótulos aparecem em inglês.** O manual cita os rótulos exatamente como o código-fonte os define, porque é assim que a interface é construida e traduzida: a tradução de tela vive em `po/`, e a entrada original é a referência. O aplicativo está traduzido para alemão, espanhol, francês e português do Brasil; um leitor com a interface em português verá, por exemplo, `New Post` como *Nova publicação*. A [tabela de rótulos do capítulo 10](10-referencia.md#105-rótulos-da-interface-em-inglês-e-em-português) relaciona os dois.

## Conceitos

Esta seção é o modelo mental do aplicativo. Leia antes do capítulo 1 se você nunca usou o Babble; pule se já conhece Mastodon.

### O que é uma rede federada

Uma rede federada é um conjunto de servidores independentes que trocam mensagens entre si.

- Um **servidor** (o Mastodon chama de *instance*) é uma instalação com seus próprios usuários, suas próprias regras e seu próprio banco de dados. `mastodon.social` e `hachyderm.io` são servidores diferentes.
- Uma **conta** pertence a um único servidor. O mesmo endereço de e-mail em dois servidores são duas contas diferentes, e os identificadores numéricos podem colidir — por isso o Babble sempre identifica uma conta pelo par *servidor + identificador*.
- A **federação** é o que circula entre os servidores: quando você segue alguém de outro servidor, as publicações dele chegam ao seu servidor, e o seu servidor as repassa para você.

Duas consequências práticas aparecem o tempo todo neste manual:

- **Nem tudo existe em toda parte.** Uma enquete, uma citação ou um painel de moderação depende da versão do Mastodon do seu servidor. O Babble esconde o que o servidor não suporta em vez de mostrar um botão quebrado. A lista do que depende de quê está em `Docs/mastodon-compat.md`.
- **A mídia nem sempre está no seu servidor.** Quando seu servidor não tem cópia de uma imagem que veio de outro servidor, carregá-la avisa o servidor de origem que você a viu, e por isso há uma permissão específica para isso — capítulo 5.

### Publicações, e o que se pode fazer com elas

Uma **publicação** (*post*) é a unidade de conteúdo. Ela tem autor, texto, hora, e pode ter mídia, enquete, aviso de conteúdo e citação.

| Termo | O que significa na prática |
|---|---|
| **Publicação** (*post*, *status*) | O post. |
| **Fio** (*thread*) | Uma publicação e suas respostas. Um fio tem ancestrais acima e respostas aninhadas abaixo. |
| **Resposta** (*reply*) | Uma publicação que responde a outra. Aparece com a linha "Replying to @conta". |
| **Impulso** (*boost*, *reblog*) | Republicar a publicação de outra pessoa no seu feed. |
| **Favorito** (*favorite*) | Uma curtida. É privado: ninguém é avisado. |
| **Item salvo** (*bookmark*) | Salvar para ler depois. Privado, como o favorito. |
| **Citação** (*quote*) | Publicar um comentário junto com a publicação citada, para os seus seguidores. |
| **Menção** (*mention*) | O `@conta` dentro de uma publicação. Vira notificação para quem foi citado. |
| **Hashtag** | O `#assunto` dentro de uma publicação. Cria uma página com todas as publicações que o usam. |
| **Lista** | Um conjunto nomeado de contas, com a sua própria timeline. |
| **Aviso de conteúdo** (*content warning*, CW) | Um texto que esconde o conteúdo até você pedir para ver. |
| **Descrição** (*alt text*) | A descrição textual de uma imagem, anexada à mídia. É o que um leitor de tela lê e o que quem não enxerga a imagem usa. |
| **Conteúdo sensível** | Marcador de mídia que a cobre até ser revelado. |

Os quatro verbos principais — `Reply`, `Boost`, `Quote`, `Favorite` — e o `Bookmark` estão na barra de ações de cada publicação. Se você desativar *Post Actions on Timelines* em Preferências, eles migram para o menu `More Options` da publicação, mas continuam disponíveis.

### A janela: barra lateral, páginas e sobreposições

A janela do Babble tem três camadas, e vale saber em qual delas você está quando algo acontece:

1. **Barra lateral.** A coluna de destinos: `Home`, `Catch Up`, `Notifications`, `Conversations`, `Search`, `Favorites`, `Bookmarks`, `Hashtags`, `Explore`, `Local`, `Federated`, `Lists`, mais as listas e hashtags que você fixar. No topo fica o botão da conta, que troca de conta; no rodapé, o botão que muda o modo da barra lateral.
2. **Página.** O conteúdo do destino escolhido. Páginas como perfil, fio e busca abrem por cima, e `Alt+Left` volta.
3. **Sobreposição.** O visualizador de mídia e o visualizador de página web não são janelas: eles cobrem a janela principal e o botão de fechar sempre está à mão. Os *toasts* — as faixas que aparecem na parte de baixo — são a única coisa que aparece por cima de tudo.

A barra lateral tem três modos, escolhidos no botão do rodapé (`Sidebar`): `Full Sidebar` (com rótulos), `Icons Only` (uma faixa de ícones) e `Hidden` (some da tela; os botões `Sidebar`, `Main Menu`, `New Post` e `Notifications` passam a ficar no cabeçalho de cada página).

### Atualização ao vivo e recarregamento

- **Atualização ao vivo** entrega publicações e notificações no instante em que acontecem, por uma conexão permanente. Está ligada por padrão, e é desligada em Preferências → Timelines → *Live Timelines*.
- **Recarregamento automático** é o substituto quando o ao vivo não está disponível: a página busca de novo a cada 30 segundos, a cada minuto ou a cada 5 minutos, conforme a configuração.
- **Puxar para baixo** no topo de uma página recarrega na hora, em qualquer modo.

Quando chegam publicações novas enquanto você está rolado mais abaixo, aparece um botão `N New Posts` no topo da lista; clicar nele leva ao início.

### A conta é sempre um par

Toda conta no Babble é `(servidor, identificador)`. Duas contas com o mesmo identificador em servidores diferentes são contas diferentes, e o aplicativo nunca as confunde. Isso aparece na interface de troca de conta, que mostra avatar, nome e `@conta@servidor` de cada uma.

### Onde as coisas ficam guardadas

| O quê | Onde fica |
|---|---|
| Token de acesso e credenciais OAuth | Chaveiro do sistema (GNOME Keyring ou equivalente) |
| Lista de contas e conta ativa | GSettings, no `dconf` |
| Rascunhos locais (até 5, por conta) | Arquivos no diretório de dados do aplicativo |
| Rascunhos, postagens agendadas, listas, filtros | No seu servidor Mastodon |
| Instantâneos do Catch Up | No seu computador, por conta |
| Preferências | GSettings; as de servidor, no servidor |
| Capturas coladas e arquivos temporários | Cache do aplicativo |

O ponto que importa: **nada disso é um arquivo de senha**. O Babble não guarda a sua senha do servidor, nunca. A senha é digitada na página do próprio servidor, no navegador.

## Histórico de revisões

| Versão | Data | Autor | Alterações |
|---|---|---|---|
| 1.0 | 2026-11-11 | Equipe do Babble | Versão inicial, redigida a partir do Babble 0.3.0 (`main` em 2026-11-11). |
