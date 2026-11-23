# osu-tracker

Guarda automaticamente toda jogada sua de osu!, inclusive os fails, num banco no seu proprio PC.
A ideia e transformar esse historico em decisoes de treino: que mapa farmar, que habilidade treinar e se os seus misses estao caindo.

Estado atual: coleta automatica, dificuldade de cada mapa com os mods que voce usou, uma janela de configuracao e status e um painel com graficos no navegador (no Windows, tudo num `osu-tracker.exe` que nao precisa de Python).

## O que ele faz

- Fica rodando em segundo plano, sem janela, desde o logon.
- Com o osu! fechado, so confere a cada 5 segundos se o jogo abriu; nao acessa a internet.
- Com o osu! aberto, busca suas jogadas recentes na API oficial do osu! a cada 2 minutos.
- Quando voce fecha o jogo, faz uma ultima coleta.
- Cada jogada entra uma vez so no banco, mesmo que apareca em varias coletas.

Tudo fica no seu PC: nada e enviado a lugar nenhum alem das consultas a API do osu!.
Feito primeiro para Windows com osu!stable, mas tambem roda no Linux e no macOS (detecta o osu!stable e o lazer).

## Instalacao no Windows (osu-tracker.exe)

Nao precisa de Python.

1. Baixe o `osu-tracker.exe` da pagina Releases deste repositorio e coloque numa pasta fixa, por exemplo `Documentos\osu-tracker` (o inicio com o Windows aponta para ele; se mudar de lugar, desmarque e marque a opcao de novo).
2. De dois cliques nele.
   Como o executavel nao e assinado, o Windows pode mostrar "O Windows protegeu o computador": clique em "Mais informacoes" e "Executar assim mesmo".
3. Na aba *Configuracao*, siga o passo a passo para criar o seu app OAuth no osu!, cole o Client ID e o Client Secret (Ctrl+V ou botao direito > Colar), escreva seu nick ou ID e clique em "Testar e salvar".
   O osu-tracker confere tudo na API antes de gravar.
4. Na aba *Status*, clique em "Iniciar" e marque "Iniciar o detector junto com o Windows".

A janela mostra se o detector esta rodando (com o botao para iniciar ou parar), a ultima coleta e quantas jogadas ela trouxe, o total de jogadas no banco e botoes para abrir o painel, a pasta de dados e o log.
Fechar a janela nao para o detector: ele continua em segundo plano, sem janela.
Para voltar a janela, abra o `osu-tracker.exe` de novo.

Para remover: desmarque "Iniciar o detector junto com o Windows", clique em "Parar" e apague o `.exe`.
Seus dados ficam na pasta indicada em "Onde a configuracao e os dados ficam", abaixo.

## Instalacao pelo codigo-fonte (Windows, Linux e macOS)

Requisitos:

- Python 3.10 ou mais novo (no Windows, pela Microsoft Store ou por python.org).
- Uma conta no osu!.

Baixe ou clone esta pasta e, dentro dela, crie o ambiente:

Windows (PowerShell):

```powershell
py -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

Linux e macOS:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Configuracao (primeira execucao)

Pelo codigo-fonte, a mesma janela do `.exe` abre com:

```powershell
.venv\Scripts\python -m osu_tracker janela
```

(no Linux e no macOS, `.venv/bin/python -m osu_tracker janela`; precisa do tkinter, que no Linux as vezes vem num pacote separado, como `python3-tk`)

Ou, sem janela, rode o assistente no console:

```powershell
.venv\Scripts\python -m osu_tracker configurar
```

(no Linux e no macOS, `.venv/bin/python -m osu_tracker configurar`)

O Client Secret nao aparece enquanto voce digita ou cola.
No PowerShell e no Prompt de Comando, o Ctrl+V nao cola nesse campo: clique com o botao direito do mouse dentro da janela para colar (no Windows Terminal, o Ctrl+V funciona).

Ele explica e pede duas coisas:

1. Um app OAuth seu no osu!, que permite ler suas jogadas pela API.
   Cada jogador cria o proprio, em 1 minuto:
   - abra https://osu.ppy.sh/home/account/edit#oauth (logado) e clique em "New OAuth Application";
   - de qualquer nome (por exemplo osu-tracker) e deixe "Application Callback URL" em branco;
   - copie o Client ID e o Client Secret para o assistente.
   O Client Secret e como uma senha: nao mostre a ninguem.
2. Seu nick ou ID no osu!.

O assistente confere tudo na API antes de gravar.
Para mudar algo depois, rode o mesmo comando de novo.

Onde a configuracao e os dados ficam:

- Windows: `%LOCALAPPDATA%\osu-tracker`
- macOS: `~/Library/Application Support/osu-tracker`
- Linux: `~/.local/share/osu-tracker`

Nessa pasta ficam `config.toml` (suas configuracoes), `osu_tracker.sqlite3` (o banco) e `osu-tracker.log` (o que aconteceu em cada coleta).
A variavel de ambiente `OSU_TRACKER_DIR` troca essa pasta, se voce quiser guardar em outro lugar.

Se preferir nao gravar as credenciais no `config.toml`, defina `OSU_CLIENT_ID` e `OSU_CLIENT_SECRET` no ambiente ou num arquivo `.env` na pasta do projeto (modelo em `.env.example`); elas tem prioridade sobre o arquivo.

Opcional, no `config.toml`:

```toml
[detector]
processos = ["osu!.exe", "osu!"]   # nomes do processo do jogo
intervalo_checagem = 5              # segundos entre conferencias com o jogo fechado
intervalo_coleta = 120              # segundos entre coletas com o jogo aberto

[banco]
caminho = "D:/osu/meu-banco.sqlite3"
```

## Iniciar junto com o Windows

Pelo `.exe` ou pela janela, basta marcar "Iniciar o detector junto com o Windows" na aba *Status*.
Ela usa os mesmos scripts abaixo.

Pelo codigo-fonte sem janela, no PowerShell, dentro da pasta do projeto:

```powershell
powershell -ExecutionPolicy Bypass -File windows\instalar.ps1
```

O script roda o assistente (se faltar configuracao), registra uma tarefa no Agendador de Tarefas que inicia o osu-tracker a cada logon, sem janela, e ja o deixa rodando.
Nao precisa de administrador.
Se o Agendador recusar, ele cria um atalho na pasta Inicializar no lugar; para ir direto pelo atalho, use `windows\instalar.ps1 -Atalho`.

Para desfazer (seus dados continuam na pasta):

```powershell
powershell -ExecutionPolicy Bypass -File windows\desinstalar.ps1
```

No Linux e no macOS, coloque `/caminho/do/projeto/.venv/bin/python -m osu_tracker` nos aplicativos de inicio da sua sessao.

## Outros comandos

```sh
python -m osu_tracker              # detector (roda o assistente antes, se ainda nao houver configuracao)
python -m osu_tracker coletar      # uma coleta agora, com ou sem o jogo aberto
python -m osu_tracker detectar     # so o detector
python -m osu_tracker configurar   # assistente
python -m osu_tracker janela       # janela de configuracao e status
python -m osu_tracker painel       # painel com os graficos no navegador (Ctrl+C fecha)
python -m osu_tracker marcar       # marca o tipo de um mapa (veja Progresso)
```

O `painel` aceita `--porta 8000` (padrao: uma porta livre) e `--sem-navegador` (so sobe o painel e mostra o endereco).

O `osu-tracker.exe` aceita os mesmos comandos (`osu-tracker.exe coletar`, por exemplo); sem comando, abre a janela.
Ele e um programa de janela, entao num terminal a saida aparece mas o terminal nao espera ele terminar; o que aconteceu fica sempre no log.
A configuracao pelo `.exe` e pela janela; o assistente de console e para quem roda pelo codigo-fonte.

So roda uma coleta por vez: se duas forem disparadas juntas (por exemplo o detector e um `coletar` manual), a segunda e pulada e registrada no log.
Tambem so roda um detector por vez.

## Detalhes

- A API do osu! so mostra as jogadas recentes por um tempo limitado, por isso a coleta acontece enquanto voce joga.
  Se uma coleta falhar (internet caiu, PC desligou logo depois do jogo), a proxima sessao recupera o que ainda estiver na API.
- Respeita os termos da API: no maximo 1 requisicao por segundo, bem abaixo do limite de 60 por minuto, com espera progressiva em erro.
  Uma coleta usa 1 a 3 requisicoes, mais uma por combinacao de mapa e mods ainda nao vista, e o token de acesso e reaproveitado.
- Cada jogada guarda a resposta completa da API (`raw_json`) e os principais campos em colunas: misses, 300/100/50, acc, combo, mods, pp e se passou.
- Em fail, os contadores e o `rank` cobrem so o trecho jogado, nao o mapa inteiro.
  A coluna `fracao_jogada` diz ate onde voce chegou: notas julgadas (300, 100, 50 e miss) sobre o total de notas do mapa, 1 num pass.
  So e calculada no osu! standard.
- `accuracy` vai de 0 a 1 e conta os misses.

## Mapas

Depois de cada coleta, o osu-tracker guarda a dificuldade dos mapas que voce jogou, com os mods de cada jogada:

- `mapas`: dados do mapa sem mods (artista, titulo, dificuldade, BPM, duracao, AR, OD, CS, HP, numero de notas).
  Vem junto com a jogada, sem consulta extra.
- `mapas_mods`: uma linha por mapa e combinacao de mods, com SR, aim, speed, slider factor, combo maximo e os demais atributos da API (resposta completa em `atributos_json`), mais BPM, duracao, AR, OD, CS e HP ja com os mods.
  A jogada liga com ela pela coluna `mods_chave` (vazia = sem mods, ex.: `HDHR`).

Cada combinacao e consultada uma vez so e fica guardada.
NF, SD, PF e CL nao mudam a dificuldade e nao contam como combinacao diferente.
Cada coleta consulta no maximo 50 combinacoes novas (cerca de 50 segundos); o que sobrar fica para as proximas.
Se o mapa for atualizado pelo mapper, os atributos dele sao consultados de novo.

A API nao devolve AR e OD com mods, entao eles sao calculados aqui (HR, EZ, Difficulty Adjust, DT/NC e HT/DC).
A API tambem ignora velocidade customizada do lazer (por exemplo DT a 1.3x) e calcula SR, aim e speed como 1.5x.
Nesses casos BPM, duracao, AR e OD usam a velocidade real (`taxa`) e a coluna `taxa_api` mostra a velocidade usada pela API.

## Painel

Na janela, o botao "Abrir painel" sobe um servidor so neste PC (`127.0.0.1`, numa porta livre) e abre o painel no navegador.
O painel fica no ar enquanto a janela estiver aberta.
Pelo console, `python -m osu_tracker painel` faz o mesmo.
Ele so le o banco (a unica escrita e a marcacao do tipo de um mapa, na aba Progresso) e funciona sem internet: a pagina e a biblioteca de graficos ([plotly.js](https://github.com/plotly/plotly.js), licenca MIT, em `osu_tracker/web/vendor`) vao junto com o programa.

Quase toda jogada de quem esta subindo de nivel e fail, entao os graficos nao dependem de passar o mapa:

- *misses por 100 notas*: misses sobre as notas julgadas (300, 100, 50 e miss) do trecho jogado;
- *fracao jogada*: ate onde a jogada chegou no mapa (100% num pass);
- *acc*: pela formula do stable, sobre o trecho jogado; *acc so das acertadas* e a mesma conta sem os misses.

Em grupos (dia, mapa, faixa), os totais sao somados antes de dividir: uma tentativa de 30 notas pesa menos que uma de 500.

O que ele mostra, com filtro de periodo (7, 30, 90 dias ou tudo) e de mods:

- *Misses ao longo do tempo*: todos os mapas, um ponto por dia, ou um mapa tentado mais de uma vez, tentativa por tentativa, com a fracao jogada embaixo.
  A tendencia de cada mapa compara a primeira metade das tentativas com a segunda (10% para menos e "caindo", 10% para mais e "subindo").
- *Desempenho por faixa de 0,5 estrela* (com os mods): misses por 100 notas, fracao jogada media e acc.
- *Aim contra speed*: cada jogada e comparada com as da mesma faixa de estrelas; se a razao aim/speed do mapa esta acima da mediana da faixa, conta como mapa de aim, se esta abaixo, como mapa de speed.
  Assim um mapa mais dificil nao parece "de aim" so por ser mais dificil.
- *Acc so das notas acertadas* em cada jogada, ao lado da acc com os misses.
- *Mapas sugeridos para farmar*: para cada mapa (com os mesmos mods) ainda sem FC, a taxa de misses nele vezes as notas do mapa inteiro, ou seja, quantos misses voce faria hoje jogando ate o fim.
  Lista os 10 com a menor estimativa, entre os que ja tem pelo menos 100 notas jogadas.

So osu! standard por enquanto: e o unico modo em que a fracao jogada e as contagens de notas batem.

FC, aqui e na aba Progresso, conta como no jogo: passou, sem miss e sem quebrar o combo (campos `is_perfect_combo` do lazer ou `legacy_perfect` do stable, guardados na coluna `combo_perfeito`).

## Progresso

A aba *Progresso* do painel mostra se voce esta melhorando, com a variacao em 7 e 30 dias:

- *Perfil*: na primeira coleta de cada dia o osu-tracker guarda pp, rank global e no pais, acc, play count, ranked score e nivel (tabela `perfil_diario`).
  O rank global dos 90 dias anteriores vem do historico que o osu! guarda (`rank_history`) e fica em `rank_historico`, entao o grafico de rank ja comeca cheio.
  A variacao compara com a foto de 7 e de 30 dias atras.
- *Estrelas por semana*: a maior dificuldade (estrelas com os mods) que voce passou e a maior com FC, de segunda a domingo, mais a mais dificil que voce tentou.
- *Aim, speed e acc*: o maior aim e o maior speed (atributos da API com os mods) entre os mapas passados e entre os FCs, e a acc de cada semana com o OD medio dos mapas.
- *Skillsets*: stream, jump, tech e alternate / finger control.
  Os numeros de cada tipo vem so das jogadas em mapas daquele tipo: acc, misses por 100 notas, maior pp e FCs.
  Nos blocos e nas tabelas, a variacao compara os ultimos 7 (ou 30) dias com os 7 (ou 30) anteriores.

### Como o tipo do mapa e decidido

Depois de cada coleta o osu-tracker baixa o `.osu` dos mapas que voce jogou (`https://osu.ppy.sh/osu/<id>`, um por segundo, no maximo 30 por coleta), guarda numa pasta `mapas_osu` ao lado do banco e tira dele medidas de padrao (`arquivo_osu.py`): BPM, objetos por segundo, notas em streams (9+ notas a 1/4 ou mais rapido) e em bursts (3 a 8), saltos (intervalo de 1/2 ou mais lento com 2,5 circulos ou mais de distancia), espacamento, mudancas de ritmo, divisores usados (1/2, 1/3, 1/4...), sliders e mudancas de SV.
O `.osu` so e baixado de novo se o mapa for atualizado.

Para cada mapa e tipo vale, nesta ordem:

1. a sua marcacao, sempre;
2. um modelo treinado com as suas marcacoes, quando o tipo tem pelo menos 20 mapas marcados como "e" e 20 como "nao e";
3. uma regra sobre as medidas do `.osu` (mostrada no painel).

O modelo e uma regressao logistica sobre as medidas padronizadas, treinada de novo a cada marcacao.
O painel mostra a confianca de cada classificacao, as medidas que mais pesam no modelo e a precisao estimada: a media do acerto nos mapas que sao e nos que nao sao do tipo, numa validacao cruzada de 5 partes, ao lado da mesma conta para a regra.
Abaixo de 75%, o painel avisa.

### Marcar o tipo de um mapa

No painel, a lista *Mapas para marcar* traz os mapas em que o classificador tem mais duvida: marque os tipos e clique em Salvar (sem nenhum marcado, salva como "nenhum").
Todos os mapas ficam em *Todos os mapas classificados*.
Esta e a unica escrita que o painel faz no banco.
Pela linha de comando:

```sh
python -m osu_tracker marcar 5587991 stream jump   # um ou mais tipos
python -m osu_tracker marcar 5587991 nenhum
python -m osu_tracker marcar 5587991 --limpar      # volta a valer o classificador
python -m osu_tracker marcar --sugerir             # os mapas que mais ensinariam
```

O numero e o ID da dificuldade (o que aparece depois de `#osu/` no endereco do mapa).

## Testes

```sh
python -m unittest discover -s tests
```

Os testes da janela abrem uma janela de verdade e sao pulados se nao houver tela.

`tests/e2e_detector.py` faz um teste ponta a ponta no Linux ou macOS com a API real e a sua configuracao: simula o jogo abrindo e fechando e duas coletas ao mesmo tempo.

`tests/e2e_painel.py` abre o painel com o seu banco num Chromium sem tela, confere que a pagina nao tem erro e salva um print (precisa de `pip install playwright`; veja `--help`).

## Gerar o executavel

O workflow `.github/workflows/executavel.yml` roda os testes (Windows e Linux, Python 3.10 e 3.13) e gera o `osu-tracker.exe` no Windows a cada push.
Numa tag `v*` (por exemplo `v0.1.0`), ele anexa o `.exe` a uma Release com o nome da tag.

Para gerar na sua maquina:

```powershell
.venv\Scripts\pip install -r requirements-build.txt
.venv\Scripts\python -m PyInstaller --noconfirm windows\osu-tracker.spec
```

O resultado fica em `dist\osu-tracker.exe`.

## Licenca

MIT, ver `LICENSE`.
