# 4 — Escrita: o compositor, enquetes, citações e rascunhos

> **Documento:** `BABBLE-MAN-001` — capítulo 4 — v1.0 — rascunho para revisão
> **Software descrito:** Babble 0.3.0 · **Data:** 2026-11-11 · **Pai:** [Manual do Usuário do Babble](README.md)

O compositor do Babble é uma caixa de diálogo sobre a janela principal. Ela guarda o que você escreveu, verifica a ortografia, completa menções e hashtags e sabe o que o seu servidor aceita.

## 4.1 Abrir o compositor

Há quatro caminhos:

| Caminho | Observação |
|---|---|
| O botão `New Post`, no cabeçalho | Aparece na barra lateral e no cabeçalho das páginas em janelas estreitas. |
| **Ctrl+N** | De qualquer lugar. |
| `Mention {conta}`, no menu de uma publicação | Abre o compositor com `@conta` já escrito. |
| `Direct Message`, no menu de um perfil | Abre o compositor com `@conta` e a visibilidade `Direct`. |

O **Quick Post Shortcut** (Preferências → Notifications) registra um atalho global do sistema — por padrão **Super+Shift+M** — que abre o compositor de qualquer aplicativo.

## 4.2 A anatomia do compositor

```
┌──────────────────────────────────────────────────────────────┐
│ [lixeira]            Título do diálogo        [agenda] [Post] │
├──────────────────────────────────────────────────────────────┤
│ (faixa "Replying to a post")                                 │
│ (faixa "Scheduled for …")                                    │
│                                                              │
│  Área de texto: "What's on your mind?"                       │
│                                                              │
├──────────────────────────────────────────────────────────────┤
│ [aviso de conteúdo]  (campo "Content Warning")               │
│ (grupo da enquete)                                           │
├──────────────────────────────────────────────────────────────┤
│ [anexar][captura][aviso][enquete][menção][emoji][fio]        │
│ [visibilidade] [idioma]                          [caracteres]│
└──────────────────────────────────────────────────────────────┘
```

O **título** do diálogo diz o que você está fazendo: `New Post`, `Reply`, `Quote Post`, `Edit Post`, `Edit Post · 1/3` (quando a publicação tem mais de uma parte editável), `Edit Scheduled Post` ou `Thread · Post 2` (quando o compositor fica aberto para um fio).

O botão de lixeira, no canto superior esquerdo, é `Discard Draft`: ele descarta o rascunho, depois de uma confirmação — *"The text, attachments and poll of this post will be lost"*.

## 4.3 Publicar

**Objetivo:** enviar a sua publicação.

**Pré-condições:** o texto, a mídia ou a enquete estão prontos, e nada está pendente na validação.

1. Escreva no campo de texto.
2. Revise o que o Babble assinalou. As palavras com erro de ortografia ficam sublinhadas; clique com o botão direito para ver as sugestões. O corretor segue o idioma escolhido no seletor `Post Language`.
3. Clique em `_Post` (o botão sugerido, no canto superior direito) ou pressione **Ctrl+Enter**.

**Resultado:** um toast `Post sent` confirma. O compositor fecha e a publicação aparece na sua `Home`.

**Se der errado:** o Babble recusa a publicação e explica por quê, com um toast:

| Mensagem | Causa |
|---|---|
| `Post is too long` | O texto passou do limite do servidor. |
| `Add content warning text` | O aviso de conteúdo está ligado, mas o texto do aviso está vazio. |
| `Add at least two poll choices` | A enquete está com menos de duas opções. |
| `Poll choices must be unique` | Duas opções da enquete têm o mesmo texto. |
| `A poll cannot be combined with media` | Enquete e anexo na mesma publicação. |

Para fechar o compositor sem publicar, use o botão `Discard Draft` (que apaga) ou pressione **Escape** (que fecha e guarda o rascunho — veja [4.13](#413-rascunhos)).

## 4.4 O contador de caracteres

O canto inferior direito mostra `Characters Left`. A contagem é a do servidor, não uma estimativa:

- O limite vem do servidor (`max_characters`), 500 por padrão.
- Cada URL conta como um bloco fixo de caracteres (23, ou o valor que o servidor declarar), e não pelo seu comprimento real.
- Cada menção a alguém de outro servidor conta como `@user`.
- O texto do aviso de conteúdo também conta.

O contador fica âmbar quando restam 20 % ou menos, e vermelho quando passa do limite.

## 4.5 Menções

**Objetivo:** escrever o `@` de alguém sem errar o identificador.

**Pré-condições:** o compositor está aberto.

1. Clique no botão `Mention` (ícone de pessoa) na barra inferior, ou digite `@` seguido do início do nome.
2. A lista `Suggested Accounts` aparece. Diga um nome para restringir: *"Type a name to search"*.
3. Clique na conta desejada.

**Resultado:** o `@conta@servidor` é inserido no texto, com um espaço depois.

**Se der errado:** se a conta não aparecer na lista, digite mais letras: a busca só traz contas que o seu servidor conhece. Para uma conta de outro servidor que o seu nunca viu, digite o identificador completo (`fulano@exemplo.social`) e o Babble resolve. Se a menção ficar sem negrito nem cor, o servidor não a reconheceu como menção — mas ela ainda funciona como texto.

Se você digitar o identificador completo (`fulano@exemplo.social`), o Babble resolve a conta mesmo que o seu servidor nunca a tenha visto.

Para citar alguém sem notificar, escreva o `@` dentro de um bloco de código, ou use a forma `(@fulano)`. O servidor entende as duas.

## 4.6 Hashtags

1. Digite `#` no começo de uma linha ou depois de um espaço.
2. A lista `Suggested Hashtags` aparece, ordenada por utilidade: primeiro a correspondência exata do que você digitou; depois as hashtags que você usou recentemente, as que estão no seu perfil e as que você segue; por fim, as do servidor — as seguidas primeiro, e depois por número de pessoas.
3. Escolha uma da lista.

**Resultado:** a hashtag é inserida. A capitalização que você digitou é mantida: digitar `GamingD` e escolher `gamingdeals` produz `GamingDeals`.

As legendas das sugestões dizem de onde elas vieram: `Recently used`, `On your profile`, `Followed`, e `{n} people, last 2 days`.

## 4.7 Emoji

1. Clique em `Insert Emoji` (ícone de sorriso).
2. O painel tem duas abas: `Emoji`, com os emojis do sistema, e `Custom`, com os emojis do seu servidor. A aba `Custom` some quando o servidor não tem emojis customizados.
3. Pesquise em `Search Emoji` ou `Search Custom Emoji`, ou navegue pelas categorias. A categoria `Recently Used` guarda os seus últimos emojis.

**Resultado:** um emoji padrão é inserido como caractere; um emoji customizado entra como `:nome_curto:`, com espaços ao redor.

## 4.8 Aviso de conteúdo

1. Clique no botão `Content Warning` (ícone de triângulo) na barra inferior.
2. Escreva o aviso no campo que aparece.

**Resultado:** o texto fica escondido dos seus seguidores até eles clicarem em `Show Content`. O aviso também marca a publicação como sensível, automaticamente.

Responda a uma publicação que tem aviso de conteúdo e o compositor abre com o mesmo aviso, preenchido — você não começa do zero.

## 4.9 Enquetes

**Objetivo:** publicar uma enquete.

**Pré-condições:** o servidor permite enquetes.

1. Clique no botão `Poll` (ícone de barras) na barra inferior.
2. O grupo da enquete aparece com duas opções, chamadas `Choice 1` e `Choice 2`.
3. Escreva o texto de cada opção.
4. Clique em `_Add Choice` para mais opções — até o limite do servidor (4 por padrão).
5. Em `Duration`, escolha `30 minutes`, `1 hour`, `1 day` ou `7 days`. O servidor pode restringir esse conjunto.

**Resultado:** a enquete aparece na publicação, com as opções como botões de rádio (ou de seleção, se admitir mais de uma resposta).

**Restrições:** uma enquete não pode conviver com mídia, e as opções precisam ser diferentes entre si. O servidor recusa com `A poll cannot be combined with media` e `Poll choices must be unique`.

**Se der errado:** se o botão `Poll` não existir ou não tiver efeito, o seu servidor pode não permitir enquetes. O servidor também recusa a publicação com `Add at least two poll choices` e `Poll choices must be unique`. Veja [10.6](10-referencia.md#106-mensagens-e-avisos).

### Como votar

Na publicação, uma enquete em aberto é um formulário: escolha as opções e clique em `_Vote`. O botão `Show Results` mostra o resultado sem votar.

Depois de votar, ou quando a enquete fecha, aparecem barras com as porcentagens, a sua escolha marcada como `Your vote` e a vencedora em negrito. O rodapé mostra `{n} votes`, `Closed` ou `{tempo} left`, e um botão de recarregar (`Refresh Results`) para atualizar o resultado. O botão `_Back to Voting` volta ao formulário.

Se a conexão falhar ao votar, o aviso é `Vote failed`.

## 4.10 Citar uma publicação

**Objetivo:** comentar uma publicação mantendo-a à vista dos seus seguidores.

**Pré-condições:** a publicação pode ser citada — o servidor precisa suportar citações (Mastodon 4.5 ou posterior) e o autor precisa permitir.

1. Clique em `Quote` na barra de ações da publicação, ou em `Quote` no menu `Boost With Visibility`.
2. Escreva o seu comentário. A publicação citada vai junto, com o seu texto na frente.
3. Publique.

**Resultado:** o título do compositor passa a ser `Quote Post`, e a publicação sai como uma citação.

**Se der errado:** se a citação não aparecer na publicação depois de enviada, o servidor é anterior ao Mastodon 4.5 e não suporta citações. Se o endereço colado não virou cartão, ou porque a publicação não pode ser citada, ou porque você digitou o endereço em vez de colá-lo — digitar não converte. Veja [10.7](10-referencia.md#107-limites).

### Citar colando o endereço

Se você **colar** o endereço de uma publicação — e apenas colar, sem digitar — o Babble troca o texto pelo cartão de citação e avisa: `Post attached as a quote`, com um botão `Undo` que desfaz a troca e devolve o endereço como texto.

Isso não acontece se você digitou o endereço, se desfez, se arrastou, ou se o rascunho foi restaurado. E não acontece se a publicação não puder ser citada: nesse caso o endereço fica como texto.

Citar exige Mastodon 4.5 ou posterior. Em servidores mais antigos, os campos de citação são omitidos e a publicação sai sem citação.

## 4.11 Visibilidade

O seletor `Post Visibility`, na barra inferior, tem quatro opções:

| Opção | Quem vê |
|---|---|
| `Public` | Qualquer pessoa, e aparece nas timelines públicas do servidor. |
| `Unlisted` | Qualquer pessoa com o endereço, mas não aparece nas timelines públicas. |
| `Followers only` | Só os seus seguidores. |
| `Direct` | Só as pessoas mencionadas. |

O valor inicial vem de Preferências → Posting → `Default Visibility`. Esse valor é **gravado no seu servidor**, de modo que vale para qualquer outro cliente Mastodon.

**Atenção ao citar:** a citação usa a visibilidade que estiver selecionada no compositor, que começa no valor de `Default Visibility`. A publicação citada não impõe a sua — uma citação pública continua pública mesmo que a original seja `Followers only`.

## 4.12 Idioma

O seletor `Post Language` define o idioma da publicação. Ele tem duas funções: o servidor usa para filtrar e para traduzir; e o Babble usa para **escolher o dicionário do corretor ortográfico**.

O valor inicial vem de Preferências → Posting → `Default Language`, que oferece `System language` e cerca de sessenta idiomas.

## 4.13 Rascunhos

**Objetivo:** não perder o que você começou a escrever.

**Pré-condições:** o compositor tem texto, mídia ou enquete.

1. Feche o compositor — pelo botão de fechar ou por **Escape**. *Não* use o botão de lixeira, que descarta de propósito.

**Resultado:** o Babble guarda o rascunho localmente e mostra uma faixa no topo da janela principal: `Draft from {tempo}` — por exemplo, `Draft from 5m` — ou `You have an unsent draft`. Clicar em `Restore` traz o rascunho de volta para o compositor.

O Babble guarda até **5 rascunhos**, e cada conta tem os seus: um rascunho de uma conta nunca é restaurado em outra.

**Se der errado:** o botão de lixeira (`Discard Draft`) apaga o rascunho guardado, depois da confirmação.

## 4.14 Agendar uma publicação

**Objetivo:** publicar mais tarde, sem precisar estar com o aplicativo aberto na hora.

**Pré-condições:** o servidor permite agendamento.

1. Escreva a publicação como de costume.
2. Clique no botão de alarme, no canto superior direito do compositor.
3. Escolha a data, a hora e o fuso horário. A dica é: *"Choose a time at least 5 minutes in the future."*
4. Clique em `_Schedule`.

**Resultado:** o compositor mostra a faixa `Scheduled for {data}` com um botão `_Clear Schedule`. O botão `_Post` passa a ser `_Schedule`, e o tooltip para `Schedule (Ctrl+Enter)`. A publicação fica agendada no servidor.

**Se der errado:** se o seletor recusar a data, o horário precisa ser pelo menos 5 minutos no futuro — e o fuso horário tem de estar certo. Se a publicação agendada não aparecer na página `Scheduled Posts`, o servidor pode ter recusado o agendamento; tente de novo. Veja [11.12](11-solucao-de-problemas.md#1112-um-recurso-não-aparece).

Para agendar sem abrir o seletor de data, o botão de alarme também funciona depois que a data está marcada: o tooltip vira `Change Time`.

### A página `Scheduled Posts`

O menu principal tem `Scheduled Posts`. Cada linha mostra `Scheduled for {data}`, mais fichas com a visibilidade, o número de anexos, `Poll · {n} options` e `Reply`, quando aplicável. As ações são `Edit`, `Reschedule`, `Convert into a Draft` e `Delete`.

Editar uma publicação agendada **reagenda**: a nova entrada substitui a antiga. Se o servidor não conseguir remover a original, o Babble avisa com a faixa *"The replacement was saved, but the original scheduled post could not be removed. Both may be published."*, com um botão `Retry Removal`. Ao fechar o compositor nesse estado, o aplicativo pergunta `Leave Duplicate Scheduled Posts?` e oferece `Keep Open` ou `Close Anyway`.

Apagar uma publicação agendada pede confirmação: `Delete Scheduled Post?` — *"The post will not be published."*

## 4.15 Editar e apagar uma publicação

### Editar

1. Abra o menu `More Options` da sua publicação e escolha `Edit`.
2. O compositor abre com o texto e a mídia da publicação, editáveis. O título é `Edit Post` ou `Edit Post · {n}/{total}`.
3. Clique em `_Save`.

As mídias voltam como linhas editáveis: você pode trocar a descrição e remover anexos. A marcação de sensível é preservada.

Se a publicação não puder ser carregada para edição, o aviso é `Could not load post for editing`.

### Apagar

1. Abra o menu `More Options` e escolha `Delete`.
2. Confirme em `Delete Post?` — *"This post will be removed from your profile and from the timelines of other servers."*

**Resultado:** a publicação some na hora, e um toast `Post deleted` aparece com um botão `Undo`. A remoção só é enviada ao servidor quando o toast expira — o que dá uma janela para você se arrepender.

### Apagar e redigir

`Delete and Redraft` apaga a publicação e copia o texto, os anexos e a enquete para um rascunho novo. A confirmação avisa: *"This post will be deleted and its text, attachments and poll copied into a new draft."* As mídias podem ser reanexadas ao rascunho por cerca de 24 horas.

## 4.16 Publicar um fio

**Objetivo:** escrever várias publicações que se respondem.

**Pré-condições:** o compositor está aberto e a primeira publicação ainda não saiu.

1. Ligue o botão `Keep Open for a Thread`, na barra inferior do compositor.
2. Publique a primeira.
3. O compositor **fica aberto**, com o texto limpo e o título `Thread · Post 2`. A linha `End of the previous post` mostra o fim do que você acabou de publicar, para você saber o que está respondendo.

**Resultado:** cada publicação seguinte sai como resposta da anterior. Ao terminar, feche o compositor.

**Se der errado:** se o título não virar `Thread · Post 2`, o interruptor `Keep Open for a Thread` não ficou ligado. Se o compositor fechar sozinho no meio, o servidor recusou a publicação anterior — veja o toast. Para desfazer o fio já publicado, apague as publicações uma a uma, pelo menu `More Options`.

## 4.17 Fixar e citar nas suas publicações

Duas ações do menu `More Options` controlam quem pode citar o que você publica:

- **`Pin to Profile`** — fixa a publicação no seu perfil. Só vale para publicações suas, públicas ou não listadas.
- **`Who Can Quote`** — escolhe entre `_Anyone`, `My Followers` e `_Nobody`. É o equivalente, para a publicação, da preferência `Default Quote Policy`.

O valor padrão vem de Preferências → Posting → `Default Quote Policy` (`Everyone`, `Followers` ou `Only me`), que é gravado no servidor e vale para todos os seus clientes. A preferência só aparece em servidores Mastodon 4.5 ou posteriores.

Para retirar uma publicação sua de uma citação de outra pessoa, use `Remove My Post From Quote` no menu da citação. A confirmação diz *"The quote will no longer show your post."* e o aviso final é `Your post was removed from the quote`.

## 4.18 Resumo: o que o compositor oferece

| Recurso | Como ativar | Onde fica a opção |
|---|---|---|
| Anexar mídia | `Attach Media`, arrastar ou colar | [Capítulo 5](05-midia.md) |
| Captura de tela | `Take Screenshot` | [Capítulo 5](05-midia.md) |
| Aviso de conteúdo | `Content Warning` | Preferências → Posting |
| Enquete | `Poll` | Preferências → Posting |
| Menção | `Mention` | — |
| Emoji | `Insert Emoji` | — |
| Fio | `Keep Open for a Thread` | — |
| Visibilidade | `Post Visibility` | Preferências → Posting → `Default Visibility` |
| Idioma | `Post Language` | Preferências → Posting → `Default Language` |
| Marcar mídia como sensível | automático, se ligado | Preferências → Posting → `Media Marked Sensitive` |
| Agendar | botão de alarme | Preferências → Posting → `Old Post Reply Warning` e `Private Post Link Warning` (para os avisos) |
