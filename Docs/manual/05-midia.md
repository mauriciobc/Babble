# 5 — Mídia: anexar, visualizar, salvar

> **Documento:** `BABBLE-MAN-001` — capítulo 5 — v1.0 — rascunho para revisão
> **Software descrito:** Babble 0.3.0 · **Data:** 2026-11-11 · **Pai:** [Manual do Usuário do Babble](README.md)

Mídia é tudo o que não é texto: imagens, vídeos, GIFs animados e áudio. Este capítulo vai do "como anexo isso" ao "como salvo aquilo", e passa pela decisão que o Babble toma por você quando a mídia não está no seu servidor.

## 5.1 Quatro maneiras de anexar

**Objetivo:** colocar uma imagem, um vídeo ou um áudio na publicação.

**Pré-condições:** o compositor está aberto.

1. Escolha uma das quatro vias:
   - **`Attach Media`** (ícone de clipe, na barra inferior) abre o seletor de arquivos. O filtro oferecido depende do servidor: `Media accepted by this server` quando o servidor declara o que aceita, `Images, Videos and Audio` caso contrário.
   - **Arrastar e soltar**: arraste os arquivos de um gerenciador de arquivos para dentro do compositor.
   - **Colar**: com o cursor no campo de texto, **Ctrl+V** cola arquivos copiados. Uma imagem isolada na área de transferência vira um PNG no cache, chamado `Pasted image`.
   - **`Take Screenshot`** (ícone de câmera, na barra inferior) tira uma captura de tela pelo portal do XDG e a anexa. O botão só aparece quando esse portal existe no sistema.

**Resultado:** cada anexo vira uma linha no compositor, com miniatura, nome do arquivo, botão de remoção (`Remove Attachment`) e um campo `Description`.

**Se der errado:**

| Mensagem | Causa |
|---|---|
| `Only images, videos and audio can be attached` | O arquivo não é uma imagem, um vídeo nem um áudio. |
| `A post can have at most {n} attachments` | O servidor limita a quantidade (4 por padrão). |
| `A video or audio file must be the only attachment` | Vídeo ou áudio precisa ser o único anexo. |
| `Wait for the post to be sent to attach files` | Já há uma publicação em andamento. |
| `Could not open the file picker` | O seletor de arquivos não abriu. |
| `Could not paste the image` / `Could not paste the files` | A área de transferência não tinha nada utilizável. |
| `Could not take a screenshot` | O portal recusou a captura. |

## 5.2 A descrição da mídia

Cada anexo tem um campo `Description`. É o texto alternativo: o que o Babble anuncia para um leitor de tela, e o que serve a quem não enxerga a imagem. Ele é enviado ao servidor como a descrição da mídia, e é o que o servidor mostra para os seus seguidores.

O limite vem do servidor (`description_limit`). O Babble não exige que você preencha — mas **sem descrição, quem usa leitor de tela não sabe o que há na publicação**, e é por isso que o Babble pergunta antes de deixar você impulsionar uma publicação com mídia sem descrição (veja [2.4](02-leitura.md#antes-de-impulsionar)).

## 5.3 O envio

Ao publicar, cada anexo é enviado ao servidor. Antes disso, o Babble confere o tamanho do arquivo contra o limite do servidor; se passar, a recusa é explícita:

> `“{nome}” is too large for this server ({limite} is the limit)`

Depois do envio, o servidor pode precisar de tempo para processar o arquivo (é o que acontece com vídeo). O Babble espera esse processamento e diz:

| Mensagem | Significado |
|---|---|
| `The server is still processing the media` | O servidor ainda está trabalhando; o Babble espera até dois minutos. |
| `The server did not accept the media` | O servidor recusou ou demorou demais. |
| `Could not upload media` | Falha no envio. |

GIFs são enviados como `image/gif`; é o servidor que os transcodifica para MP4. O Babble nunca envia o formato `gifv`.

Remover um anexo mostra um toast `Attachment removed` com `Undo` — dá para trazê-lo de volta.

## 5.4 O visualizador de mídia

O visualizador **não é uma janela separada**: ele cobre a janela principal, e é por isso que o botão `Fullscreen` realmente preenche a tela.

### O cabeçalho

O título diz o tipo do anexo: `Image`, `Video`, `Animated GIF`, `Audio` ou `Attachment`. Com mais de um anexo, aparece o subtítulo `{atual} de {total}`.

Os botões são `Close`, `Fullscreen` (que vira `Exit Fullscreen`), `Save As…` e um menu `More Actions`:

| Item | O que faz |
|---|---|
| `Copy URL` | Copia o endereço do arquivo. |
| `Copy Image` | Copia a imagem para a área de transferência. Só para imagens. |
| `Open in Browser` | Abre o arquivo no navegador. |
| `Open With…` | Baixa o arquivo e o entrega aos aplicativos instalados. |

### A cena

- `Previous Attachment` e `Next Attachment` trocam de anexo. Uma fileira de pontos indica a posição: `Attachment {n} of {total}`.
- O zoom, para imagens: `Zoom Out`, `Zoom In`, `Fit to Window` e um botão com a porcentagem atual, cuja dica é `Toggle Fit and Actual Size`.

### O teclado

| Tecla | O que faz |
|---|---|
| **←** / **→** | Vai para o anexo anterior ou o seguinte. |
| **F** | Alterna a tela cheia. |
| **Esc** | Fecha o visualizador. |
| **+** / **-** | Aproxima e afasta. |
| **0** | Ajusta à janela. |

### A descrição

Sob a mídia há um botão `ALT`, cuja dica é `Show or Hide Description`. O texto é selecionável, o que permite copiá-lo.

### Quando a mídia não abre

| Mensagem | Causa |
|---|---|
| `No Media` | A publicação não tem mídia. |
| `Media Still Processing` | O servidor ainda está processando o arquivo: *"Open the post again in a moment."* |
| `Preview Not Available` | Esse tipo de anexo não pode ser mostrado aqui. |
| `Could Not Load Image` | A imagem não carregou. |
| `Could Not Load Media` | *"Playback is not available on this system."* |

Em caso de falha há os botões `_Try Again` e `_Open in Browser`.

## 5.5 Zoom e arrasto

O zoom começa em "ajustar à janela". Cada passo multiplica por 1,25, e o limite é 8× (ou mais, para imagens pequenas).

- **Ctrl + roda do mouse** amplia; a roda sem Ctrl desloca a imagem.
- **Pinça** amplia em telas de toque.
- **Duplo clique** alterna entre "ajustar à janela" e o tamanho real (200 % para imagens pequenas).
- **Arrastar** uma imagem que cabe na janela tira a imagem da janela — ela vai para outro aplicativo como arquivo, acompanhada pela URL. Já com o zoom aplicado, arrastar desloca a imagem em vez de extraí-la.

Para extrair vídeo ou áudio, o que vai para fora é o arquivo baixado ou o endereço dele.

## 5.6 Salvar uma mídia

**Objetivo:** guardar o arquivo no seu computador.

**Pré-condições:** a mídia está aberta no visualizador, e você tem permissão de escrita na pasta de destino.

1. Abra a mídia no visualizador.
2. Clique em `Save As…`.
3. Escolha o destino. O diálogo se chama `Save Media` e o botão é `_Save`. O nome sugerido vem do endereço do arquivo; sem ele, é `media` com a extensão correspondente ao tipo.

**Resultado:** um toast `Saved “{nome}”` aparece, com um botão `Show in Folder` que abre o gerenciador de arquivos no lugar certo. Durante o download, uma barra de progresso fica no visualizador.

**Se der errado:** se o aviso for `Could not save media`, o destino não é gravável ou a conexão caiu no meio. Para mídia que o seu servidor não tem, veja [5.7](#57-mídia-que-está-em-outro-servidor).

**Se der errado:** `Could not save media`.

## 5.7 Mídia que está em outro servidor

Este é o ponto em que a federação cobra o seu preço, e vale entender antes de precisar.

Quando o seu servidor mostra uma publicação com mídia, ele guarda uma **cópia** da mídia. Essa cópia é o que o Babble exibe, e não há nenhum custo para você nem para o servidor de origem.

Mas há casos em que o seu servidor **não** tem cópia — em geral, quando o servidor de origem está fora do alcance do seu, ou a mídia foi removida de lá. Nesses casos, o arquivo só existe no servidor de origem. Carregá-lo diretamente dali faz com que **o servidor de origem veja o seu endereço IP e saiba que você abriu aquela publicação**.

Por isso o Babble pergunta. O diálogo `Load Media From Another Server?` diz:

> *"Your server has no copy of this media. Loading it from {host} lets that server see your IP address and that you viewed it."*

Os botões são `_Cancel` e `_Load`, e há a caixa `_Remember This Choice` para não perguntar de novo.

### A configuração

Preferências → Reading → grupo `Media` → **`Media From Other Servers`**:

| Valor | O que acontece |
|---|---|
| `Ask Each Time` | Pergunta a cada mídia. É o padrão. |
| `Always Load` | Carrega sem perguntar. |
| `Never Load` | Não carrega; o visualizador oferece abrir no navegador. |

Mídia que **o seu servidor tem** nunca gera essa pergunta.

Se você recusar, o visualizador mostra `Media Not Stored by Your Server` — *"Your server has no copy of this media. Open it in the browser to view it on the original server."* — e, quando a configuração é `Never Load`, um toast avisa `Loading media from other servers is turned off`.

**O que nunca é bloqueado:** abrir o original no navegador, copiar o endereço e arrastar a mídia para fora não fazem nenhuma requisição ao servidor de origem. O consentimento cobre apenas o *carregamento* dentro do aplicativo.

## 5.8 Reprodução automática e carregamento

Três preferências do grupo `Media` em Preferências → Reading decidem quando a mídia pesada começa a baixar e a tocar.

### `Load Media Automatically`

Cobre vídeo, áudio e GIF animado — que são transmitidos, não apenas exibidos. Imagens e miniaturas sempre carregam.

| Valor | O que acontece |
|---|---|
| `Always` | Carrega tudo de imediato. |
| `Not on Metered Connections` | O padrão. Carrega, a menos que o sistema marque a conexão como limitada. |
| `Never` | Espera você pedir. |

Quando a mídia não carrega sozinha, aparece um botão `Load Video` ou `Load Audio`, com a dica `Media is not downloaded automatically on a metered connection` (ou `You chose not to load media automatically`).

### `Autoplay Animated Media`

Vem ligada. Com ela, um GIF animado toca em ciclo, sem som. Desligada, o GIF fica parado até um clique.

Três coisas do sistema **desligam a reprodução automática**, mesmo com a preferência ligada:

- movimento reduzido pedido pelo sistema;
- animações desligadas no GNOME;
- modo de economia de energia.

Essas três condições são reavaliadas **enquanto o GIF toca** — se o sistema entra em economia de energia no meio, a reprodução para.

A preferência não afeta vídeo com som.

### `Play Animated GIFs in Timelines`

Vem **desligada**, e é uma escolha deliberada: o efeito custa mais memória que uma imagem parada, e a janela pode pausar por um instante na primeira reprodução.

Quando está ligada, o GIF **mais visível** da página é o único que toca, e a reprodução acontece no lugar, dentro da timeline. Um botão sobre o GIF alterna: `Pause Animation` enquanto toca, `Play Animation` quando está pausado. Um GIF que você pausou continua pausado na rolagem seguinte.

As três restrições acima continuam valendo, assim como `Load Media Automatically` e `Media From Other Servers`.

O volume do vídeo e do áudio é lembrado entre sessões.

## 5.9 Mídia sensível

Uma publicação pode marcar a mídia como sensível. Aí a mídia aparece coberta, e o botão `Show Sensitive Content` a revela; depois de revelada, o botão vira `Hide Sensitive Content`.

A preferência **`Sensitive Media`** (`show-sensitive-media`) revela a mídia automaticamente, sem botão. Quem prefere decidir publicação por publicação desliga.

## 5.10 Cartões de pré-visualização

Quando uma publicação tem um link, o servidor pode devolver uma **pré-visualização** — imagem, título e descrição de uma página. O Babble mostra esse cartão abaixo da publicação. A preferência **`Link Preview Cards`** desliga a exibição.

O cartão de pré-visualização também aparece nas notícias em alta do `Explore`, e a aba `News` mostra quem está falando do link: `By {fonte}`, `{n} person talking` / `{n} people talking`, ou `Shared by {conta}`.
