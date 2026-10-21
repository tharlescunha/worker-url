"""
Limpeza de processos que sobram de uma task com foco (SAP, CIGAM, Citrix...).

Esses programas abrem janelas/processos que NÃO são filhos do robô; se o robô falha ou
é encerrado, eles ficam abertos (tela de login, popup de erro) e atrapalham a próxima
task. Como só uma task com foco roda por vez, é seguro encerrá-los entre uma task e outra.

As regras ficam em CONFIG_DIR/cleanup_rules.json (fora do git, então atualizações do
worker não sobrescrevem). O arquivo é criado com os valores padrão na primeira vez.
Cada regra tem:
  - name: nome da regra (aparece no log da task)
  - enabled: liga/desliga
  - processes: executáveis a encerrar; aceita curinga (ex.: "sap*" encerra saplogon.exe,
    sapgui.exe...). Veja os nomes com `tasklist`.
  - before_foreground: encerra esses processos ANTES de iniciar uma task com foco
  - on_foreground_failure: encerra depois de QUALQUER falha de task com foco
  - match_output_regex: também encerra depois de falha (de qualquer task) se a saída do robô
    casar com a expressão (sem diferenciar maiúsculas)
  - match_bot_names: idem, se o nome do bot contiver um destes trechos
Nunca encerra nada enquanto outra task com foco estiver em andamento.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass, field

from app.core.constants import CONFIG_DIR

CLEANUP_RULES_FILE = CONFIG_DIR / "cleanup_rules.json"

# Sobe quando os padrões mudam: arquivos mais antigos são salvos como .bak e recriados.
RULES_VERSION = 2

DEFAULT_RULES: list[dict] = [
    {
        "name": "SAP",
        "enabled": True,
        "processes": ["sap*"],
        "before_foreground": True,
        "on_foreground_failure": True,
        "match_output_regex": r"\bSAP\b",
        "match_bot_names": [],
    },
    {
        "name": "CIGAM",
        "enabled": True,
        "processes": ["cigam*"],
        "before_foreground": True,
        "on_foreground_failure": True,
        "match_output_regex": r"\bCIGAM\b",
        "match_bot_names": [],
    },
    {
        "name": "Citrix",
        "enabled": True,
        "processes": ["citrix*", "wfica32.exe"],
        "before_foreground": True,
        "on_foreground_failure": True,
        "match_output_regex": r"\bCitrix\b",
        "match_bot_names": [],
    },
]

_HELP = (
    "Edite este arquivo para ajustar a limpeza de processos entre tasks. 'processes' aceita "
    "curinga (sap*). before_foreground limpa antes de cada task com foco; on_foreground_failure "
    "limpa depois de qualquer falha de task com foco; match_output_regex/match_bot_names limpam "
    "depois de falha quando a saída ou o nome do bot casam. Nada é encerrado enquanto outra "
    "task com foco estiver rodando."
)

# Tasks com foco em andamento nesta máquina: a limpeza nunca derruba a tela de outra task.
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
    processes: list[str] = field(default_factory=list)
    before_foreground: bool = False
    on_foreground_failure: bool = False
    match_output_regex: str = ""
    match_bot_names: list[str] = field(default_factory=list)

    def matches_failure(self, bot_name: str, output_text: str, is_foreground: bool) -> bool:
        if not self.enabled or not self.processes:
            return False

        if is_foreground and self.on_foreground_failure:
            return True

        name = (bot_name or "").lower()
        if any(part.strip() and part.strip().lower() in name for part in self.match_bot_names):
            return True

        if self.match_output_regex and output_text:
            try:
                return re.search(self.match_output_regex, output_text, re.IGNORECASE) is not None
            except re.error:
                return False

        return False


def _write_default_file() -> None:
    CLEANUP_RULES_FILE.parent.mkdir(parents=True, exist_ok=True)
    CLEANUP_RULES_FILE.write_text(
        json.dumps(
            {"_ajuda": _HELP, "version": RULES_VERSION, "rules": DEFAULT_RULES},
            indent=4,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def load_rules(logger=None) -> list[CleanupRule]:
    """Lê as regras; cria o arquivo padrão (ou migra um antigo). Nunca levanta exceção."""
    items: list = DEFAULT_RULES

    try:
        if not CLEANUP_RULES_FILE.exists():
            _write_default_file()
        else:
            raw = json.loads(CLEANUP_RULES_FILE.read_text(encoding="utf-8"))
            version = raw.get("version", 0) if isinstance(raw, dict) else 0

            if version < RULES_VERSION:
                shutil.copy(CLEANUP_RULES_FILE, CLEANUP_RULES_FILE.with_suffix(".json.bak"))
                _write_default_file()
                if logger:
                    logger.warning(
                        "cleanup_rules.json atualizado para a versão %s (cópia da anterior em .bak).",
                        RULES_VERSION,
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
                processes=[str(v) for v in (item.get("processes") or []) if str(v).strip()],
                before_foreground=bool(item.get("before_foreground", False)),
                on_foreground_failure=bool(item.get("on_foreground_failure", False)),
                match_output_regex=str(item.get("match_output_regex") or ""),
                match_bot_names=[str(v) for v in (item.get("match_bot_names") or [])],
            )
        )
    return rules


def kill_image(image_name: str) -> bool:
    """Encerra os processos com esse nome (aceita curinga) e seus filhos. True se matou algum."""
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
    is_foreground: bool = False,
) -> list[str]:
    """Depois de uma task com falha, encerra os processos das regras que se aplicam."""
    if other_foreground_running(task_id):
        return []

    messages: list[str] = []

    for rule in load_rules(logger):
        if not rule.matches_failure(bot_name, output_text, is_foreground):
            continue

        killed = _kill_rule_processes(rule, logger)
        if killed:
            message = (
                f"Limpeza após falha (regra {rule.name}): encerrado(s) {', '.join(killed)}."
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
            message = f"Limpeza antes da task (regra {rule.name}): encerrado(s) {', '.join(killed)}."
            logger.warning("%s | task_id=%s", message, task_id)
            messages.append(message)

    return messages
