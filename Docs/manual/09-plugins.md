# 9 — Plugins

> **Documento:** `BABBLE-MAN-001` — capítulo 9 — v1.0 — rascunho para revisão
> **Software descrito:** Babble 0.3.0 · **Data:** 2026-11-11 · **Pai:** [Manual do Usuário do Babble](README.md)

Um plugin acrescenta uma função ao Babble: um item no menu de uma publicação, um painel numa página de hashtag, um par de entradas no menu principal, uma página de configurações própria. Ele é código de terceiros rodando dentro do aplicativo, com as mesmas permissões do aplicativo.

## 9.1 A página `Plugins`

**Objetivo:** ver, ligar e desligar os plugins.

**Pré-condições:** o `libpeas 2` e seu carregador de Python estão instalados.

1. Abra o menu principal e escolha `Plugins`. A página se divide em dois grupos:
   - **`Included With Babble`** — os plugins que vêm com o aplicativo, revisados junto com ele.
   - **`Installed by You`** — os plugins que você instalou.
2. Ligue ou desligue cada plugin com o interruptor ao lado do nome.
3. Se o plugin tiver configurações, o botão com uma engrenagem abre a subpágina delas.

**Resultado:** a mudança vale a partir da próxima vez que o plugin for usado; desligar um plugin remove o que ele acrescentava.

**Se der errado:** se o `libpeas` não estiver instalado, a página mostra `Plugins Are Unavailable` — *"Babble needs libpeas 2 and its Python loader to load plugins"*. Nada mais acontece, e o resto do aplicativo funciona normalmente.

## 9.2 Onde colocar um plugin

O botão `Open Plugins Folder` abre a pasta onde o Babble procura plugins:

| Situação | Caminho |
|---|---|
| Instalação comum | `~/.local/share/babble/plugins` |
| Instalação em Flatpak | `~/.var/app/io.github.mauriciobc.Babble/data/babble/plugins` |

O Babble também procura plugins em `BABBLE_PLUGIN_PATH` (a variável de ambiente) e na pasta `plugins/` ao lado do pacote instalado.

Um plugin é uma pasta com um arquivo `.plugin` e um módulo Python dentro. A pasta precisa ter um nome próprio, e o módulo é nomeado `babble_<nome>` para não colidir com nenhum pacote instalado.

## 9.3 Aviso importante

> **Só ligue plugins em que você confie.**

Um plugin roda como parte do Babble e pode fazer tudo o que o Babble faz — inclusive ler e publicar com a sua conta. Os plugins que vêm com o aplicativo são revisados junto com ele; qualquer outro é tão seguro quanto quem o escreveu.

O grupo `Installed by You` traz a descrição: *"Plugins you install can do anything Babble can, including reading and posting with your account. Only turn on plugins you trust."*

## 9.4 Os plugins que vêm com o Babble

### Wallabag

Acrescenta `Save to Wallabag` ao menu de cada publicação, para guardar o endereço do post e lê-lo depois no [Wallabag](https://wallabag.org).

**Configuração, na primeira vez:** o plugin pede o endereço do servidor Wallabag (`app.wallabag.it` para o serviço hospedado, ou o seu), o **ID** e o **segredo** de um cliente de API — que você cria no Wallabag, em `API Clients Management` —, e o seu nome de usuário e a sua senha.

A senha é usada uma vez, para entrar, e **não é guardada**. O chaveiro guarda o segredo do cliente e um token que mantém você assinado; se o token expirar, o plugin volta a pedir a entrada. O item `Sign Out` está nas configurações do plugin.

### Hashpanel

O [Hashpanel](https://github.com/mauriciobc/hashpanel) acompanha as hashtags diárias do fediverse brasileiro — `#segundaficha`, por exemplo, às segundas.

Na página de uma hashtag que ele acompanha, o plugin mostra de quem é o dia, os favoritos, impulsos e respostas do dia, a comparação da semana com a anterior, e o post mais popular do dia.

O plugin **só pergunta ao Hashpanel sobre as hashtags que ele acompanha**: abrir qualquer outra hashtag não envia nada ao Hashpanel. A página `Hashtag Panel` nas configurações permite trocar o servidor do Hashpanel.

### Na Real

O [Na Real](https://codeberg.org/thiagojedi/na-real) é um aplicativo de perguntas e respostas anônimas do fediverse: alguém faz uma pergunta a uma conta, sem se identificar, e a conta responde.

O plugin acrescenta duas entradas ao **menu principal** do Babble:

- `My Questions` — o painel com as perguntas dirigidas a você;
- `Share My Questions Link` — o compositor, com um convite e o seu endereço público.

Ele não acrescenta nada às publicações nem aos perfis. Como o Na Real é um aplicativo da web que precisa de JavaScript, o plugin o mostra na coluna web do Babble e programa um pouco a página: no formulário de entrada, o servidor da sua conta já vem preenchido (o clique em `Log in` continua sendo seu); nas listas de perguntas, o link `Ver postagem` abre o fio dentro do Babble; o botão de compartilhar do Na Real abre o compositor do Babble em vez de copiar o endereço; e a hashtag do próprio Na Real ganha uma linha que a abre.

Cada instalação do Na Real é um servidor próprio, e qual usar é uma configuração do plugin.

## 9.5 Quando um plugin não funciona

| Situação | O que aparece |
|---|---|
| O plugin não pôde ser carregado | Uma mensagem sob o nome dele, com um sinal de aviso. Ele é tentado de novo na próxima vez que o Babble iniciar. |
| O plugin parou de funcionar | O motivo aparece sob o nome, com um sinal de aviso, **e o plugin é desligado**. O Babble avisa que o desligou. |
| O plugin foi feito para outra versão do Babble | Ele não é carregado. |

O detalhe técnico de cada caso fica no log do sistema — veja [11.6](11-solucao-de-problemas.md#116-o-log-do-sistema).

Um detalhe importante: **o Python não consegue descarregar um módulo**. Um plugin que foi carregado e falhou continua registrado até o Babble reiniciar. Por isso a mensagem de falha só some de verdade depois de reiniciar o aplicativo.
