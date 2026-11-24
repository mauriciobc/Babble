# 13 — Feed Chef: timelines de receita

> **Documento:** `BABBLE-MAN-001` — capítulo 13 — v1.1 — aprovado
> **Software descrito:** Babble 0.4.0 · **Data:** 2026-11-24 · **Pai:** [Manual do Usuário do Babble](README.md)

O `Feed Chef` é uma timeline que você mesmo desenha. Onde o `Catch Up` olha para trás no que você já segue, o Feed Chef continua preparando um feed ao vivo a partir de uma *receita* sob o seu controle.

## 13.1 O que é

Uma **receita** tem três partes:

1. **Pantry** (despensa) — de onde vêm as publicações: `Home`, `Local`, `Federated`, ou uma das suas listas, com hashtags para incluir ou excluir.
2. **Diet** (dieta) — o que uma publicação precisa passar: palavras bloqueadas, regras de mídia, texto alternativo, sensibilidade, tom, um tamanho mínimo, ou só quem você segue.
3. **Kitchen** (cozinha) — como as publicações que passam são ordenadas e equilibradas: engajamento, atualidade, assuntos em alta, um limite por pessoa ou servidor, e agrupamento de publicações semelhantes.

O resultado é uma timeline contínua, não um instantâneo congelado. Você pode fixar várias receitas na barra lateral, editá-las, pré-visualizá-las e compartilhá-las.

O Feed Chef é **opcional**. Preferências → Timelines → Sidebar → `Feed Chef` liga ou desliga. Quando está desligado, o Babble não o carrega de jeito nenhum: sem entradas na barra lateral, sem trabalho em segundo plano e sem requisições de rede por causa dele. Desligar fecha as páginas abertas na hora; a memória do código é liberada depois de reiniciar.

## 13.2 Criar uma receita

**Objetivo:** montar uma timeline do Feed Chef só sua.

**Pré-condições:** você está com a conta conectada, e o `Feed Chef` está ligado nas Preferências.

1. Abra `Feed Chef` na barra lateral (ou a lista de receitas que ele mostra quando você ainda não tem nenhuma).
2. Escolha `New Recipe`, depois um modelo ou `Blank Recipe`.
3. No construtor de receitas, ajuste a despensa, a dieta e a cozinha. O construtor conta as publicações correspondentes conforme você muda.
4. Opcionalmente clique em `_Preview` / `_Preview Recipe` para ver uma amostra do resultado sem salvar.
5. Salve a receita.

**Resultado:** a receita aparece na lista do Feed Chef e pode ser aberta como timeline.

**Se der errado:** uma receita que não puder ser salva mostra `Could not save the recipe`. A pré-visualização nunca grava a receita; fechar o construtor sem salvar descarta as mudanças (`Your changes to this recipe will be lost.`).

## 13.3 Ler a timeline de uma receita

Abra uma receita na lista do Feed Chef, ou no lugar fixado na barra lateral.

- Publicações entre as mais engajadas podem mostrar o selo `Popular`.
- Um **nível de calor** (`Heat Level`: Cool, Warm, Hot, Fire) esconde uma parte das publicações mais quietas para o feed ficar mais afiado.
- Publicações novas que pontuam bem podem esperar atrás de uma pílula em vez de pular a página.
- `_Why This Post?` em uma publicação explica por que ela foi mantida ou removida naquele preparo.
- `Kitchen Report` resume os preparos recentes daquela receita.
- `More Like This` em uma publicação foca a timeline nas hashtags e palavras dela (`Showing more like %s`).

Tudo o que o Feed Chef registra para essas explicações fica **no seu computador**. Você pode limpar esse histórico de auditoria no Feed Chef sem apagar as receitas.

## 13.4 Fixar, compartilhar e importar

**Fixar.** Em uma timeline de receita, use `Pin to Sidebar` / `Unpin from Sidebar` como em qualquer outro lugar.

**Compartilhar.** `Share Recipe` (ou `S_hare Recipe`) oferece um link de compartilhamento e, quando a receita é pequena o bastante, um QR code. `_Copy Share Link` copia o link; uma receita grande demais para QR code avisa e ainda deixa copiar o link.

**Importar.** Abrir um link do Feed Chef pergunta antes de gravar qualquer coisa: `Import Recipe?` / `Import “%s” into this account?`, com `_Import`. Se já existir uma receita da mesma identidade, o Babble pergunta `Replace Recipe?` primeiro. Nada é importado até você confirmar.

## 13.5 Desligar o Feed Chef

**Objetivo:** impedir que o Feed Chef use memória ou a rede.

1. Abra Preferências → Timelines → Sidebar.
2. Desligue `Feed Chef` (subtítulo: *"Recipe timelines; uses more memory and network. Turning it off closes its pages now and frees the rest after a restart"*).

**Resultado:** o Feed Chef some da barra lateral, as páginas abertas fecham, e ele não faz mais requisições. Depois que você reinicia o Babble, o código dele também deixa de ficar na memória.

## 13.6 Resumo

| Pergunta | Resposta |
|---|---|
| Em que isso difere do Catch Up? | O Catch Up é um olhar ranqueado para trás no Início, congelado como instantâneo. O Feed Chef é uma receita ao vivo que você desenha. |
| Onde eu desligo? | Preferências → Timelines → Sidebar → `Feed Chef`. |
| Compartilhar envia a receita para servidores do Babble? | Não. O link codifica a receita; a importação pergunta antes de gravá-la em uma conta. |
| Onde ficam os logs de “por que esta publicação”? | Só no seu computador. |
