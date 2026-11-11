# Compilando o Babble

## Dependências

| | |
|---|---|
| GTK | 4.24+ |
| libadwaita | 1.10+ (GNOME 51) |
| GtkSourceView | 5, com libspelling 0.4+ |
| libsoup | 3 |
| libsecret | para o chaveiro de tokens |
| meson | 1.0+ |
| Python | 3, com PyGObject |
| Yelp | de execução, para o F1 mostrar as páginas de ajuda |

O runtime do GNOME 51 não traz o libspelling, então o manifesto de
desenvolvimento do Flatpak o compila a partir do código-fonte. Em uma máquina,
instale-o pela sua distribuição (`libspelling` / `libspelling-devel`).

O Yelp é quem trata a URI `help:` que a tecla F1 distribui; ele vem no runtime
do GNOME 51, mas uma instalação na máquina precisa do pacote `yelp` da
distribuição. Sem ele, o Babble abre a documentação online, de modo que as
páginas em `share/help/` são um comforto, não uma exigência. Uma compilação não
instalada segue esse caminho por um segundo motivo: as páginas em `data/` não
ficam onde um visualizador procura, e `python3 build/src/babble` não as instala.

## Compilação

```sh
meson setup build
meson compile -C build
meson test -C build
meson install -C build
```

`python3 build/src/babble` executa a compilação não instalada. Ela não é
executável antes de ser instalada, e só funciona com o diretório de compilação
diretamente dentro da árvore do código-fonte (ele resolve `../../src`).

## Flatpak

O manifesto em `build-aux/flatpak/io.github.mauriciobc.Babble.json` é um
manifesto de desenvolvimento local apenas — veja [flathub.md](../flathub.md).

```sh
flatpak-builder --user --install --force-clean \
    build-flatpak build-aux/flatpak/io.github.mauriciobc.Babble.json
flatpak run io.github.mauriciobc.Babble
```

A CI compila esse manifesto a cada push, então uma URL de código-fonte
renomeada ou um tarball movido falha ali, e não na máquina de alguém.

## Testes

`meson test -C build` roda tudo: os testes do meson mais os validadores de
desktop/metainfo/gschema/catálogo.

```sh
meson test -C build                        # tudo
meson test -C build 'timeline memory'      # um teste, pelo nome
meson test -C build -v 'compose widgets'   # -v mostra as linhas ok/FAIL de cada teste
xvfb-run -a meson test -C build            # sem tela
```

Cada teste é um `src/test-*.py` autônomo que imprime linhas `ok - …` / `FAIL - …`.
Ele sai com 77 (um skip do Meson) quando não há `DISPLAY`, ou — para
`timeline render` e `thread profile render` — quando não há rede, já que esses
dois buscam dados públicos ao vivo em mastodon.social.

Um teste novo é registrado em `src/meson.build` com o mesmo padrão de ambiente.
**Um módulo Python novo também precisa entrar na lista `install_data` dali**,
senão o aplicativo instalado não o terá, e precisa ser listado em
`po/POTFILES.in` se contiver strings traduzíveis.

## Benchmark e perfil

```sh
./autoresearch.sh    # carga da timeline contra um servidor de fixtures local
```

Avalie a fluidez pelos números da thread principal (`main_ms`, `stall_max_ms`,
`stall_p99_ms`, `stalls_over_16ms`), e não por `cpu_ms`, que conta todas as
threads. O `PERFORMANCE.md` registra as linhas de base e por que cada otimização
foi feita.

Memória: siga o `MEMORY-LEAKS.md`. `python3 bench/memwatch.py --trim` inicia o
aplicativo não instalado e relata objetos vivos, caches, texturas e heap ao
pressionar Enter. O aplicativo é de instância única, então feche antes qualquer
cópia em execução.
