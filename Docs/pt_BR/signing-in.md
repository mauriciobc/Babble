# Entrar

Digite o endereço do seu servidor, aprove o Babble no navegador e pronto.
O Babble só pede `read write`, e o seu token vai para o chaveiro do sistema,
não para um arquivo.

## Como o login funciona

Durante o login, o Babble escuta em uma porta de `127.0.0.1` e registra
`http://127.0.0.1:<port>/callback` como redirecionamento OAuth, de modo que o
navegador devolve a autorização diretamente. A aba então avisa que é seguro
fechá-la. O fluxo usa PKCE (RFC 7636) e um `state` novo a cada tentativa.

Uma autorização negada encerra a tentativa sem salvar credenciais; um state
ou redirecionamento inválido deixa a tentativa legítima disponível. JSON
inválido, tokens ausentes e identidades de conta inválidas são relatados como
falhas de login, e não adotados como contas.

## Quando nenhuma porta pode ser aberta

O Babble recorre a um redirecionamento `babble://`. Esse precisa de um único
comando, executado uma vez por máquina no host:

```sh
xdg-mime default io.github.mauriciobc.Babble.desktop x-scheme-handler/babble
```

A entrada de área de trabalho já anuncia `x-scheme-handler/babble`, mas o GLib
só resolve um esquema de URI que também esteja listado em
`~/.config/mimeapps.list`, que é o que o `xdg-mime default` escreve. Sem isso,
a devolução do navegador falha com "Unsupported location specified" e o Babble
nunca recebe a resposta.

O Flatpak não consegue escrever o `mimeapps.list` do host de dentro do
sandbox, então isso precisa ser executado fora dele. Isso vale também para o
`meson install`.

**"Inserir código de autorização…" na página de login funciona sem nenhum dos
dois redirecionamentos** — cole o código que o navegador mostra.

## Escopos

O login comum pede `read write` e nada mais. Todo escopo solicitado aparece na
tela de consentimento da instância, então nada extra entra lá por acidente.

Os escopos de administração (`admin:read admin:write`) são pedidos apenas pelo
painel de administração, e apenas uma vez que o papel da sua conta indica que
ele poderia usá-los — essa autorização roda separadamente, por cima da sessão
em que você já está conectado. O navegador precisa autorizar a mesma conta e o
mesmo servidor em que o Babble já está conectado. Trocar de conta durante a
autorização impede que a resposta antiga substitua as credenciais da nova
sessão.

## Removendo uma conta

A saída local não espera pela revogação no servidor nem pela limpeza do
chaveiro, que são apenas tentativas. O Babble tenta excluir tanto o token da
conta quanto as credenciais da aplicação OAuth dela, restrito a essa conta e a
esse servidor. Uma falha do chaveiro gera um único aviso depois que as duas
exclusões respondem; ela não restaura a conta removida nem desloga outra
sessão.
