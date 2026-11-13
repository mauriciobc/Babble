# Política de documentação

| | |
|---|---|
| **Título** | Política de documentação do Babble — o que é documentado, quem escreve, quem aprova e quando muda |
| **Identificador / versão** | `BABBLE-POL-001` — v1.3 — em revisão |
| **Data de emissão** | 2026-11-11 |
| **Autoria** | Os mantenedores do Babble |
| **Aprovado por** | Pendente: a v1.3 é aprovada pelo mantenedor na fusão do pull request que a traz, e esse pull request é citado aqui na fusão. A v1.2 foi aprovada na fusão do [PR #109](https://github.com/mauriciobc/Babble/pull/109) (2026-11-11). O pull request é o registro: contém o diff, a revisão, a execução da CI e a fusão. |
| **Âmbito** | Todos os documentos que o projeto distribui ou mantém: o manual do usuário, as páginas de ajuda no aplicativo, os documentos de referência e de plano sob `Docs/`, as notas de trabalho na raiz do repositório e os catálogos de tradução — cada um em inglês e, onde o conjunto assim o diz, em português do Brasil. Cobre **como** esses documentos são planejados, escritos, traduzidos, revisados, aprovados, versionados, distribuídos e aposentados. Não cobre o software em si, o processo de versão estável, nem o que os documentos dizem. |
| **Propósito** | Manter a documentação fiel ao software que descreve, sem pagar esse preço com um processo que o projeto não consegue sustentar. Uma política que ninguém segue é pior do que nenhuma, porque passa a ser citada como se fosse. |
| **Referências** | ISO/IEC/IEEE 12207 (governança do processo de documentação); ISO/IEC/IEEE 15289 (conteúdo e identificação dos itens de informação); ISO/IEC 26514 (documentação do usuário); ISO/IEC/IEEE 29148 (requisitos). Documentos do projeto: [o manual do usuário](manual/README.md), [Docs/README.md](README.md) (o índice), [Docs/flathub.md](../flathub.md) (uma política do mesmo tipo, para outro assunto). A edição brasileira desta política é [Docs/pt_BR/documentation-policy.md](../pt_BR/documentation-policy.md). |
| **Rastreabilidade** | A seção 3 nomeia o conjunto de documentação; a seção 12 mapeia os documentos ao código que descrevem. |
| **Revisão** | A cada versão estável, e sempre que o próprio processo mudar. |

## 1. Por que este documento existe

O projeto escreve muita documentação: um manual do usuário, duas línguas de ajuda no aplicativo, vinte e nove arquivos sob `Docs/`, três documentos de referência na raiz do repositório e quatro catálogos de tradução. Esse volume tem um modo de falha, e não é "pouca documentação" — é **documentação que divergiu do software**. Uma funcionalidade é lançada, a página de ajuda não a menciona, um rótulo muda e o manual continua a nomear o antigo, um documento de plano descreve uma arquitetura que foi descartada há dois versões estáveis. Nada quebra; a documentação apenas deixa de ser verdadeira.

Esta política existe para tornar essa falha visível e barata de prevenir. Segue as seis atividades da ISO/IEC/IEEE 12207 — planejar, produzir, revisar e aprovar, gerir a configuração, distribuir, manter — mapeadas para um projeto pequeno em que uma pessoa mantém o código e assistentes de IA escrevem grande parte do texto.

## 2. Proporcionalidade

A 12207 pede governança proporcional ao risco e ao tamanho do projeto. Este projeto é pequeno: um mantenedor, uma única série de versões estáveis, nenhuma equipe externa de documentação. Portanto:

- **Não há conselho de documentação, conselho editorial nem cadeia de aprovação.** Os papéis da seção 8 são papéis que uma mesma pessoa pode exercer simultaneamente.
- **Não há conselho de revisão separado para alterações pequenas.** Uma alteração que toca uma única página de ajuda é revisada pela mesma pessoa que a escreveu, contra a lista de verificação da seção 13.
- O que **não** é reduzido em escala é a parte mecânica. Onde uma regra pode ser um teste, ela é um teste, e roda na CI. Uma regra que existe apenas em prosa é a regra que acaba por ser quebrada.
- O que **não** é reduzido em escala é o registro. Uma aprovação que não possa ser apontada mais tarde não aconteceu.

A política diz o que é proporcional; não diz o que é conveniente.

## 3. O conjunto de documentação

O que o projeto documenta, quem o lê e quando ele é produzido. «Norma» designa a norma de conteúdo contra a qual cada item é escrito.

### 3.1 Para usuários

| Item | Público | Formato | Norma | Produzido quando | Critério de completude | Estado |
|---|---|---|---|---|---|---|
| `README.md` | Pessoas que decidem se instalam | Markdown | 26514 (orientação) | A cada versão estável | Diz o que o aplicativo faz, o que exige e como executá-lo | Atual |
| `Docs/features.md`, `Docs/pt_BR/features.md` | Utilizadores e contribuidores | Markdown, inglês e pt-BR | 26514 | A cada funcionalidade | Todas as funcionalidades listadas, com a versão do servidor de que precisam | Atual |
| `Docs/manual/`, `Docs/pt_BR/manual/` | Utilizadores | Markdown, inglês e pt-BR | 26514 + 15289 | A cada alteração visível ao usuário | Cada procedimento traz o modelo completo; nenhuma referência cruzada quebrada; a tabela de rótulos está atualizada; ambas as edições têm as mesmas seções | Atual |
| `data/help/` | Utilizadores, dentro do aplicativo | Mallard | Convenções de ajuda do GNOME | A cada alteração visível ao usuário | Cada conjunto de línguas traz as mesmas páginas e os mesmos identificadores; toda referência cruzada resolve | Atual |
| `Docs/building.md`, `Docs/pt_BR/building.md` | Utilizadores que compilam a partir do código-fonte | Markdown, inglês e pt-BR | 26514 | Quando a compilação muda | Todas as dependências, o manifesto e a suíte de testes | Atual |
| `Docs/signing-in.md`, `Docs/pt_BR/signing-in.md` | Utilizadores | Markdown, inglês e pt-BR | 26514 | Quando o fluxo de entrada muda | Os escopos, os dois caminhos de redirecionamento e o que é guardado | Atual |
| `Docs/desktop-integration.md`, `Docs/pt_BR/desktop-integration.md` | Utilizadores e contribuidores | Markdown, inglês e pt-BR | 26514 | Quando uma integração muda | Todas as funcionalidades do GNOME, e o que cada uma exige | Atual |
| `Docs/ROADMAP.MD` | Utilizadores e contribuidores | Markdown | — | A cada versão estável | Em que o projeto está a apostar, e o que abandonou | Atual |

### 3.2 Para contribuidores e mantenedores

| Item | Público | Formato | Norma | Produzido quando | Critério de completude | Estado |
|---|---|---|---|---|---|---|
| `CLAUDE.md` | Agentes de IA que editam o código | Markdown | 15289 | A cada alteração que quebra uma convenção | Arquitetura, comandos e as regras fáceis de quebrar | Atual |
| `AGENTS.md` | Agentes de IA e contribuidores | Markdown | 15289 | A cada alteração no arnês de testes | O arnês, exatamente como é executado | Atual |
| `PERFORMANCE.md` | Contribuidores | Markdown | 15289 (relatório) | A cada alteração de desempenho | A linha de base, o número atual e por que cada alteração foi feita | Atual |
| `MEMORY-LEAKS.md` | Contribuidores | Markdown | 15289 (relatório) | A cada vazamento encontrado | O padrão, como verificá-lo e se já foi corrigido | Atual |
| `CATCH-UP.md` | Contribuidores | Markdown | 15289 (especificação) | A cada alteração no Catch Up | O comportamento da funcionalidade, especificado | Atual |
| `Docs/mastodon-compat.md` | Contribuidores | Markdown | 15289 | A cada alteração de capacidade | Cada barreira, e a versão do servidor por trás dela | Atual |
| `Docs/localization.md` | Contribuidores e tradutores | Markdown | 26514 | A cada alteração na configuração de i18n | As línguas, o estado e como adicionar uma | Atual |
| `Docs/IMPROVEMENTS.md` | Contribuidores | Markdown | 15289 (registro) | De forma contínua | Cada achado, sua decisão e o que foi adiado | Atual |
| `Docs/glossary.md` | Contribuidores | Markdown | — | A cada novo conceito em que o código se apoia | Cada termo de domínio que o código e os testes nomeiam, definido em palavras simples, em acordo com o glossário do manual onde os dois nomeiam a mesma coisa | Atual |
| `Docs/*-plan.md`, `Docs/*-audit.md`, `Docs/*-study.md`, `Docs/*-review*.md` | Contribuidores | Markdown | 15289 (plano / relatório) | Com o trabalho que descrevem | O plano ou os achados, com critérios de aceitação | Alguns são históricos; ver a seção 11 |

### 3.3 Para tradutores

| Item | Público | Formato | Norma | Produzido quando | Critério de completude | Estado |
|---|---|---|---|---|---|---|
| `po/README.md` | Tradutores | Markdown | 26514 | Com uma alteração no fluxo de tradução | Como traduzir, e quais são as ferramentas | Atual |
| `po/*.po` | Tradutores | Catálogo gettext | — | A cada alteração de string | `msgfmt --check-format` sem erros; o teste `po check <lang>` passa | Atual |
| `data/help/<lang>/` | Tradutores | Mallard | Convenções de ajuda do GNOME | A cada alteração em página de ajuda | O conjunto de páginas corresponde ao inglês arquivo por arquivo | Atual |

### 3.4 Itens formais e notas de trabalho

Dois tipos de documento, com dois níveis diferentes de rigor:

- Um **item formal** é aquele que chega ao leitor como uma declaração do que o software faz. Traz o bloco de identificação da seção 7, tem identificador e versão, e é aprovado nos termos da seção 8. Hoje, são o manual do usuário e esta política. Acrescentar um terceiro é uma decisão, não um acidente.
- Uma **nota de trabalho** é o registro de processo do projeto: planos, auditorias, benchmarks, registros, as notas dos agentes na raiz do repositório. Não precisa de bloco de identificação, porque o seu público são as pessoas que fazem o trabalho e o seu histórico é o do repositório. O Git é o seu histórico de registro.

A linha não é entre "importante" e "não importante". É entre "isto faz uma promessa ao leitor" e "isto registra o que estamos a fazer". Um documento de plano lido apenas pelo seu autor é uma nota de trabalho, mesmo que longo.

### 3.5 As duas línguas

O conjunto formal é escrito duas vezes: uma em inglês, outra em português do Brasil. A regra é uma frase.

> **A edição em inglês fica onde o arquivo já está. Uma edição em
> português do Brasil fica ao lado dele, sob `Docs/pt_BR/`, espelhando o
> caminho exatamente.**

Assim, `Docs/features.md` tem a sua tradução em
`Docs/pt_BR/features.md`, e os treze capítulos do manual são
`Docs/manual/NN-<english-slug>.md` e
`Docs/pt_BR/manual/NN-<portuguese-slug>.md`. O prefixo numérico mantém
os dois na mesma ordem; os nomes de arquivo são traduzidos, porque um
leitor os abre.

O que decorre da regra:

- **A árvore em inglês é a fonte.** Uma alteração é feita em inglês e
  traduzida, nunca o contrário. A edição em português não se adianta:
  quando as duas divergem, o inglês está certo e a tradução está atrasada.
- **As duas carregam o mesmo conteúdo.** As mesmas seções, na mesma
  ordem, dizendo as mesmas coisas. Uma tradução é uma tradução, não uma
  reescrita: não acrescenta, não omite nem reorganiza.
- **As âncoras seguem a língua.** Um link dentro da árvore em inglês
  aponta para dentro da árvore em inglês, e um dentro da árvore em
  português aponta para dentro da árvore em português. As referências
  cruzadas são resolvidas no momento da tradução, não são deixadas para o
  leitor descobrir.
- **As notas de trabalho permanecem em inglês.** A seção 3.4 as
  classifica: são o registro do próprio projeto, lido por quem faz o
  trabalho. Traduzir doze mil linhas de registros de auditoria e listas
  de tarefas dobraria o custo de manutenção de cada revisão e não
  chegaria a ninguém que já não leia o código.
- **Os blocos gerados permanecem gerados.** A tabela de endpoints em
  `mastodon-compat.md` é escrita por `src/test-api-registry.py` e nunca
  é editada à mão; na edição em português ela é mantida byte a byte, com
  a nota a dizer isso. A tabela de rótulos do capítulo 10 mapeia os
  rótulos da interface em inglês para os seus equivalentes em português,
  por isso pertence à edição em português; a edição em inglês diz onde
  está a tabela em vez de a repetir em inglês.

## 4. Nomenclatura e versionamento

### 4.1 Nomes

- Itens formais: `BABBLE-<KIND>-<NNN>`, onde kind é `MAN` (manual), `POL` (política), `REF` (referência), `RPT` (relatório), `PLN` (plano).
- Arquivos: minúsculas, separados por hífen (`mastodon-compat.md`, `documentation-policy.md`). A exceção são os três documentos de referência na raiz do repositório, cujos nomes são consagrados (`PERFORMANCE.md`, `MEMORY-LEAKS.md`, `CATCH-UP.md`) e são mantidos.
- Os capítulos do manual: `NN-<slug>.md`, numerados pela ordem de leitura, com `README.md` como elemento inicial. O slug é traduzido junto com o capítulo.
- Uma edição em português do Brasil: `Docs/pt_BR/<path>`, espelhando o caminho em inglês. O diretório tem o nome da localidade, como já acontece em `data/help/<lang>/`.
- Nunca `final`, `final_v2`, `new`, nem uma data no lugar de uma versão. O histórico vive na versão e no git, não no nome do arquivo.

### 4.2 Versões

Cada item formal traz uma versão da forma `vMAJOR.MINOR` e um estado.

| Alteração | Incremento | Exemplo |
|---|---|---|
| O conteúdo é corrigido, sem mudar o que diz sobre o software | MINOR | Um procedimento ganha um passo que faltava, um erro de digitação num rótulo |
| O que diz sobre o software muda | MAJOR | Um capítulo novo, uma preferência removida, um atalho alterado |
| O item é retirado | O estado passa a `obsolete` | — |

A versão no bloco de identificação indica a versão do item, não a versão do software que ele descreve. A versão do software descrita é um campo à parte, e é ela que tem de estar certa.

### 4.3 Estado

`draft` → `in review` → `approved` → `obsolete`.

Apenas itens `approved` são citados como normativos a partir de outro documento. Um rascunho pode ser citado como rascunho, com o seu estado indicado.

## 5. Produzir um item formal

1. **Classificá-lo.** Voltado ao usuário (26514), uma declaração do que o sistema deve fazer (29148), ou um item de informação do projeto (15289). Um item formal neste projeto é voltado ao usuário ou normativo; não é uma especificação de requisitos, porque o projeto não escreve especificações de requisitos.
2. **Reunir as entradas antes de escrever.** Público, âmbito, ambiente, restrições e a origem de cada afirmação sobre o software. Uma lacuna é marcada com `[A DEFINIR: …]` exatamente no lugar a que pertence — nunca adivinhada, nunca deixada para o leitor descobrir.
3. **Seguir a estrutura da norma aplicável.** Para um manual, é a estrutura da ISO/IEC 26514: identificação, sumário, introdução, conceitos, procedimentos, referência, solução de problemas, glossário, índice, suporte. Para cada procedimento, o modelo: objetivo, pré-condições, passos numerados, resultado e uma indicação para o capítulo de solução de problemas.
4. **Verificar cada afirmação sobre o software contra o código.** Um rótulo, um atalho, um limite e uma mensagem são todos verificáveis, e todos mudam. A seção 12 diz onde é verificado cada tipo de afirmação.
5. **Aplicar o bloco de identificação** e o histórico de alterações.

São os dois últimos que concentram a maior parte do valor. Um manual que descreve o software de memória é precisamente o que esta política existe para evitar.

## 6. As normas que este projeto usa

| Norma | O que ela rege aqui | Onde é aplicada |
|---|---|---|
| ISO/IEC/IEEE 12207 | O processo de documentação | Este documento |
| ISO/IEC/IEEE 15289 | O conteúdo e a identificação de um item de informação | Todos os itens formais |
| ISO/IEC 26514 | Documentação do usuário | O manual, as páginas de ajuda, `README.md`, os documentos voltados ao usuário sob `Docs/` |
| ISO/IEC/IEEE 29148 | Requisitos | Não usada: o projeto não escreve especificações de requisitos, e esta política não cria uma |

Usar uma norma não significa reproduzi-la. São destilações da intenção e da estrutura de cada norma, não substituições do texto publicado. Onde o projeto detém uma edição específica de uma norma, a numeração é adaptada a ela, e qualquer ponto que mereça verificação contra o texto publicado é assinalado no item.

## 7. O bloco de identificação

Todos os itens formais começam com um. O modelo:

```
Título:           <nome do item>
Identificador:    <BABBLE-KIND-NNN> — v<major.minor> — <status>
Data de emissão:  <YYYY-MM-DD>          Autoria: <…>
Aprovado por:     <…>                    Referências: <…>
Âmbito:           <o que abrange e o que não abrange>
Propósito:        <por que existe e para quem>
```

As regras que o tornam útil:

- **O aprovador é nomeado e datado.** "O mantenedor" é um papel, não um nome. Uma aprovação que não possa ser apontada mais tarde não aconteceu.
- **A evidência é o pull request.** O ato de aprovação é a fusão em `main`; o número e o URL do pull request são o registro. É isso que o campo `Approved by` cita.
- **O âmbito declara o que o item não cobre.** Um documento sem essa fronteira é lido como cobrindo tudo o que não menciona.
- **A versão do software que o item descreve é nomeada à parte.** A versão do item e a versão do software são coisas diferentes, e só a segunda diz a um leitor se o item ainda se aplica à sua compilação.

## 8. Revisão e aprovação

### 8.1 Os papéis

| Papel | Quem | O que faz |
|---|---|---|
| **Autor** | Quem escreve o item — um mantenedor, um contribuidor ou um assistente de IA sob a direção do mantenedor | Escreve-o, verifica as afirmações e entrega-o com a lista de verificação marcada |
| **Revisor técnico** | O mantenedor, ou um segundo leitor | Verifica a exatidão técnica contra o código, e a lista de verificação da seção 13 |
| **Aprovador** | O mantenedor | Autoriza a linha de base; a fusão é o ato |
| **Gestor de configuração** | O mantenedor | Corta linhas de base, mantém o histórico e aposenta o que fica obsoleto |

Um assistente de IA nunca é o revisor nem o aprovador do que escreveu. A sua saída é revisada por uma pessoa contra a lista de verificação, exatamente como a de qualquer outro contribuidor.

Quando o autor é o aprovador — o caso normal num projeto de uma pessoa só — a revisão é uma passagem separada sobre o item concluído, contra a lista de verificação, feita depois da escrita, não durante ela. A regra da proporcionalidade é que a revisão acontece; quem a faz é o que varia.

### 8.2 O fluxo

```
autor escreve ─► revisão técnica ─► aprovador ─► fusão em main ─► linha de base
      │                │                  │
      │                └── a lista falha ─► de volta ao autor
      └── a lista falha ─► de volta ao autor
```

### 8.3 Critérios de entrada — antes da revisão

- O bloco de identificação está completo, com um aprovador nomeado.
- Toda afirmação sobre o software foi verificada contra o código, e as verificações estão registradas no pull request.
- As barreiras mecânicas estão verdes: a suíte, o linter e o teste de documentação da seção 9.
- As referências cruzadas resolvem: cada link, cada âncora, cada tabela.
- Nenhum `[A DEFINIR: …]` fica numa seção que o revisor é chamado a aprovar. Uma lacuna marcada é aceitável num rascunho; não é aceitável num item que está a ser aprovado.
- O histórico de alterações registra o que mudou e porquê.

### 8.4 Critérios de saída — após a fusão

- O item faz parte da linha de base.
- O índice que o lista é atualizado (`Docs/README.md`, ou `README.md` para os itens de primeiro nível).
- A rastreabilidade da seção 12 continua válida, e qualquer módulo que a alteração tenha tocado é nomeado.
- As notas do próximo versão estável dizem o que mudou na documentação, quando há algo a dizer.

### 8.5 O registro

O pull request é o registro: contém o diff, a conversa de revisão, a execução da CI e a fusão. O bloco de identificação cita-o. Um projeto que queira um rastro de aprovação que ainda possa ler daqui a dez anos obtém-no do repositório, não de um documento separado que pode divergir dele.

## 9. O que é verificado mecanicamente

Regras que podem ser um teste são um teste, e o teste roda na CI. Esta é a parte da política que não se negocia por conveniência.

| Regra | Verificada por |
|---|---|
| As duas línguas das páginas de ajuda trazem os mesmos arquivos, os mesmos ids de página e de seção, e referências cruzadas resolvíveis | `src/test-docs.py` |
| Todo diretório de língua sob `data/help/` está registrado em `help_languages`, e toda língua registrada tem um diretório | `src/test-docs.py` |
| Todo arquivo nomeado pela lista de instalação do meson existe, para cada língua registrada | `src/test-docs.py` |
| A tabela de rótulos do manual é a que o catálogo de tradução produz | `src/test-docs.py`, com `--write-table` para a regenerar |
| As duas árvores de documentação estão em sintonia: cada item formal numa tem a sua contraparte na outra, e os capítulos do manual têm as mesmas seções | `src/test-docs.py` |
| Cada link e cada âncora do manual resolve | `src/test-docs.py` |
| Cada capítulo do manual traz um bloco de identificação | `src/test-docs.py` |
| O código continua a comportar-se como a suíte diz | a suíte do meson |

O teste prova que cada regra pode falhar com uma entrada sintética, tal como fazem os outros testes de regras do projeto. Uma verificação que não pode falhar não é uma verificação.

## 10. Controlo de configuração

### 10.1 Linhas de base

Uma **linha de base** é o conjunto da documentação aprovada num versão estável. O versão estável é o corte: a versão em `meson.build` é o identificador da linha de base, e os itens aprovados nesse momento são o seu conteúdo.

Entre linhas de base, `main` é o estado de trabalho. Um documento em `main` que não passou por revisão é um rascunho, independentemente do que o seu campo de estado diga.

### 10.2 Controlo de alterações

Toda alteração a qualquer documento é um pull request contra `main`, revisado e fundido nos termos da seção 8. Não há outra porta de entrada: nem um push direto, nem uma edição na árvore de trabalho que salte a revisão.

Uma alteração que toca o código e a sua documentação entra em **um** pull request. Uma alteração a um rótulo que o renomeia no código, na página de ajuda, no manual e na tabela de rótulos é uma alteração, não quatro — e se as quatro não puderem ir juntas, a alteração ao código espera.

### 10.3 Histórico

- O **histórico do repositório** é o histórico de alterações de registro para cada documento do repositório.
- A **tabela de histórico de alterações** de um item é o resumo para o leitor: o que mudou, quando e porquê. Não é um segundo registro a manter.
- A **mensagem de commit** diz o que mudou e porquê, no imperativo, e indica o motivo quando há um que não é óbvio a partir do diff.

## 11. Distribuição

| Item | Fica em | Acesso por | Quem pode alterá-lo |
|---|---|---|---|
| O manual | `Docs/manual/` (inglês), `Docs/pt_BR/manual/` (português) | O repositório; `Docs/README.md` e `Docs/pt_BR/README.md` ligam-nos | Qualquer pessoa, através de um pull request |
| As páginas de ajuda | `data/help/<lang>/` | Instaladas com o aplicativo, sob `share/help/<lang>/`; abertas com F1 | Qualquer pessoa, através de um pull request |
| Os documentos de referência e de plano | `Docs/` (inglês), `Docs/pt_BR/` (português, para o conjunto formal) | O repositório; os dois índices listam-nos | Qualquer pessoa, através de um pull request |
| As notas de trabalho | Raiz do repositório | O repositório | Qualquer pessoa, através de um pull request |
| Os catálogos de tradução | `po/` | O repositório; compilados para dentro do aplicativo | Qualquer pessoa, através de um pull request |

**`Docs/README.md` é o índice**, e um item que não está listado ali não é distribuído. O `README.md` da raiz aponta para ele. Dois níveis, ambos mantidos: o da raiz para as pessoas que decidem se instalam, o índice para todos os outros.

As páginas de ajuda são a única documentação que chega a um usuário que nunca abre o repositório, e é por isso que são instaladas em vez de apenas mantidas.

## 12. Rastreabilidade

Cada item formal nomeia o código que descreve. O manual faz isso no seu capítulo 12; a tabela abaixo é a visão ao nível do projeto.

| Tipo de afirmação num documento | Verificada contra | Por |
|---|---|---|
| Um rótulo da interface | `src/`, e `po/*.po` para a tradução | O teste de documentação, para a tabela de rótulos; o código, para o rótulo em si |
| Um atalho de teclado | `src/main.py`, `src/shortcuts-dialog.ui` | A leitura do código; o teste de ciclo de vida da suíte |
| Um item de menu | `src/window.ui`, `src/main.py` | A leitura do código |
| Uma preferência e o que ela faz | `src/preferences.py`, `data/io.github.mauriciobc.Babble.gschema.xml` | A leitura do código |
| Uma mensagem de erro ou um aviso | `src/`, pela sua string de tradução | A leitura do código; `src/test-ui-strings.py` para as regras das strings |
| Um limite | A declaração do servidor (`max_characters`, `max_media_attachments`, …) | O servidor; o manual diz isso em vez de apresentar um número como universal |
| Uma dependência da versão do servidor | `src/capabilities.py`, `src/api/` | `src/test-api-registry.py`, e a tabela gerada de `Docs/mastodon-compat.md` |
| Um comportamento | A suíte | A suíte do meson |

Um documento que faz uma afirmação sobre o software e não diz de onde veio essa afirmação tem uma afirmação que apodrece.

## 13. A lista de conformidade

O revisor executa isto contra o item antes de o aprovar. É curta de propósito.

**Todos os itens:**

- [ ] O bloco de identificação está completo: identificador, versão, estado, data, aprovador nomeado, âmbito, propósito.
- [ ] O âmbito diz o que o item não cobre.
- [ ] O histórico de alterações registra a alteração e porquê.
- [ ] O índice que lista o item lista a versão atual.

**Um item voltado ao usuário:**

- [ ] Toda afirmação sobre um rótulo, um atalho, um item de menu, uma preferência, uma mensagem ou um limite foi verificada contra o código, e as verificações estão nomeadas no pull request.
- [ ] Cada procedimento traz o modelo completo: objetivo, pré-condições, passos numerados, resultado e uma indicação para a solução de problemas.
- [ ] Cada passo está no imperativo, e cada um tem um resultado observável.
- [ ] Nenhuma instrução depende de uma imagem ou de uma cor.
- [ ] Toda referência cruzada resolve, e todo termo usado está definido no glossário.
- [ ] O mesmo termo significa a mesma coisa em todo o item.

**Um item traduzido:**

- [ ] O conjunto traduzido traz os mesmos arquivos e os mesmos ids da língua a partir da qual foi feito.
- [ ] Toda string que é um rótulo da interface é traduzida pelo catálogo, não editada à mão no documento.

**Uma alteração ao software:**

- [ ] A documentação que o descreve mudou no mesmo pull request, ou o pull request diz porquê não.

## 14. Manutenção

### 14.1 Gatilhos

Um documento é atualizado quando o seu gatilho dispara. Não "em algum momento": na alteração que o dispara.

| Gatilho | O que tem de mudar |
|---|---|
| Um rótulo da interface muda | O manual, a página de ajuda e a tabela de rótulos |
| Um atalho muda | O capítulo de referência do manual e a página de teclado da página de ajuda |
| Um item de menu é acrescentado ou removido | O manual e a página de ajuda em que aparece |
| Uma preferência é acrescentada, removida ou muda de sentido | O capítulo de preferências do manual e a página de configurações da página de ajuda |
| Uma mensagem ou um aviso muda | A tabela de mensagens do manual |
| Um limite muda | A tabela de limites do manual |
| Uma nova funcionalidade é lançada | `Docs/features.md`, o manual e as páginas de ajuda |
| Uma barreira de capacidade do servidor muda | `Docs/mastodon-compat.md` e a tabela de limites do manual |
| A compilação muda | `Docs/building.md`, o manifesto flatpak |
| O fluxo de entrada muda | `Docs/signing-in.md`, o primeiro capítulo do manual |
| Uma string muda | O catálogo e a tabela de rótulos do manual |
| Um item formal muda em inglês | A sua edição em português, no mesmo pull request |
| É cortado um versão estável | Uma nova linha de base; a versão do software no manual |

### 14.2 Revisão periódica

- **A cada versão estável**, os capítulos do manual e as páginas de ajuda que o versão estável tocou são relidos contra o aplicativo compilado, não contra o diff.
- **Uma vez por ano**, ou sempre que o próprio processo mudar, esta política é revista contra o que aconteceu de facto.
- Os documentos de plano, auditoria e estudo sob `Docs/` são **notas de trabalho**. Não são mantidos depois de concluído o trabalho que descrevem; são datados, e os que descrevem trabalho terminado são marcados como históricos na seção 3.2.

### 14.3 Obsolescência e aposentadoria

Um item que já não descreve nada é marcado `obsolete` no seu bloco de identificação, com uma linha que diz o que o substituiu. Não é eliminado: o histórico das decisões do projeto vale mais do que a arrumação de um diretório. Um documento que foi aposentado continua legível.

## 15. Indicadores de saúde

Quatro medidas, todas contáveis sem ferramenta:

| Indicador | Como contar | Alvo |
|---|---|---|
| Itens formais com um bloco de identificação completo | `src/test-docs.py` | 100 % |
| Marcadores `[A DEFINIR: …]` deixados em itens aprovados | Uma busca sobre `Docs/manual/` | 0 |
| Documentos em `Docs/` não listados em `Docs/README.md` | Uma busca sobre o índice | 0 |
| Itens formais numa língua sem contraparte na outra | `src/test-docs.py` | 0 |
| Funcionalidades lançadas ausentes do manual e das páginas de ajuda | A leitura do versão estável contra o manual | 0 |

O primeiro é imposto pela suíte. O segundo e o terceiro são buscas baratas; o quarto é o que precisa de uma pessoa, e é por isso que fica escrito.

## 16. O que esta política não faz

- Não exige um documento para cada alteração. A maioria das alterações não precisa de nenhum.
- Não exige que os itens formais sejam longos. O manual é longo porque o software tem muita superfície; uma funcionalidade pequena não precisa de um manual longo.
- Não cria uma especificação de requisitos, um modelo de processo nem um registro de conformidade.
- Não substitui a suíte. A suíte é a barreira; esta política diz que documentos têm de estar de acordo com ela.

## 17. Histórico de alterações

| Versão | Data | Autor | Alteração |
|---|---|---|---|
| 1.0 | 2026-11-11 | Os mantenedores do Babble | Primeira versão. Escrita depois do manual do usuário e das páginas de ajuda em português, que mostraram o que faltava: um rito escrito, um aprovador nomeado e um teste que mantém a documentação fiel ao código. Aprovada na fusão do PR #107. |
| 1.1 | 2026-11-11 | Os mantenedores do Babble | A documentação é escrita duas vezes: uma nova secção sobre as duas árvores de idiomas, a regra de nomenclatura de uma tradução, e a verificação mecânica que mantém as duas edições em passo. |. Approved in the merge of PR #108.
| 1.2 | 2026-11-11 | Os mantenedores do Babble | Uma correção de conteúdo nos termos da seção 4.2 (MINOR): o inventário (seção 3.2) lista `Docs/glossary.md`, uma nota de trabalho para contribuidores (os termos de domínio em que o código se apoia; fica em inglês, como a seção 3.5 diz das notas de trabalho), e a seção 1 conta vinte e oito arquivos sob `Docs/` em vez de vinte e sete. O que a política diz não muda. Aprovada na fusão do PR #109. |
| 1.3 | 2026-11-13 | Os mantenedores do Babble | Uma correção de conteúdo nos termos da seção 4.2 (MINOR): a seção 1 conta vinte e nove arquivos sob `Docs/` em vez de vinte e oito, porque `Docs/architecture-candidates-5-7-plan.md` é uma nova nota de trabalho (um plano, já coberto pela linha `Docs/*-plan.md` da seção 3.2, de modo que o inventário não muda). O que a política diz não muda. |
