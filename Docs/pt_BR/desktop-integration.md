# Integração com o ambiente de trabalho

O que o Babble usa do GNOME, e o que cada recurso precisa.

- **Colar e soltar mídia.** Cole uma captura de tela (Print Screen copia uma)
  ou arquivos copiados no compositor, ou arraste imagens e vídeos para ele.
- **Tirar Captura de Tela.** O botão de câmera do compositor tira uma captura
  de tela pelo portal de captura de tela e a anexa. Fica oculto quando o portal
  não existe.
- **Verificação ortográfica.** As palavras escritas errado ficam sublinhadas, e
  o botão direito mostra as correções no idioma da publicação. Se não houver
  dicionário instalado para esse idioma, a verificação ortográfica se desliga
  sozinha.
- **Estado do sistema.** O Babble percebe quando você está off-line e
  reconecta o streaming assim que voltar. No modo de economia de energia, ele
  consulta com menos frequência e para de reproduzir GIFs automaticamente. Em
  conexões medidas, o visualizador de mídia espera você carregar o vídeo e o
  áudio.
- **Formato do relógio.** Os horários seguem a sua configuração de 12 ou 24
  horas.
- **Transferência de mídia.** Arraste publicações e mídias para fora do Babble,
  ou copie uma imagem do visualizador.
- **Publicações de áudio.** Anexe ou abra arquivos de áudio. O Mastodon quer
  o áudio publicado sozinho, e é assim que o Babble o envia.
- **Acessibilidade.** Os leitores de tela anunciam os lotes novos de
  publicações, e o Babble impede que o sistema deslogue ou suspenda enquanto
  uma publicação ainda está sendo enviada.
- **Abrir com o Babble.** Abra imagens e vídeos com o Babble a partir do
  Arquivos, e uma nova publicação já começa com eles anexados.
- **Botões nas notificações.** Favoritar, impulsionar e seguir de volta direto
  da notificação da área de trabalho.
- **Ações do lançador.** Clique com o botão direito no ícone do aplicativo
  para Nova publicação ou Notificações.
- **Busca.** Contas, hashtags e publicações aparecem na visão geral de
  Atividades (ligue o Babble em Configurações → Busca).
- **Status em segundo plano.** Com o Modo em segundo plano ligado, o painel
  rápido mostra quantas menções chegaram enquanto você estava away.
- **Atalho de Postagem Rápida** (Preferências). Super+Shift+M em qualquer
  lugar abre o compositor pelo portal GlobalShortcuts.
- **Execução em segundo plano.** O Babble pode continuar recebendo o streaming
  com a janela fechada, pelo portal de segundo plano, e mostrar uma linha de
  status para isso.
- **Ajuda.** F1, ou Ajuda no menu principal, abre as páginas Mallard em
  `share/help/C/io.github.mauriciobc.Babble/`, por meio de uma URI
  `help:io.github.mauriciobc.Babble` — o esquema que o Yelp registra, e a
  forma que a Desktop Help System Specification dá. (`yelp://` não é um
  esquema que algo trate: o Yelp declara apenas `ghelp`, `help`, `info` e
  `man`.) `window.open_help` pergunta `help_available()` antes: um visualizador
  para o esquema e as páginas em um datadir. Só então ele é iniciado. Isso
  acontece porque uma abertura não pode responder à pergunta — sem um
  visualizador, o portal OpenURI mostra "no apps available" e chama isso de
  sucesso, e sem as páginas instaladas o visualizador relata um documento
  ausente. De qualquer modo, a documentação online é a resposta. Dentro de um
  Flatpak as páginas são deliberadamente ignoradas: a URI `help:` é resolvida
  no host pelo portal, então o visualizador procuraria nos diretórios do host, e
  não em `/app/share/help`, mesmo que o runtime do GNOME 51 traga o Yelp. As
  páginas são dados instalados comuns, e não são traduzidas por `po/`: uma
  página Mallard é um documento, então uma tradução é uma página traduzida ao
  lado da original em inglês, adicionada a `help_languages` em
  `data/meson.build`.

## O que exige uma compilação instalada

A ativação por D-Bus, o provedor de busca e as ações do lançador só funcionam a
partir de uma compilação instalada (`meson install` ou Flatpak), porque o GNOME
os encontra pelos arquivos instalados `.desktop`, de serviço D-Bus e de provedor
de busca.

Os recursos apoiados em portal (captura de tela, segundo plano, atalho de
postagem rápida) precisam do portal de área de trabalho XDG correspondente. Um
portal ausente esconde o recurso, em vez de quebrar alguma coisa.
