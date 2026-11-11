# 1 — Primeiros passos

> **Documento:** `BABBLE-MAN-001` — capítulo 1 — v1.0 — rascunho para revisão
> **Software descrito:** Babble 0.3.0 · **Data:** 2026-11-11 · **Pai:** [Manual do Usuário do Babble](README.md)

Este capítulo traz o caminho entre abrir o aplicativo e conseguir ler o seu feed: entrar na conta, entender o que o aplicativo pede ao servidor, gerir várias contas e sair.

## 1.1 Como funciona a entrada

O Babble não pede a sua senha. Ele usa o fluxo de autorização do OAuth, o mesmo que usa o navegador ao pedir acesso a um site.

Acontece nesta ordem:

1. Você escreve o endereço do seu servidor e clica em `Continue`.
2. O navegador abre a **página de autorização do seu próprio servidor** — não a do Babble. Ali você digita sua senha, no servidor, como se estivesse entrando nele.
3. O servidor mostra o que está sendo pedido. No login normal, são duas permissões: `read` (ler) e `write` (publicar).
4. O servidor devolve um **token** ao aplicativo. É esse token, e não a sua senha, que o Babble guarda.
5. O token vai para o **chaveiro do sistema** (GNOME Keyring, KeePassXC ou equivalente).

O token funciona como uma chave: se alguémiver obtê-lo, age como você. Por isso ele fica no chaveiro, que pede a sua senha do sistema para abrir.

**Por que `read write` e nada mais.** Toda permissão pedida aparece na tela de consentimento do servidor, uma por uma. Se o Babble pedisse permissões de moderação junto, elas apareceriam ali e seriam aceitas junto do resto. Por isso as permissões de administração (`admin:read admin:write`) são pedidas **separadamente**, apenas quando você abre o `Admin Dashboard` e apenas se o papel da sua conta no servidor permitir.

**O que o token dá acesso.** `read` cobre listar e ver publicações, perfis, notificações, conversas, listas, filtros e histórico de edições. `write` cobre publicar, editar, apagar, responder, impulsar, favoritar, salvar, seguir, silenciar e bloquear. Não existe uma permissão `follow` separada desde o Mastodon 3.5: seguir está dentro de `read`/`write`.

### O retorno do navegador

Quando o servidor devolve o token, ele redireciona o navegador para um endereço local do seu próprio computador (`http://127.0.0.1:<porta>/callback`). O Babble abre uma escuta nessa porta antes de começar, então o navegador entrega o token direto para o aplicativo, sem passar por nenhum servidor intermediário. A aba do navegador mostra que é seguro fechá-la.

O fluxo usa **PKCE** (RFC 7636) e um `state` novo a cada tentativa, de modo que uma resposta antiga ou adulterada não seja aceita.

Se, por alguma razão, o navegador não conseguir voltar — o caso mais comum é o computador não conseguir abrir a porta — o Babble recorre a um redirecionamento `babble://`, que depende de o sistema estar configurado para abrir esse esquema com o Babble (ver [7.4](07-integracao-com-o-desktop.md#74-abrir-posts-e-links-de-outros-servidores)). Como terceiro recurso, e o mais seguro quando há firewall, o botão `Enter Authorization Code…` deixa você colar à mão o código que o servidor exibiu.

## 1.2 Entrar pela primeira vez

**Objetivo:** entrar na sua conta e ver a timeline `Home`.

**Pré-condições:** você tem uma conta em um servidor Mastodon; o chaveiro do sistema está em execução; há rede.

1. Abra o Babble. A janela de boas-vindas aparece, com o título `Welcome to Babble` e o texto `Log in with your Mastodon account`.
2. Clique no campo `Server Address` e escreva o endereço do seu servidor, sem `https://` e sem barra final — por exemplo, `mastodon.social`. Se preferir, clique em um dos chips de servidores populares (`Other Popular Servers`) para preencher o campo.
3. Clique em `Continue`.
4. O navegador abre a página de autorização do seu servidor. Entre com sua conta e aprove o pedido.
5. O navegador volta sozinho para o Babble e a aba mostra que o login terminou. Feche a aba.
6. A janela passa a mostrar a timeline `Home`.

**Resultado:** o botão da conta, no alto da barra lateral, mostra seu avatar, seu nome e seu `@conta`. A timeline `Home` carrega.

**Se der errado:**

- *O navegador não volta.* Use `Enter Authorization Code…` na janela de boas-vindas e cole o código que o servidor exibiu. Veja [11.2](11-solucao-de-problemas.md#112-o-login-não-avança).
- *O servidor recusa o pedido.* O Babble registra a falha de registro do aplicativo no servidor e informa. Servidores muito restritivos podem recusar; nesse caso, outro cliente do mesmo servidor resolve.
- *O aplicativo avisa que não achou o chaveiro.* Veja [11.3](11-solucao-de-problemas.md#113-o-chaveiro-não-guarda-o-login).
- *A janela fica em `Welcome to Babble` mesmo com a aba do navegador fechada.* Você ainda não autorizou: volte ao navegador e aprove o pedido, ou use o código.

## 1.3 Adicionar, trocar e remover contas

O Babble guarda quantas contas você quiser, de servidores diferentes. Todas ficam no mesmo chaveiro, e cada uma mantém seus rascunhos, seus instantâneos do Catch Up e suas preferências separadas.

### Como adicionar outra conta

**Objetivo:** ter duas contas do Babble abertas ao mesmo tempo.

**Pré-condições:** você já entrou em pelo menos uma conta.

1. Clique no botão da conta, no alto da barra lateral (o que mostra seu avatar).
2. Clique em `Add Account…`.
3. Repita os passos de [1.2](#12-entrar-pela-primeira-vez).

**Resultado:** a nova conta entra na lista. O Babble continua na conta anterior até você trocar.

**Se der errado:** se a nova conta não aparecer na lista depois da autorização, o chaveiro pode não estar gravando — veja [11.3](11-solucao-de-problemas.md#113-o-chaveiro-não-guarda-o-login). Se o servidor recusar o pedido, veja [11.2](11-solucao-de-problemas.md#112-o-login-não-avança).

### Como trocar de conta

**Objetivo:** passar a ler e publicar como outra conta.

**Pré-condições:** há mais de uma conta guardada.

1. Clique no botão da conta, no alto da barra lateral.
2. Clique na conta desejada na lista.

**Resultado:** a interface recarrega para a conta escolhida, e a marca de verificação ao lado dela passa a ser a atual. A troca troca também o servidor: o que você vê em `Local`, por exemplo, passa a ser a timeline pública do outro servidor.

**Se der errado:** se a troca não acontecer, o servidor pode estar limitando requisições — tente de novo em instantes. Se a conta sumiu da lista, ela foi removida do aparelho: entre de novo pela tela de boas-vindas. Veja [11.14](11-solucao-de-problemas.md#1114-não-consigo-mais-usar-uma-conta).

### Como sair de uma conta

**Objetivo:** encerrar a sessão de uma conta e remover suas credenciais deste computador.

**Pré-condições:** há pelo menos uma conta guardada.

1. Clique no botão da conta, no alto da barra lateral.
2. Ao lado da conta que você quer remover, clique no botão de lixeira. A dica dele é `Remove Account`.
3. Confirme em `Remove Account?`. O aviso diz: *"The login for @conta will be removed from this device."*

**Resultado:** o Babble lê o token da conta, apaga a entrada do chaveiro e remove a conta da lista — tudo localmente, sem esperar a rede. Em seguida ele avisa o servidor para revogar o token e descarta as credenciais do aplicativo no servidor. As demais contas guardadas não são afetadas.

**Se der errado:** se a revogação no servidor falhar, o aplicativo registra o aviso no log do sistema e segue com a remoção local. A conta sai do Babble de qualquer maneira; o servidor pode, nesse caso, continuar considerando o token válido até a revogação chegar. Para conferir o que o aplicativo registrou, veja [11.6](11-solucao-de-problemas.md#116-o-log-do-sistema).

## 1.4 Abrir a ajuda

O Babble traz uma ajuda própria, instalada junto, que abre com `F1` ou pelo item `Help` do menu principal. Ela cobre o essencial de forma resumida e está disponível em inglês.

A ajuda está disponível em **inglês** e em **português (pt-BR)**: o leitor de ajuda escolhe a pasta de idioma pelo idioma do sistema, de modo que a página aparece na língua do seu ambiente. As páginas em português ficam em `data/help/pt_BR/` e são instaladas ao lado das de inglês, em `data/help/C/`.

Este manual é a referência completa: ele explica o *porquê* de cada comportamento, lista limites e mensagens de erro e registra as decisões que o aplicativo toma em situations ambíguas.

## 1.5 Referência rápida do primeiro uso

| Ação | Como |
|---|---|
| Abrir o compositor | `New Post`, ou **Ctrl+N** |
| Recarregar a página atual | **Ctrl+R** ou **F5** |
| Trocar o modo da barra lateral | Botão `Sidebar`, no rodapé da barra lateral |
| Abrir Preferências | **Ctrl+**, |
| Abrir a lista de atalhos | **Ctrl+?** |
| Ver os atalhos | `Keyboard Shortcuts`, no menu principal |
| Ir para `Home` | **Ctrl+1** |
| Ir para `Notifications` | **Ctrl+2** ou **Ctrl+Shift+N** |
| Ir para `Search` | **Ctrl+F** ou **Ctrl+K** |
| Fechar a janela | **Ctrl+W** |
| Sair do aplicativo | **Ctrl+Q** |

A lista completa está no [capítulo 10](10-referencia.md).
