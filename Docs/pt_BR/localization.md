# Localização

O Babble fala português do Brasil, alemão, francês e espanhol. A interface
segue o idioma do seu sistema, a menos que você escolha um em
Preferências → Geral → Idioma, que passa a valer na próxima vez que o Babble
iniciar.

## Estado das traduções

Gerado a partir de `msgfmt --statistics`. **Os quatro catálogos são
assistidos por máquina e ainda precisam de revisão de falantes nativos**, ainda
que já estejam completos.

| Idioma | Traduzidas | Fuzzy | Não traduzidas |
|---|---|---|---|
| `de` | 1843 | 0 | 0 |
| `es` | 1843 | 0 | 0 |
| `fr` | 1843 | 0 | 0 |
| `pt_BR` | 1843 | 0 | 0 |

Não sobrou nenhuma entrada fuzzy ou não traduzida. Entradas fuzzy são palpites
de máquina trazidos de uma execução anterior de `babble-update-po`, quando uma
string é renomeada; elas não são usadas em execução (veja abaixo).

## Flatpak

As traduções do próprio Babble vão dentro do aplicativo
(`"separate-locales": false`), então todos os idiomas do seletor funcionam,
independentemente do idioma do seu sistema. As poucas strings que vêm do GTK e
do libadwaita moram na extensão de locale do runtime, que instala apenas os
idiomas do seu sistema. Para ter essas também, adicione o idioma ao Flatpak e
atualize:

```sh
flatpak config --set extra-languages de
flatpak update
```

## Trabalhando nos catálogos

Depois de alterar strings:

```sh
meson compile -C build babble-pot          # regerar po/babble.pot
meson compile -C build babble-update-po    # mesclar em todos os po/*.po
```

Para adicionar um idioma, crie `po/<lang>.po` a partir do modelo, liste-o em
`po/LINGUAS` e adicione-o — com o nome no próprio idioma — a
`INTERFACE_LANGUAGES` em `src/preferences.py`.

Todo arquivo de código-fonte que contenha strings traduzíveis está listado em
`po/POTFILES.in`. Um módulo novo com strings precisa entrar lá também, ou as
suas strings nunca chegam ao catálogo.

Os testes `po check <lang>` executam `msgfmt --check-format`, que pega uma
tradução cujos especificadores de formato não batem com os do original — um
erro que, de outro modo, só apareceria em execução.

## Entradas fuzzy não são um defeito a ser limpo

`babble-update-po` executa `msgmerge`, que anexa sem pensar a tradução de uma
string antiga parecida a uma renomeada e marca o resultado como `fuzzy`. Algumas
dessas sugestões estão erradas — `ID of the active account` herda o alemão de
"Could not add the account" — e é exatamente para isso que serve o marcador:
**o gettext deixa as entradas fuzzy de fora do catálogo compilado**, então o
usuário vê a string em inglês em vez de uma tradução errada.

Deixe-as para um tradutor aceitar ou descartar. Em particular, não execute
`msgattrib --clear-fuzzy` para arrumar as contagens: isso promove texto não
verificado a "traduzido" e, como algumas sugestões carregam especificadores de
formato que não batem com o novo original, faz `msgfmt --check` — e com ele a
CI — falhar de vez. As contagens de `msgfmt --statistics` são um indicador
aproximado de progresso, não uma lista de defeitos.

Vale a pena saber de duas coisas ao lê-las: strings renomeadas normalmente
deixam uma entrada obsoleta `#~` com a tradução antiga (para que um tradutor
possa recuperá-la), e strings que o msgmerge consumiu como correspondência
fuzzy em vez disso não deixam nada para trás.

Veja [po/README.md](../../po/README.md) para notas destinadas a tradutores.
