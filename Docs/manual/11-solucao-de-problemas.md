# 11 — Solução de problemas

> **Documento:** `BABBLE-MAN-001` — capítulo 11 — v1.0 — rascunho para revisão
> **Software descrito:** Babble 0.3.0 · **Data:** 2026-11-11 · **Pai:** [Manual do Usuário do Babble](README.md)

Cada item abaixo traz o **sintoma** (o que você vê), a **causa provável** e a **ação** que resolve. Se o sintoma não estiver aqui, o [capítulo 10](10-referencia.md) tem a lista de mensagens por área.

## 11.1 O aplicativo não abre

| Sintoma | Causa provável | Ação |
|---|---|---|
| O aplicativo não inicia e a janela não aparece | Ele já está rodando: o Babble é de instância única | Procure o ícone no painel do GNOME e clique nele. Se ele estiver escondido, use a busca de aplicações. |
| O aplicativo não inicia e o terminal acusa `Settings schema … is not installed` | As chaves do GSettings não estão instaladas | Use uma build instalada (`meson install`) ou aponte `GSETTINGS_SCHEMA_DIR` para a pasta `data` da build. |
| A janela abre e fecha na hora | Falha na inicialização do GTK | Rode o aplicativo no terminal para ver a mensagem de erro. |
| O aplicativo não aparece no panorama de Atividades, no menu de arquivos nem no provedor de busca | O `.desktop`, o serviço D-Bus e o provedor de busca são arquivos **instalados** | Instale a build. Esses três recursos não funcionam a partir do código-fonte. |

## 11.2 O login não avança

| Sintoma | Causa provável | Ação |
|---|---|---|
| O navegador abre a página do servidor, mas não volta ao Babble | O navegador não conseguiu alcançar o ouvinte em `127.0.0.1` | Use o botão `Enter Authorization Code…` na tela de boas-vindas e cole o código que o servidor exibiu. |
| O navegador diz "Unsupported location specified" | O esquema `babble://` não está registrado no sistema | Registre o esquema, uma vez: `xdg-mime default io.github.mauriciobc.Babble.desktop x-scheme-handler/babble`. Em Flatpak, rode fora do sandbox. |
| A janela fica em `Welcome to Babble` depois de autorizar | A autorização foi negada, ou foi autorizada em outra conta | Tente de novo e aprove o pedido na mesma conta e no mesmo servidor já conectados ao Babble. |
| O servidor recusa o pedido de autorização | O registro do aplicativo foi recusado pelo servidor | Tente outro cliente do mesmo servidor. O Babble registra a falha de registro no log do sistema. |
| O login funciona, mas a conta não é salva | O chaveiro não está acessível | Veja [11.3](#113-o-chaveiro-não-guarda-o-login). |

**Dica:** o `Enter Authorization Code…` funciona **sem nenhum** dos dois caminhos de redirecionamento. Se o login está te dando trabalho, use-o.

## 11.3 O chaveiro não guarda o login

| Sintoma | Causa provável | Ação |
|---|---|---|
| O aplicativo avisa que não encontrou um serviço de chaveiro | Nenhum serviço de chaveiro está rodando | Ligue o GNOME Keyring (ou o KeePassXC, ou outro serviço compatível) e **reinicie o Babble**. |
| O login é pedido toda vez que o aplicativo abre | O chaveiro está rodando, mas o aplicativo não consegue falar com ele | Verifique se o serviço está desbloqueado. No Flatpak, o aplicativo precisa da permissão para falar com `org.freedesktop.secrets`. |
| O aviso diz que o serviço existe, mas a conta não volta | A entrada da conta foi apagada do chaveiro por fora | Entre de novo pela tela de boas-vindas. O Babble esquece a conta em vez de tentar para sempre. |

O Babble mantém todas as contas no chaveiro do sistema. O que ele guarda ali é um **token**, não a sua senha: revogar o acesso é questão de revogar o token no servidor.

## 11.4 As timelines não atualizam

| Sintoma | Causa provável | Ação |
|---|---|---|
| As publicações não chegam sozinhas | A atualização ao vivo está desligada | Preferências → Timelines → `Live Timelines`. |
| A `Home` atualiza, mas `Local` e `Federated` não | A atualização ao vivo das timelines públicas está desligada | Preferências → Timelines → `Live Public Timelines`. Ela depende de `Live Timelines` e **usa mais memória** em timelines muito movimentadas. |
| Nada atualiza e o servidor parece lento | O servidor limitou a taxa de requisições | Espere o limite passar. O recarregamento automático pausa enquanto o servidor estiver limitando. |
| As publicações chegam com atraso depois de um tempo offline | O fluxo ao vivo foi reconectado e está recuperando o que perdeu | É o comportamento esperado. Se persistir, recarregue a página. |

## 11.5 A faixa `You’re offline`

| Sintoma | Causa provável | Ação |
|---|---|---|
| A faixa `You’re offline` aparece | O aplicativo perdeu a rede | Espere. A faixa some sozinha quando a rede volta, e o fluxo ao vivo se reconecta. |
| A faixa não some | A rede voltou, mas o fluxo não reconectou | Feche e abra a janela, ou reinicie o aplicativo. |
| A faixa aparece mesmo com rede | Um proxy ou firewall bloqueia a conexão de longa duração | Verifique as permissões de rede do Flatpak, se for o caso. |

## 11.6 O log do sistema

O Babble registra no log do sistema as mensagens que explicam o comportamento do login e das contas. Para lê-las:

```sh
journalctl --user -g 'accounts:' -f
```

As linhas de conta trazem o identificador do processo do Babble (`BABBLE_PID`), o que permite seguir um aplicativo específico. O log do kernel e o do usuário são separados; se o problema é de rede ou de portal, o `journalctl --user` mostra a parte do aplicativo, e o `journalctl -b` mostra a do sistema.

Esse log é a melhor fonte quando um plugin falha: a página `Plugins` mostra o resumo, e o log tem o detalhe.

## 11.7 A mídia não carrega

| Sintoma | Causa provável | Ação |
|---|---|---|
| O visualizador diz `Media Not Stored by Your Server` | O seu servidor não tem cópia, e você recusou carregar do servidor de origem | Use `Open in Browser`, ou mude `Media From Other Servers` para `Always Load` ou `Ask Each Time`. |
| Um toast diz `Loading media from other servers is turned off` | A configuração está em `Never Load` | Mude a configuração, se quiser carregar. |
| O vídeo mostra `Load Video` em vez de tocar | A conexão está marcada como limitada, ou a configuração é `Never` | Clique em `Load Video`, ou mude `Load Media Automatically`. |
| O GIF não toca sozinho | Movimento reduzido, animações desligadas, economia de energia, ou `Autoplay Animated Media` desligada | Verifique as quatro condições em [5.8](05-midia.md#58-reprodução-automática-e-carregamento). |
| O GIF não toca dentro da timeline | `Play Animated GIFs in Timelines` está desligada | Ligue a preferência. Só o GIF mais visível toca, um de cada vez. |
| A imagem está cortada | O ajuste padrão corta a mídia para preencher o espaço | Ligue `Letterboxed Media`. |
| O visualizador diz `Playback is not available on this system` | O sistema não tem os decodificadores de vídeo | Abra o arquivo no navegador. |

## 11.8 Uma publicação sumiu

| Sintoma | Causa provável | Ação |
|---|---|---|
| A publicação não aparece, e no lugar há uma barra `Filtered: {nome}` | Um filtro do seu servidor casou com ela | Clique em `Show Anyway`, ou revise o filtro em Preferências → Reading → `Manage Filters`. |
| A publicação nem aparece, nem há barra nenhuma | O filtro é do tipo `Hide`, e ele remove a publicação sem avisar | O filtro está no servidor; ajuste-o lá, ou peça a quem administra o servidor. |
| A mídia está coberta, mas o texto aparece | Um filtro do tipo `Blur` casou com ela | É o comportamento do `Blur`. |
| As republicações desapareceram da `Home` | `Hidden Boosts on Home` está ligado | Desligue a preferência. |
| As respostas de contas que você não segue sumiram | `Hidden Replies From Non-Follows` está ligado | Desligue a preferência. |

## 11.9 O Catch Up está incompleto

| Sintoma | Causa provável | Ação |
|---|---|---|
| A faixa diz `Covers {intervalo} ({n} posts)` | O servidor entrega no máximo 800 publicações da `Home`, independentemente da janela pedida | Reduza a janela de tempo, ou aceite a cobertura parcial. |
| A faixa diz `Only {n} post in {intervalo}` | Houve pouco movimento no período | Use o botão `Catch up on 24 hours`. |
| O aviso fala em limite de requisições | O servidor limitou a taxa, e a varredura foi interrompida | Espere os minutos indicados e repita. |
| O instantâneo não aparece da próxima vez | Ele não pôde ser gravado | O aviso `This catch-up could not be saved` aparece; refaça o catch-up com uma janela menor. |

## 11.10 Um plugin parou de funcionar

| Sintoma | Causa provável | Ação |
|---|---|---|
| O plugin aparece com um sinal de aviso sob o nome | Ele levantou uma exceção | O Babble **desliga** o plugin e diz que o fez. O motivo está no log do sistema. |
| O plugin nem aparece na lista | Ele não pôde ser carregado | O motivo também está no log. Ele é tentado de novo na próxima vez que o aplicativo iniciar. |
| O plugin sumiu depois de uma atualização do aplicativo | Ele é de outra versão do Babble | Procure a versão correspondente. |
| A página `Plugins` diz `Plugins Are Unavailable` | O `libpeas 2` não está instalado | Instale o `libpeas 2` e o seu carregador de Python. |

Depois de um plugin ter sido carregado, **reiniciar o aplicativo** é necessário: o Python não consegue descarregar um módulo.

## 11.11 A interface está em inglês

| Sintoma | Causa provável | Ação |
|---|---|---|
| A interface não está em português | A tradução não está completa em 0.3.0, ou o catálogo não está instalado | Preferências → Appearance → `Language` → `Português (Brasil)`, e **reinicie o aplicativo** — o idioma só vale na próxima inicialização. |
| Só alguns rótulos estão em português | O catálogo pt-BR está incompleto | Os que faltam aparecem em inglês; é o comportamento esperado nesta versão. A [tabela do capítulo 10](10-referencia.md#105-rótulos-da-interface-em-inglês-e-em-português) mostra o que já tem tradução. |

## 11.12 Um recurso não aparece

| Sintoma | Causa provável | Ação |
|---|---|---|
| `Collections` não está no menu | O servidor é anterior ao Mastodon 4.6 | Nada a fazer: o recurso não existe no servidor. |
| `Admin Dashboard` não está no menu | A sua conta não tem papel de administrador | Peça o papel ao administrador do servidor. |
| Não há abas no perfil de alguém | O servidor ou a conta não expõem aquele dado | Depende do servidor e das escolhas da conta. |
| Não há `Notification Filtering` nas Preferências | O servidor é anterior ao Mastodon 4.3 | Use os filtros do próprio Babble. |
| Não há `Default Quote Policy` nas Preferências | O servidor é anterior ao Mastodon 4.5 | As citações também não funcionam nesse servidor. |
| O botão `Take Screenshot` sumiu | O portal de captura de tela do XDG não existe no sistema | Instale o portal correspondente. |
| Não há coluna web ao lado da timeline | O WebKitGTK não está instalado, ou a preferência está desligada | Verifique `Web Links Beside the Timeline`. |

O Babble esconde o que o servidor não suporta, em vez de mostrar um botão que falha. A tabela de versões está em [10.7](10-referencia.md#107-limites).

## 11.13 O aplicativo usa muita memória

| Sintoma | Causa provável | Ação |
|---|---|---|
| A memória cresce ao rolar uma timeline longa | É o comportamento de uma lista longa com imagens e pré-visualizações | Feche a janela e reabra; as páginas são recriadas. |
| A memória sobe muito em `Local` e `Federated` | A atualização ao vivo dessas timelines mantém muita coisa viva | Desligue `Live Public Timelines`. |
| A memória sobe depois de abrir muitos vídeos | Os motores de mídia só liberam depois de parar | Feche o visualizador de mídia; o motor é liberado ao fechar. |
| O GIF animado na timeline trava o primeiro uso | O primeiro uso carrega o decodificador | É o motivo de `Play Animated GIFs in Timelines` vir desligada. |

## 11.14 Não consigo mais usar uma conta

| Sintoma | Causa provável | Ação |
|---|---|---|
| A conta sumiu da lista | Ela foi removida do aparelho | Entre de novo pela tela de boas-vindas. |
| O servidor ainda reconhece a conta, mas o Babble não | A revogação do token falhou no servidor | O aviso fica no log do sistema. Entre de novo para pegar um token novo. |
| A conta pede login a cada abertura | A entrada do chaveiro foi apagada | Veja [11.3](#113-o-chaveiro-não-guarda-o-login). |

## 11.15 Quando nada resolve

Reunidos, estes são os três passos que resolvem a maioria dos casos:

1. **Reinicie o aplicativo.** Vários problemas — plugin que parou, fluxo ao vivo travado, composers com estado antigo — só se resolvem assim, porque o Python não descarrega módulos e o fluxo de rede não se reconecta sozinho.
2. **Reinicie o chaveiro.** Se o login não se mantém, o serviço de chaveiro precisa ser reiniciado e desbloqueado.
3. **Leia o log do sistema.** `journalctl --user -g 'accounts:' -f` mostra as decisões do aplicativo sobre contas e sessões, e é onde o motivo de um plugin ter falhado aparece.
