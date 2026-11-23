"""Configuracao por usuario.

Tudo do jogador fica numa pasta so, fora do repositorio:
- Windows: %LOCALAPPDATA%\\osu-tracker
- macOS:   ~/Library/Application Support/osu-tracker
- Linux:   $XDG_DATA_HOME/osu-tracker (padrao ~/.local/share/osu-tracker)
A variavel OSU_TRACKER_DIR troca essa pasta (util para testes ou para guardar em outro lugar).

Nela ficam config.toml (criado pelo assistente), o banco, o log e as travas.
As credenciais podem vir do config.toml ou do ambiente (OSU_CLIENT_ID e OSU_CLIENT_SECRET,
inclusive de um arquivo .env na pasta do projeto); o ambiente tem prioridade.
"""

import json
import os
import sys
try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10: o mesmo leitor, pelo pacote tomli
    import tomli as tomllib
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parent.parent
NOME_APP = "osu-tracker"
ARQUIVO_CONFIG = "config.toml"
BANCO_PADRAO = "osu_tracker.sqlite3"
MODOS = ("osu", "taiko", "fruits", "mania")

# Nome do processo do jogo: osu!.exe no Windows (stable e lazer) e no Wine; osu! no lazer de Linux e macOS.
PROCESSOS_PADRAO = ("osu!.exe", "osu!")
INTERVALO_CHECAGEM_PADRAO = 5      # segundos entre conferencias do processo
INTERVALO_COLETA_PADRAO = 120      # segundos entre coletas com o jogo aberto


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    user_id: int
    username: str
    mode: str
    db_path: Path
    client_id: str
    client_secret: str
    processos: tuple[str, ...]
    intervalo_checagem: float
    intervalo_coleta: float


def pasta_app() -> Path:
    if os.environ.get("OSU_TRACKER_DIR"):
        return Path(os.environ["OSU_TRACKER_DIR"]).expanduser()
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / NOME_APP


def caminho_config() -> Path:
    return pasta_app() / ARQUIVO_CONFIG


def carregar_ambiente() -> None:
    """Le o .env da pasta do projeto, se existir, sem sobrescrever o ambiente."""
    load_dotenv(RAIZ / ".env")


def credenciais_do_ambiente() -> tuple[str, str]:
    carregar_ambiente()
    return os.environ.get("OSU_CLIENT_ID", "").strip(), os.environ.get("OSU_CLIENT_SECRET", "").strip()


def ler_arquivo() -> dict:
    caminho = caminho_config()
    if not caminho.exists():
        return {}
    with open(caminho, "rb") as f:
        try:
            return tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise ConfigError(f"{caminho} esta com erro de formato: {e}") from None


def carregar() -> Config:
    dados = ler_arquivo()
    caminho = caminho_config()
    if not dados:
        raise ConfigError(
            f"Ainda nao ha configuracao em {caminho}. Rode o assistente: python -m osu_tracker configurar"
        )

    jogador = dados.get("jogador", {})
    api = dados.get("osu_api", {})
    detector = dados.get("detector", {})
    banco = dados.get("banco", {})

    if "user_id" not in jogador:
        raise ConfigError(f"Falta [jogador] user_id em {caminho}. Rode: python -m osu_tracker configurar")

    env_id, env_secret = credenciais_do_ambiente()
    client_id = env_id or str(api.get("client_id", "")).strip()
    client_secret = env_secret or str(api.get("client_secret", "")).strip()
    faltando = [n for n, v in (("client_id", client_id), ("client_secret", client_secret)) if not v]
    if faltando:
        raise ConfigError(
            f"Faltam credenciais do app OAuth ({', '.join(faltando)}): "
            f"rode python -m osu_tracker configurar ou defina OSU_CLIENT_ID e OSU_CLIENT_SECRET."
        )

    mode = jogador.get("mode", "osu")
    if mode not in MODOS:
        raise ConfigError(f"[jogador] mode invalido em {caminho}: {mode!r} (use {', '.join(MODOS)})")

    db_path = Path(banco.get("caminho", BANCO_PADRAO)).expanduser()
    if not db_path.is_absolute():
        db_path = pasta_app() / db_path

    return Config(
        user_id=int(jogador["user_id"]),
        username=str(jogador.get("username", "")),
        mode=mode,
        db_path=db_path,
        client_id=client_id,
        client_secret=client_secret,
        processos=tuple(detector.get("processos", PROCESSOS_PADRAO)),
        intervalo_checagem=float(detector.get("intervalo_checagem", INTERVALO_CHECAGEM_PADRAO)),
        intervalo_coleta=float(detector.get("intervalo_coleta", INTERVALO_COLETA_PADRAO)),
    )


def _toml_valor(valor) -> str:
    if isinstance(valor, bool):
        return "true" if valor else "false"
    if isinstance(valor, (int, float)):
        return repr(valor)
    if isinstance(valor, str):
        # String JSON e uma basic string TOML valida (mesmos escapes).
        return json.dumps(valor, ensure_ascii=False)
    if isinstance(valor, (list, tuple)):
        return "[" + ", ".join(_toml_valor(v) for v in valor) + "]"
    raise TypeError(f"Tipo nao suportado no config: {type(valor).__name__}")


def gravar(dados: dict[str, dict], comentario: str = "") -> Path:
    """Grava o config.toml do jogador (secoes simples de chave = valor)."""
    caminho = caminho_config()
    caminho.parent.mkdir(parents=True, exist_ok=True)
    linhas = [f"# {l}" if l else "#" for l in comentario.splitlines()]
    for secao, valores in dados.items():
        if not valores:
            continue
        if linhas:
            linhas.append("")
        linhas.append(f"[{secao}]")
        linhas.extend(f"{chave} = {_toml_valor(v)}" for chave, v in valores.items())
    temporario = caminho.with_suffix(".tmp")
    temporario.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    if os.name == "posix":
        # Pode conter o client secret: so o dono le. No Windows a pasta do perfil ja e restrita ao usuario.
        temporario.chmod(0o600)
    temporario.replace(caminho)
    return caminho
