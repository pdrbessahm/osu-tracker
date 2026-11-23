# osu-tracker

Guarda automaticamente toda jogada sua de osu!, inclusive os fails, num banco no seu proprio PC.
A ideia e transformar esse historico em decisoes de treino: que mapa farmar, que habilidade treinar e se os seus misses estao caindo.

Estado atual: coleta automatica.
O painel com os graficos vem nas proximas versoes.

## O que ele faz

- Fica rodando em segundo plano, sem janela, desde o logon.
- Com o osu! fechado, so confere a cada 5 segundos se o jogo abriu; nao acessa a internet.
- Com o osu! aberto, busca suas jogadas recentes na API oficial do osu! a cada 2 minutos.
- Quando voce fecha o jogo, faz uma ultima coleta.
- Cada jogada entra uma vez so no banco, mesmo que apareca em varias coletas.

Tudo fica no seu PC: nada e enviado a lugar nenhum alem das consultas a API do osu!.
Feito primeiro para Windows com osu!stable, mas tambem roda no Linux e no macOS (detecta o osu!stable e o lazer).

## Requisitos

- Python 3.11 ou mais novo (no Windows, pela Microsoft Store ou por python.org).
- Uma conta no osu!.

## Instalacao

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

Rode o assistente:

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

No PowerShell, dentro da pasta do projeto:

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
```

So roda uma coleta por vez: se duas forem disparadas juntas (por exemplo o detector e um `coletar` manual), a segunda e pulada e registrada no log.
Tambem so roda um detector por vez.

## Detalhes

- A API do osu! so mostra as jogadas recentes por um tempo limitado, por isso a coleta acontece enquanto voce joga.
  Se uma coleta falhar (internet caiu, PC desligou logo depois do jogo), a proxima sessao recupera o que ainda estiver na API.
- Respeita os termos da API: no maximo 1 requisicao por segundo, bem abaixo do limite de 60 por minuto, com espera progressiva em erro.
  Uma coleta usa 1 a 3 requisicoes e o token de acesso e reaproveitado.
- Cada jogada guarda a resposta completa da API (`raw_json`) e os principais campos em colunas: misses, 300/100/50, acc, combo, mods, pp e se passou.
- Em fail, os contadores e o `rank` cobrem so o trecho jogado, nao o mapa inteiro.
- `accuracy` vai de 0 a 1 e conta os misses.

## Testes

```sh
python -m unittest discover -s tests
```

`tests/e2e_detector.py` faz um teste ponta a ponta no Linux ou macOS com a API real e a sua configuracao: simula o jogo abrindo e fechando e duas coletas ao mesmo tempo.

## Licenca

MIT, ver `LICENSE`.
