# Onde ficam os replays (levantamento para a analise de replay)

Levantamento feito em 23/11/2026, sem implementacao.
Fontes: wiki oficial do osu! (Gameplay/Replay, osr file format, FAQ), wiki do repositorio ppy/osu (User file storage) e o banco local do tracker.
O que nao foi confirmado em fonte esta marcado como "a confirmar".

## osu!stable

Pasta do jogo: padrao do instalador `%LOCALAPPDATA%\osu!` (a confirmar no PC do jogador; da para instalar em outro lugar, entao vai ser configuravel).

Replays exportados:

- Ficam em `<pasta do osu!>\Replays\`.
- Sao gerados ao apertar F2 na tela de resultado.
- Nome: `{jogador} - {artista} - {titulo} [{dificuldade}] ({AAAA-MM-DD}) {modo}.osr`.

Replays das jogadas locais (automaticos):

- Ficam em `<pasta do osu!>\Data\r\`, em pares `.osr` e `.osg` com nome cifrado.
- O indice deles e o `scores.db` (placar local); apagar o `scores.db` perde esses replays.
- So existem para jogadas que entram no placar local, ou seja, jogadas completadas.
- O nome cifrado nao diz qual e o mapa: para casar com uma jogada da API e preciso ler o cabecalho do `.osr` (formato abaixo).
- A wiki recomenda nao renomear esses arquivos; a leitura deve ser so leitura.

Jogada com fail:

- Nao gera replay automatico (a wiki so descreve o replay interno para mapa completado; a confirmar se nada vai para `Data\r`).
- Desde 24/08/2020 da para salvar o replay do fail apertando F2 na tela de game over: vai para `Replays\` como os exportados.
  So no modo Solo e so quando a tela de game over aparece (sair no meio com Esc nao conta, a confirmar).
- Replay online: no banco local, as 60 jogadas coletadas ate agora sao todas fails e todas tem `has_replay = 0`.
  Ou seja, nao da para baixar replay de fail pela API.

## osu!lazer

- Os arquivos importados ficam com nome de hash SHA-256 em `files/` dentro da pasta de dados do lazer, com o indice no `client.realm`.
- Para analisar, o caminho mais simples e o jogador exportar o replay (o lazer gera um `.osr`); a pasta de exportacao exata fica a confirmar.

## Cabecalho do .osr (confirmado na wiki)

Na ordem: modo (byte), versao do jogo (int), MD5 do beatmap (string), nome do jogador (string), MD5 do replay (string), 300, 100, 50, geki, katu, misses (short cada), score total (int), maior combo (short), FC (byte), mods (int, bitmask), barra de vida (string), data (long, ticks do Windows), tamanho dos dados (int), dados do cursor e teclas comprimidos em LZMA, id do score online (long).

Para casar `.osr` com uma jogada do banco: MD5 do beatmap (a API devolve o `checksum` do beatmap), data e contadores (300/100/50/miss).

## O que isso significa para a analise de replay

- Jogadas completadas: o replay ja esta no PC, em `Data\r`, sem o jogador fazer nada.
- Fails: so tem replay se o jogador apertar F2 na tela de game over.
  Como hoje todas as jogadas recentes coletadas sao fails, sem esse habito a analise de misses fica quase sem dados.
- Proposta: ler (sem alterar) `Replays\` e `Data\r\`, casar cada `.osr` com a jogada da API pelo cabecalho e so entao medir o erro de mira.

Perguntas para o jogador antes de comecar:

1. A pasta do osu! no seu PC e `%LOCALAPPDATA%\osu!`?
2. Voce topa apertar F2 nos fails que quer analisar?
