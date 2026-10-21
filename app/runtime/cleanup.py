"""
Limpeza de processos que sobram depois de uma task com falha.

Programas como o SAP abrem janelas/processos que NÃO são filhos do robô; se o robô
falha, eles ficam abertos (tela de login, popup de erro) e atrapalham a próxima task.

As regras ficam em CONFIG_DIR/cleanup_rules.json (fora do git, então atualizações do
worker não sobrescrevem). O arquivo é criado com valores padrão na primeira vez.
Cada regra tem:
  - name: nome da regra (aparece no log da task)
  - enabled: liga/desliga
  - match_output_regex: expressão (sem diferenciar maiúsculas) procurada na saída do robô
  - match_bot_names: trechos do nome do bot que também disparam a regra
  - processes: nomes dos executáveis a encerrar (ex.: "saplogon.exe")
  - before_foreground: se true, também encerra esses processos ANTES de iniciar uma task
    com foco (garante tela limpa; só roda se nenhuma outra task com foco estiver ativa)
"""

from __future__ import annotations

import json
import re
import subprocess
import threading
from dataclasses import dataclass, field

from app.core.constants import CONFIG_DIR

CLEANUP_RULES_FILE = CONFIG_DIR / "cleanup_rules.json"

DEFAULT_RULES: list[dict] = [
    {
        "name": "SAP",
        "enabled": True,
        "match_output_regex": r"\bSAP\b",
        "match_bot_names": [],
        "processes": ["saplogon.exe", "sapgui.exe", "NWBC.exe", "SAPBusinessClient.exe"],
        "before_foreground": False,
    },
    {
        "name": "SIGAM",
        "enabled": True,
        "match_output_regex": r"\bSIGAM\b",
        "match_bot_names": [],
        "processes": [],
        "before_foreground": False,
    },
]

_HELP = (
    "Edite este arquivo para ajustar a limpeza depois de falhas. 'processes' é a lista de "
    "executáveis a encerrar (veja os nomes no Gerenciador de Tarefas ou com 'tasklist'). "
    "'match_bot_names' dispara a regra pelo nome do bot, mesmo sem aparecer o texto na saída."
)

# Tasks com foco em andamento nesta máquina: a limpeza nunca derruba o SAP de outra task.
_foreground_lock = threading.Lock()
_running_foreground: set[int] = set()


def mark_foreground_started(task_id: int) -> None:
    with _foreground_lock:
        _running_foreground.add(task_id)


def mark_foreground_finished(task_id: int) -> None:
    with _foreground_lock:
        _running_foreground.discard(task_id)


def other_foreground_running(task_id: int) -> bool:
    with _foreground_lock:
        return any(other != task_id for other in _running_foreground)


@dataclass
class CleanupRule:
    name: str
    enabled: bool = True
    match_output_regex: str = ""
    match_bot_names: list[str] = field(default_factory=list)
    processes: list[str] = field(default_factory=list)
    before_foreground: bool = False

    def matches(self, bot_name: str, output_text: str) -> bool:
        if not self.enabled or not self.processes:
            return False

        name = (bot_name or "").lower()
        if any(part.strip() and part.strip().lower() in name for part in self.match_bot_names):
            return True

        if self.match_output_regex and output_text:
            try:
                return re.search(self.match_output_regex, output_text, re.IGNORECASE) is not None
            except re.error:
                return False

        return False


def load_rules(logger=None) -> list[CleanupRule]:
    """Lê as regras; cria o arquivo padrão se não existir. Nunca levanta exceção."""
    try:
        if not CLEANUP_RULES_FILE.exists():
            CLEANUP_RULES_FILE.parent.mkdir(parents=True, exist_ok=True)
            CLEANUP_RULES_FILE.write_text(
                json.dumps({"_ajuda": _HELP, "rules": DEFAULT_RULES}, indent=4, ensure_ascii=False),
                encoding="utf-8",
            )
        raw = json.loads(CLEANUP_RULES_FILE.read_text(encoding="utf-8"))
        items = raw.get("rules", []) if isinstance(raw, dict) else raw
    except Exception as exc:
        if logger:
            logger.warning("cleanup_rules.json inválido (%s); usando regras padrão.", exc)
        items = DEFAULT_RULES

    rules: list[CleanupRule] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        rules.append(
            CleanupRule(
                name=str(item.get("name") or "regra"),
                enabled=bool(item.get("enabled", True)),
                match_output_regex=str(item.get("match_output_regex") or ""),
                match_bot_names=[str(v) for v in (item.get("match_bot_names") or [])],
                processes=[str(v) for v in (item.get("processes") or []) if str(v).strip()],
                before_foreground=bool(item.get("before_foreground", False)),
            )
        )
    return rules


def kill_image(image_name: str) -> bool:
    """Encerra todos os processos com esse nome (e filhos). True se matou algum."""
    result = subprocess.run(
        ["taskkill", "/F", "/T", "/IM", image_name],
        capture_output=True,
        text=True,
        shell=False,
        check=False,
    )
    return result.returncode == 0


def _kill_rule_processes(rule: CleanupRule, logger) -> list[str]:
    killed: list[str] = []
    for image in rule.processes:
        try:
            if kill_image(image):
                killed.append(image)
        except Exception as exc:
            logger.warning("Falha ao encerrar %s (regra %s): %s", image, rule.name, exc)
    return killed


def cleanup_after_failure(
    *,
    task_id: int,
    bot_name: str,
    output_text: str,
    logger,
) -> list[str]:
    """
    Depois de uma task com falha, encerra os processos das regras que casam com a saída
    ou com o nome do bot. Não faz nada se outra task com foco estiver rodando.
    Devolve mensagens descrevendo o que foi encerrado.
    """
    if other_foreground_running(task_id):
        return []

    messages: list[str] = []

    for rule in load_rules(logger):
        if not rule.matches(bot_name, output_text):
            continue

        killed = _kill_rule_processes(rule, logger)
        if killed:
            message = (
                f"Limpeza após falha (regra {rule.name}): processos encerrados: {', '.join(killed)}."
            )
            logger.warning("%s | task_id=%s", message, task_id)
            messages.append(message)

    return messages


def cleanup_before_foreground(*, task_id: int, logger) -> list[str]:
    """Antes de uma task com foco: encerra restos das regras com before_foreground=true."""
    if other_foreground_running(task_id):
        return []

    messages: list[str] = []

    for rule in load_rules(logger):
        if not (rule.enabled and rule.before_foreground and rule.processes):
            continue

        killed = _kill_rule_processes(rule, logger)
        if killed:
            message = f"Limpeza antes da task (regra {rule.name}): processos encerrados: {', '.join(killed)}."
            logger.warning("%s | task_id=%s", message, task_id)
            messages.append(message)

    return messages
