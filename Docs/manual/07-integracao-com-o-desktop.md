# 7 — Integração com o ambiente GNOME

> **Documento:** `BABBLE-MAN-001` — capítulo 7 — v1.0 — rascunho para revisão
> **Software descrito:** Babble 0.3.0 · **Data:** 2026-11-11 · **Pai:** [Manual do Usuário do Babble](README.md)

O Babble foi feito para o GNOME, e usa o que o GNOME oferece em vez de recriar. Este capítulo lista cada integração, o que ela faz e o que ela precisa.

## 7.1 A coluna web ao lado da timeline

**Objetivo:** ler uma página da web sem sair do Babble.

**Pré-condições:** o WebKitGTK está instalado, e a preferência `Web Links Beside the Timeline` está ligada (Preferências → Reading → grupo `Links`).

1. Clique em um link de uma publicação que não seja um link de post nem de perfil.
2. A página abre numa coluna ao lado da timeline — ou sobre ela, se a janela for estreita demais.

**Resultado:** a coluna tem um cabeçalho com `Hide Web Column`, `Back`, `Forward`, o título da página e um menu `Page Actions` (`Reload`, `Open in Browser`, `Copy Link`).

**O que a coluna não faz:**

**Se der errado:** se o link abrir no navegador mesmo com a preferência ligada, o WebKitGTK não está instalado — nesse caso a opção nem aparece nas Preferências. Se a página abrir em branco, desligue `JavaScript on Web Pages` e recarregue; muitos sites dependem dele. Veja [11.12](11-solucao-de-problemas.md#1112-um-recurso-não-aparece).

**O que a coluna não faz:**

- Não guarda cookies nem histórico depois que o Babble fecha.
- Não pede sua localização, sua câmera nem seu microfone.
- Não baixa arquivos: um download é entregue ao seu navegador.
- Não executa JavaScript, a menos que você ligue `JavaScript on Web Pages` — e muitos sites não funcionam sem ele.

O item `Open in Browser` do menu da coluna leva a página que você está lendo ao navegador.

**Atalhos da coluna:** **Ctrl+R** e **F5** recarregam, **Alt+Left** e **Alt+Right** navegam, e **Escape** ou **Ctrl+W** escondem a coluna.

## 7.2 Como os links abrem

O Babble distingue três tipos de link:

| Tipo | Onde abre |
|---|---|
| Link de post ou de perfil, em qualquer servidor | **Dentro do Babble.** O fio ou o perfil é aberto na janela. |
| Qualquer outra página da web | No navegador, ou na coluna lateral, se você ligou `Web Links Beside the Timeline`. |
| Link para um servidor Blusky (post do bsky.app, ou post vindo pela ponte *Bridgy Fed*) | **Dentro do Babble**, se `Bluesky Posts` estiver ligado. Desligado, abre no navegador. |

A preferência `Remote Links in Babble` decide se links de post e de perfil de outros servidores abrem dentro do aplicativo. Para mandar um link ao navegador de qualquer jeito, use **Ctrl+clique** ou o botão do meio.

A preferência `Tracking Parameter Removal` remove parâmetros de rastreamento dos links antes de você clicá-los. Ligada por padrão, ela **pode quebrar alguns links** — é por isso que a descrição avisa.

## 7.3 Área de transferência e arrastar

O Babble participa dos dois gestos de transferência do GNOME:

- **Colar** — **Ctrl+V** no compositor cola arquivos e imagens copiados. Uma imagem isolada vira um PNG chamado `Pasted image`.
- **Arrastar** — você pode arrastar arquivos para dentro do compositor, e arrastar **publicações, imagens, vídeos e endereços** para fora, para outro aplicativo. Ao arrastar a imagem de um post, o que vai junto é o arquivo e o endereço dele. Ao arrastar a própria publicação, o que vai é o endereço.

A preferência `Copied Link Suggestions` faz o Babble olhar a área de transferência quando recebe foco, e oferecer abrir o post ou o perfil que você acabou de copiar. A faixa que aparece diz `Open copied post?`, `Open copied profile?`, `Open copied hashtag?` ou `Open copied collection?`, com o botão `_Open`.

## 7.4 Abrir posts e links de outros servidores

O Babble se registra como handler de:

| Esquema | O que é |
|---|---|
| `babble://` | O retorno da autorização OAuth, quando o navegador não consegue voltar por `127.0.0.1`. |
| `web+ap://` | Links de objeto federado (ActivityPub). |
| `web+mastodon://` | Links no formato de URL do Mastodon. |

Um link desses, vindo de outro aplicativo, abre no Babble em vez do navegador.

**Uma ressalva sobre o `babble://`:** para que o sistema entregue esse esquema ao Babble, é preciso que ele esteja registrado como o manipulador. Se você usa o Flatpak, o comando abaixo precisa ser executado **fora** do sandbox, uma vez por máquina:

```sh
xdg-mime default io.github.mauriciobc.Babble.desktop x-scheme-handler/babble
```

Sem esse registro, o navegador diz "Unsupported location specified" e o Babble nunca recebe a resposta. **Isso não afeta o login normal**, que usa o ouvinte em `127.0.0.1` e não depende de registro nenhum. E, em qualquer caso, o botão `Enter Authorization Code…` resolve o login sem nenhum dos dois caminhos.

## 7.5 Captura de tela

O botão `Take Screenshot`, no compositor, usa o portal do XDG de captura de tela. A imagem é copiada para o cache e anexada.

O botão **só aparece** quando o portal existe. Num sistema sem o portal, a ausência do botão é a informação — nada quebra.

## 7.6 Verificação ortográfica

O compositor corrige a ortografia com o `libspelling`, sobre um buffer do `GtkSourceView`. As palavras erradas ficam sublinhadas, e o botão direito mostra as sugestões.

O corretor segue o idioma escolhido em `Post Language`. Se não houver dicionário instalado para aquele idioma, a verificação **desliga sozinha** — e nenhum aviso aparece, porque simplesmente não há o que sugerir.

## 7.7 O estado do sistema

O Babble presta atenção ao que o GNOME informa e muda de comportamento em conformidade:

| Estado do sistema | O que muda |
|---|---|
| **Sem rede** | Uma faixa `You’re offline` aparece no topo. O aplicativo continua tentando reconectar e a faixa some sozinha quando a rede volta. |
| **Economia de energia** | O polling fica mais espaçado e os GIFs param de tocar sozinhos. |
| **Conexão limitada (dados móveis)** | O visualizador de mídia espera você pedir para carregar vídeo e áudio. |
| **Movimento reduzido** | A reprodução automática de GIFs para. |
| **Animações desligadas** | A reprodução automática de GIFs para. |

O `Catch Up` também leva isso em conta: com economia de energia, ele muda a periodicidade da varredura.

## 7.8 O formato da hora

Os horários seguem a sua preferência do GNOME de 12 ou 24 horas, lida pelo portal de Configurações. Não há ajuste dentro do Babble.

## 7.9 Notificações do sistema

As notificações do desktop trazem botões de ação: `Favorite`, `Boost` e `Follow Back`. Você age sobre a publicação sem abrir a janela.

As notificações aparecem quando a janela do Babble não está em foco. Quando ela está, elas ficam dentro do aplicativo.

Quando o `Background Mode` está ligado, o painel rápido do GNOME mostra quantas menções chegaram enquanto você estava away.

## 7.10 Ações do lançador

Clique com o botão direito no ícone do Babble no painel do GNOME e aparecem `New Post` e `Notifications`, que abrem o aplicativo já na ação pedida.

## 7.11 Busca no GNOME

O Babble é um **provedor de busca**: ele aparece na busca do panorama de Atividades, e você pode procurar contas, hashtags e publicações sem abrir a janela.

Para ativá-lo, ligue o Babble em **Configurações do sistema → Busca → Provedores de pesquisa**.

## 7.12 Executar em segundo plano

**Objetivo:** continuar recebendo notificações com a janela fechada.

**Pré-condições:** a preferência `Background Mode`, em Preferências → Notifications, está ligada; o portal de segundo plano do XDG existe.

1. Feche a janela do Babble. O aplicativo continua rodando.
2. O ícone do Babble no painel continua lá, e o painel rápido mostra as menções que chegaram.
3. Clique no ícone para trazer a janela de volta.

**Resultado:** o aplicativo continua rodando sem janela, o ícone do Babble permanece no painel e as notificações continuam chegando. O painel rápido do GNOME mostra quantas menções chegaram enquanto você estava away.

**Se der errado:** se o processo do Babble terminar junto com a janela, o portal de segundo plano não está disponível — a preferência fica sem efeito e o aplicativo sai com a janela. Veja [11.12](11-solucao-de-problemas.md#1112-um-recurso-não-aparece).

**O que precisa:** o portal de segundo plano. Sem ele, a preferência não tem efeito e o aplicativo sai com a janela.

## 7.13 Atalho global de publicação rápida

A preferência `Quick Post Shortcut`, em Preferências → Notifications, registra um atalho **do sistema** que abre o compositor de qualquer aplicativo. O padrão é **Super+Shift+M**, e o atalho pode ser trocado nas configurações do GNOME.

O botão da preferência só aparece quando o portal de atalhos globais do XDG existe.

## 7.14 Acessibilidade

- Leitores de tela anunciam os lotes novos de publicações, e o Babble usa descrições acessíveis em todos os botões de mídia, nas notificações e no visualizador.
- Enquanto uma publicação está sendo enviada, o sistema não entra em suspensão nem bloqueia a sessão.
- A coluna web é `WebKitWebView` com scripts desligados por padrão, o que reduz a superfície de ataque de páginas de terceiros.

## 7.15 O que precisa de uma instalação

Três recursos só funcionam a partir de uma **build instalada** (`meson install` ou Flatpak), porque o GNOME os encontra pelos arquivos instalados — o `.desktop`, o serviço D-Bus e o provedor de busca:

- a ativação por D-Bus (o aplicativo sobe sem janela e abre quando o sistema pede);
- o provedor de busca do panorama de Atividades;
- as ações do lançador.

Os recursos apoiados em portal — captura de tela, segundo plano, atalho global — só precisam do portal correspondente existir. **Um portal ausente esconde o recurso, sem quebrar nada.**

O restante funciona em uma execução direto do código-fonte.
