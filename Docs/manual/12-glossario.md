# 12 — Glossário, índice remissivo e suporte

> **Documento:** `BABBLE-MAN-001` — capítulo 12 — v1.0 — aprovado
> **Software descrito:** Babble 0.3.0 · **Data:** 2026-11-11 · **Pai:** [Manual do Usuário do Babble](README.md)

## 12.1 Glossário

**Avatar** — a imagem de perfil de uma conta. No Babble, também é o botão que abre a conta.

**Aviso de conteúdo (content warning, CW)** — um texto que esconde o conteúdo da publicação até o leitor pedir para vê-lo.

**Bloqueio** — impedir que uma conta ou um servidor inteiro interaja com a sua. As publicações ficam ocultas, e a conta não consegue seguir você nem ver o que você publica.

**Boost (impulso)** — republicar a publicação de outra pessoa na sua timeline. Também é o nome do botão que faz isso.

**Bookmark (item salvo)** — salvar uma publicação para ler depois. Privado.

**Cabeçalho de capa** — a imagem larga no topo de um perfil.

**Campo de perfil** — uma linha de informação personalizada no perfil (site, pronouns, whatever the account chose).

**Catch Up** — a visão separada que volta no tempo na sua timeline e ranqueia o que você deixou de ler. [Capítulo 6](06-catch-up.md).

**Chaveiro (keyring)** — o serviço do sistema que guarda dados secretos, como o token de acesso. No GNOME, o GNOME Keyring.

**Coluna web** — a área dentro do aplicativo que mostra uma página da web ao lado da timeline. [Capítulo 7](07-integracao-com-o-desktop.md).

**Composer** — a caixa de diálogo onde se escreve uma publicação. [Capítulo 4](04-escrita.md).

**Conteúdo sensível** — a marcação que cobre uma mídia até o leitor revelá-la.

**Conversa direta** — uma conversa privada, com as pessoas que você mencionar e que mencionarem você.

**Descrição (alt text)** — o texto que descreve uma imagem anexada, para quem não enxerga a imagem ou usa um leitor de tela.

**Domínio** — o endereço de um servidor, como `mastodon.social`. Também é o que se bloqueia, quando você bloqueia um servidor inteiro.

**Enquete (poll)** — uma publicação com opções de escolha e prazo de encerramento.

**Escopo (scope)** — uma permissão pedida ao servidor na autorização. O login normal pede `read` e `write`.

**Favo** — o favorito. Uma curtida privada.

**Feed local (timeline local)** — a timeline pública do seu servidor.

**Feed federado (timeline federada)** — a timeline pública de todos os servidores que o seu conhece.

**Fio (thread)** — uma publicação e as respostas a ela.

**Filtro** — uma regra do servidor que esconde, avisa ou desfoca publicações que casam com uma palavra-chave.

**Favorito (favorite)** — ver **Favo**.

**Hashtag** — a `#assunto` dentro de uma publicação, que cria uma página com todas as que a usam.

**Impulso** — ver **Boost**. É também a republicação de uma publicação de outra pessoa, que aparece na sua timeline como se fosse de quem impulsionou.

**Instantâneo (snapshot)** — a cópia congelada de um Catch Up, gravada no seu computador.

**Item salvo** — ver **Bookmark**.

**Lista** — um conjunto nomeado de contas, com uma timeline só dela.

**Lugares (places)** — os destinos da barra lateral. Cada lugar tem uma página, construída na primeira visita.

**Menção** — o `@conta` dentro de uma publicação, que notifica a pessoa.

**OAuth** — o protocolo de autorização que permite ao Babble usar sua conta sem saber a sua senha.

**PKCE** — a extensão do OAuth que impede que um código de autorização roubadado seja usado em outro aplicativo.

**Contadores de interação** — as contagens de respostas, impulsos e favoritos na barra de ações.

**Portal** — a interface que um aplicativo usa para pedir ao sistema algo que ele não pode fazer sozinho (captura de tela, segundo plano, atalhos globais).

**Publicação (post)** — a unidade de conteúdo do Mastodon.

**Publicação direta** — a que só as pessoas mencionadas veem.

**Quote (citação)** — comentar uma publicação mantendo-a à vista dos seus seguidores.

**Rascunho** — o texto, a mídia e a enquete guardados pelo Babble antes de publicar. Há rascunhos locais (no seu computador) e rascunhos do servidor.

**Servidor (instance)** — uma instalação do Mastodon, com os seus próprios usuários e as suas próprias regras.

**Silenciamento (mute)** — esconder as publicações de uma conta, sem que ela saiba.

**Streaming (atualização ao vivo)** — a conexão permanente com o servidor, que entrega publicações e notificações no instante em que acontecem.

**Thread** — ver **Fio**.

**Token** — a credencial que o servidor devolve na autorização. É o que o Babble guarda, e o que dá acesso à sua conta.

**Toast** — a faixa de mensagem que aparece na parte de baixo da janela, sobre o conteúdo.

**Timeline** — a lista de publicações, da mais recente para a mais antiga.

**Tradução automática** — a tradução que o servidor faz de uma publicação para o idioma da interface.

**Verificador ortográfico** — o corretor do compositor, que segue o idioma escolhido para a publicação.

**Visibility (visibilidade)** — quem pode ver a publicação: `Public`, `Unlisted`, `Followers only` ou `Direct`.

### Siglas

| Sigla | Significado |
|---|---|
| API | Interface de Programação de Aplicativos — o contrato pelo qual o Babble fala com o servidor. |
| CW | *Content Warning*, aviso de conteúdo. |
| D-Bus | O barramento de mensagens entre aplicativos do GNOME. |
| GNOME | O ambiente de trabalho para o qual o Babble foi feito. |
| GTK | A biblioteca de interface sobre a qual o Babble é construído. |
| IP | O endereço de rede do seu computador. |
| OAuth | O protocolo de autorização (ver acima). |
| PKCE | *Proof Key for Code Exchange* (ver acima). |
| SNI | Nome do servidor, usado para o TLS. |
| UI | *User Interface*, a interface. |
| URL | O endereço de uma página ou recurso. |
| XDG | O padrão de caminhos e portais do GNOME e do Linux. |

## 12.2 Índice remissivo

| Assunto | Onde está |
|---|---|
| Agendar publicação | [4.14](04-escrita.md#414-agendar-uma-publicação) |
| Alinhar (boost) | [2.4](02-leitura.md#24-interagir-com-uma-publicação) |
| Anúncios do servidor | [3.7](03-notificacoes-e-mensagens.md#37-anúncios-do-servidor) |
| Anexar mídia | [5.1](05-midia.md#51-quatro-maneiras-de-anexar) |
| Aviso de conteúdo | [4.8](04-escrita.md#48-aviso-de-conteúdo), [2.6](02-leitura.md#26-avisos-de-conteúdo-e-publicações-longas) |
| Barra lateral | [2.1](02-leitura.md#21-a-barra-lateral) |
| Bloquear | [2.8](02-leitura.md#28-o-perfil-de-outra-pessoa), [3.6](03-notificacoes-e-mensagens.md#36-silenciados-e-bloqueados) |
| Captura de tela | [5.1](05-midia.md#51-quatro-maneiras-de-anexar), [7.5](07-integracao-com-o-desktop.md#75-captura-de-tela) |
| Carrossel de impulsos | [2.15](02-leitura.md#215-publicações-repetidas-o-carrossel-de-impulsos) |
| Catch Up | [6](06-catch-up.md) |
| Chaveiro | [1.1](01-primeiros-passos.md#11-como-funciona-a-entrada), [11.3](11-solucao-de-problemas.md#113-o-chaveiro-não-guarda-o-login) |
| Citar | [4.10](04-escrita.md#410-citar-uma-publicação) |
| Coluna web | [7.1](07-integracao-com-o-desktop.md#71-a-coluna-web-ao-lado-da-timeline) |
| Contadores de interação | [2.4](02-leitura.md#24-interagir-com-uma-publicação) |
| Contas, adicionar e trocar | [1.3](01-primeiros-passos.md#13-adicionar-trocar-e-remover-contas) |
| Conversas diretas | [3.4](03-notificacoes-e-mensagens.md#34-mensagens-diretas) |
| Descrição de mídia | [5.2](05-midia.md#52-a-descrição-da-mídia) |
| Emoji | [4.7](04-escrita.md#47-emoji) |
| Enquete | [4.9](04-escrita.md#49-enquetes) |
| Esconder um lugar da barra lateral | [8.4](08-configuracoes.md#84-página-timelines) |
| Explorar | [2.12](02-leitura.md#212-explorar) |
| Filtros do servidor | [2.7](02-leitura.md#27-filtros-do-servidor), [8.3](08-configuracoes.md#83-página-reading) |
| Fio | [2.3](02-leitura.md#23-abrir-um-fio) |
| Favoritar | [2.4](02-leitura.md#24-interagir-com-uma-publicação) |
| Arrastar mídia para fora | [5.5](05-midia.md#55-zoom-e-arrasto) |
| Fotos, ver | [5.4](05-midia.md#54-o-visualizador-de-mídia) |
| Ganchos de teclado | [10.1](10-referencia.md#101-atalhos-de-teclado) |
| Hashtags | [2.11](02-leitura.md#211-hashtags), [4.6](04-escrita.md#46-hashtags) |
| Idioma da interface | [8.2](08-configuracoes.md#82-página-appearance) |
| Instalar o aplicativo | `Docs/building.md` |
| Listas | [2.10](02-leitura.md#210-listas) |
| Live updates | [2.2](02-leitura.md#22-a-timeline-home), [8.4](08-configuracoes.md#84-página-timelines) |
| Local e Federated | [2.14](02-leitura.md#214-favoritos-itens-salvos-local-e-federated) |
| Login | [1.2](01-primeiros-passos.md#12-entrar-pela-primeira-vez) |
| Menções | [4.5](04-escrita.md#45-menções) |
| Mensagens de erro | [10.6](10-referencia.md#106-mensagens-e-avisos), [11](11-solucao-de-problemas.md) |
| Mídia de outros servidores | [5.7](05-midia.md#57-mídia-que-está-em-outro-servidor) |
| Notificações | [3.1](03-notificacoes-e-mensagens.md#31-a-página-notifications) |
| Notificações do desktop | [3.2](03-notificacoes-e-mensagens.md#32-filtrar-o-que-aparece) |
| Perfil | [2.8](02-leitura.md#28-o-perfil-de-outra-pessoa), [2.9](02-leitura.md#29-o-seu-próprio-perfil) |
| Plugins | [9](09-plugins.md) |
| Posição de leitura | [2.2](02-leitura.md#22-a-timeline-home) |
| Preferências | [8](08-configuracoes.md) |
| Rascunhos | [4.13](04-escrita.md#413-rascunhos) |
| Recarregar | [2.2](02-leitura.md#22-a-timeline-home) |
| Referência de rótulos | [10.5](10-referencia.md#105-rótulos-da-interface-em-inglês-e-em-português) |
| Relatório anual (Wrapped) | [10.2](10-referencia.md#102-o-menu-principal) |
| Salvar mídia | [5.6](05-midia.md#56-salvar-uma-mídia) |
| Search (busca) | [2.13](02-leitura.md#213-busca) |
| Silenciar | [3.6](03-notificacoes-e-mensagens.md#36-silenciados-e-bloqueados) |
| Sobre | [10.2](10-referencia.md#102-o-menu-principal) |
| Timeline do link | [2.12](02-leitura.md#212-explorar) |
| Traduzir publicação | [2.6](02-leitura.md#26-avisos-de-conteúdo-e-publicações-longas) |
| Verificador ortográfico | [4.3](04-escrita.md#43-publicar), [7.6](07-integracao-com-o-desktop.md#76-verificação-ortográfica) |
| Versão do servidor | [10.7](10-referencia.md#107-limites), [11.12](11-solucao-de-problemas.md#1112-um-recurso-não-aparece) |
| Zoom | [5.5](05-midia.md#55-zoom-e-arrasto) |

## 12.3 Rastreabilidade: onde cada capítulo está no código

Cada capítulo do manual corresponde a um conjunto de módulos do aplicativo. É por aí que se verifica se o manual ainda descreve o que o aplicativo faz.

| Capítulo | Módulos correspondentes |
|---|---|
| 1 — Primeiros passos | `src/auth.py`, `src/accounts.py`, `src/main.py`, `src/window.py` |
| 2 — Leitura | `src/timelinepage.py`, `src/statusrow.py`, `src/statusfold.py`, `src/statusactions.py`, `src/statusmenu.py`, `src/statusviews.py`, `src/profile.py`, `src/profileactions.py`, `src/explore.py`, `src/hashtags.py`, `src/lists.py`, `src/search.py`, `src/filterrules.py`, `src/statustext.py` |
| 3 — Notificações e mensagens | `src/notifications.py`, `src/notificationrow.py`, `src/notificationctrl.py`, `src/notificationcopy.py`, `src/notificationpolicy.py`, `src/conversations.py`, `src/announcements.py`, `src/relations.py` |
| 4 — Escrita | `src/compose.py`, `src/composeattach.py`, `src/composeupload.py`, `src/composequote.py`, `src/composeschedule.py`, `src/drafts.py`, `src/scheduled.py`, `src/mentioncompleter.py`, `src/hashtagcompleter.py`, `src/emojichooser.py`, `src/pollbox.py` |
| 5 — Mídia | `src/mediabox.py`, `src/mediaviewer.py`, `src/mediaviewerchrome.py`, `src/mediazoom.py`, `src/mediaexport.py`, `src/mediainline.py`, `src/mediaplayback.py`, `src/remotemedia.py` |
| 6 — Catch Up | `src/catchup/` (`rank.py`, `rank_model.py`, `rank_text.py`, e os demais módulos do pacote) |
| 7 — Integração com o GNOME | `src/portals.py`, `src/clipboard.py`, `src/desktopnotify.py`, `src/searchprovider.py`, `src/mediabox.py`, `src/composeattach.py`, `src/sysstate.py` |
| 8 — Preferências | `src/preferences.py`, `data/io.github.mauriciobc.Babble.gschema.xml` |
| 9 — Plugins | `src/plugin.py`, `src/pluginhost.py`, `src/pluginspage.py`, `plugins/` |
| 10 — Referência | `src/main.py`, `src/shortcuts-dialog.ui`, `src/window.ui`, `src/capabilities.py`, `po/pt_BR.po` |
| 11 — Solução de problemas | Todos os módulos acima, mais `src/accounts.py` e o log do sistema |
| Ajuda embutida (F1) | `data/help/C/` (inglês) e `data/help/pt_BR/` (português), registrados em `help_languages`, em `data/meson.build` |
| O rito que rege este manual | `Docs/documentation-policy.md` (a política do projeto), verificada por `src/test-docs.py` |

## 12.4 Suporte

| Onde | O que você encontra |
|---|---|
| **F1**, ou `Help` no menu principal | A ajuda instalada, dentro do aplicativo. Resume o essencial. |
| `data/help/pt_BR/` | As páginas de ajuda em português, instaladas ao lado das de inglês. |
| `data/help/C/` | As páginas de ajuda em inglês. |
| Este manual | A referência completa, com procedimentos, mensagens e limites. |
| `Docs/features.md` | A lista de recursos do projeto, em termos técnicos. |
| `Docs/mastodon-compat.md` | Onde cada recurso depende da versão do servidor. |
| `Docs/desktop-integration.md` | O que cada integração com o GNOME precisa. |
| `Docs/building.md` | Como compilar, instalar e rodar os testes. |
| `github.com/mauriciobc/Babble` | O código-fonte e a página do projeto. |
| A página de problemas do projeto | Relatar um defeito. |

**Ao relatar um defeito**, inclua: a versão do aplicativo (em `About Babble`), a versão do seu servidor Mastodon, o que você fez, o que esperava e o que aconteceu. Se o problema for de login, de conta ou de plugin, inclua também a saída de `journalctl --user -g 'accounts:'`.

## 12.5 Manutenção deste manual

| Regra | Motivo |
|---|---|
| Um rótulo citado no manual tem de existir no código | O manual cita rótulos em inglês; um rótulo que muda quebra a correspondência. |
| As páginas de ajuda e o manual não divergem | A ajuda (`data/help/`) é um resumo; este manual é a referência. As duas traduções de cada página de ajuda existem lado a lado, em `data/help/C/` e `data/help/pt_BR/`, com os mesmos identificadores de seção — mudar uma é mudar a outra. |
| Um atalho citado tem de estar registrado | Os atalhos vivem em `src/main.py` e em `src/shortcuts-dialog.ui`. |
| Um limite citado tem de vir do servidor | Os limites são declarados pela instância, e o manual os descreve como tal. |
| A tabela de rótulos é gerada a partir de `po/pt_BR.po` | Ela reflete o que está traduzido de fato na versão instalada. |
| Mudar de versão do aplicativo obriga a revisar o manual | Recursos novos podem entrar em qualquer capítulo. |

O manual é atualizado junto com o aplicativo. A versão e a data estão no [cabeçalho](README.md), e o histórico de revisões fica ao lado dele.

O **processo** que rege estas regras — quem escreve, quem revê, quem aprova, o que é uma baseline, quando um documento é revisto e quando é retirado — não está neste manual, mas na [política de documentação](../documentation-policy.md). Ela é a política do projeto, e o manual é um dos seus itens formais. As duas coisas que a política promete são verificadas por `src/test-docs.py`, que corre na CI:

- as páginas de ajuda em todos os idiomas carregam os mesmos ficheiros e os mesmos identificadores, e as referências cruzadas resolvem-se dentro de cada conjunto;
- cada idioma presente em `data/help/` está registado em `help_languages`, e cada ficheiro que a instalação instala existe em cada idioma;
- cada capítulo do manual carrega o bloco de identificação, e todas as ligações e âncoras resolvem-se;
- a tabela de rótulos do capítulo 10 é a que o catálogo de tradução produz.

Para regenerar a tabela depois de uma mudança no catálogo:

```sh
python3 src/test-docs.py --write-table
```
