# Documentação do Babble

O [README](../README.md) destina-se a quem decide se instala o Babble.
Todo o resto está aqui.

**Esta é a edição em português.** A edição em inglês do conjunto formal
está em [`../`](../README.md): [English](../README.md).

## Para quem usa

| | | |
|---|---|---|
| [O manual do usuário](manual/README.md) | O guia completo: entrar, ler, escrever, mídia, Catch Up, configurações, plugins, solução de problemas | [EN](../manual/README.md) |
| [O que o Babble faz](features.md) | A lista completa de recursos, e de que versão do servidor cada um depende | [EN](../features.md) |
| [Compilar e testar](building.md) | Dependências, meson, o manifesto Flatpak, a suíte de testes | [EN](../building.md) |
| [Entrar na conta](signing-in.md) | O que o Babble pede, e como o navegador devolve o login | [EN](../signing-in.md) |
| [Integração com o ambiente de trabalho](desktop-integration.md) | Todas as funcionalidades do GNOME que o Babble usa, e o que cada uma precisa | [EN](../desktop-integration.md) |

## Para quem contribui

| | |
|---|---|
| [Tradução](localization.md) | Idiomas, estado da tradução, e como acrescentar um |
| [Compatibilidade com a API do Mastodon](mastodon-compat.md) | Onde o Babble depende de capacidades do servidor, e porquê |
| [Registo de melhorias](../IMPROVEMENTS.md) | Problemas em aberto, decisões, e o que foi adiado |
| [Perfilar a aplicação em execução](../performance-profiling.md) | A linha de base da interface na `main`, como registar uma comparação, e tudo o que se encontrou até agora |
| [Revisão GNOME](../gnome-review.md) | Auditoria de HIG, GTK4/libadwaita, arquitetura e empacotamento de uma revisão |
| [Padrões de interface GTK 4/libadwaita](../gtk4-ui-patterns.md) | Como oito aplicações GTK 4 (escolhidas pela cobertura, não pela popularidade) constroem a sua interface, e como o Babble se compara com elas |
| [Propostas de interface: estudo de implementação](../ui-proposals-implementation-study.md) | Como implementar cada proposta desse relatório, com os resultados das experiências e a ordem para as fazer |
| [Propostas de interface: plano por tarefas](../ui-proposals-plan.md) | Pacotes de trabalho e tarefas com ficheiros, critérios de aceitação e ordem |
| [Plano de correcção da revisão GNOME](../gnome-review-remediation-plan.md) | Correções faseadas dessa auditoria |
| [Timeline ranqueada: desenho](../timeline-algorithm.md) | Um lugar na barra lateral, ranqueado: BYOTA e o "For You" da X comparados, a hipótese, os portões de avaliação, e a fronteira com o Catch Up |
| [Timeline ranqueada: plano de implementação](../ranked-timeline-plan.md) | Fatias, histórias de usuário e tarefas prontas para agentes, com critérios de aceitação e uma definição de "feito" |
| [Estratégia](../ROADMAP.MD) | Em que o projecto está a apostar |

O `CLAUDE.md` na raiz do repositório são as notas de trabalho para agentes
de IA que editam este código: arquitetura, convenções, e as regras de
memória que é fácil quebrar por acidente.

## Documentos de referência na raiz do repositório

| | |
|---|---|
| `PERFORMANCE.md` | As linhas de base da timeline, e porquê de cada optimização |
| `MEMORY-LEAKS.md` | Os padrões de fuga de memória encontrados até agora, e como os verificar |
| `CATCH-UP.md` | A especificação do Catch Up |

## Como a documentação é governada

| | |
|---|---|
| [Política de documentação](../documentation-policy.md) | O que é documentado, quem escreve, quem aprova, e quando muda |

A política é um item formal por si só, e as regras que ela diz serem
verificáveis mecanicamente são verificadas por `src/test-docs.py`, que
corre com a suíte.

### As duas línguas

O conjunto formal -- este índice, o manual e os seis documentos de
referência acima -- é escrito duas vezes. A regra é uma frase:

> A edição em inglês fica onde o ficheiro já está. Uma edição em
> português do Brasil fica ao lado, em `Docs/pt_BR/`, espelhando
> exactamente o caminho.

Assim, a tradução de `features.md` é `pt_BR/features.md`, e os capítulos
do manual são `manual/NN-<slug-em-inglês>.md` e
`pt_BR/manual/NN-<slug-em-português>.md`.

Os documentos da secção "Para quem contribui" são as notas de trabalho do
próprio projecto: auditorias, planos, benchmarks e registos, cujo
público é quem trabalha neles. Continuam em inglês, que é o que a
[política](../documentation-policy.md) diz deles.

O inglês é a fonte. Uma alteração é feita em inglês e traduzida, no
mesmo pull request.

## Para tradutores

[po/README.md](../../po/README.md).

As páginas de ajuda não são traduzidas através do `po/`: uma página
Mallard é um documento por si só, por isso uma tradução é uma página
traduzida ao lado da inglesa em `data/help/<lang>/`, acrescentada a
`help_languages` em `data/meson.build`. Os dois conjuntos mantêm-se em
passo -- os mesmos ficheiros, os mesmos identificadores de página e
secção -- e é o `src/test-docs.py` que os mantém assim.
