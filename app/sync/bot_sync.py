from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Callable

from app.core.config_models import BotRegistryItem, BotsRegistry
from app.core.constants import BOTS_REGISTRY_FILE
from app.core.http_client import HttpClient
from app.core.json_store import load_model, save_model
from app.runtime.bot_usage import REGISTRY_LOCK, bot_usage
from app.sync.bot_installer import (
    install_or_update_bot,
    install_retry_allowed,
    needs_install,
    register_install_failure,
    register_install_success,
)


SYNC_PATH = "/api/v1/worker/sync"

# Só um sync por vez (thread periódica e sync sob demanda de uma task não se atropelam).
SYNC_LOCK = threading.RLock()
ProgressCallback = Callable[[str], None]
FailureCallback = Callable[[BotRegistryItem, str], None]


def load_registry() -> BotsRegistry:
    with REGISTRY_LOCK:
        data = load_model(BOTS_REGISTRY_FILE, BotsRegistry)
    if not data:
        return BotsRegistry(bots=[])
    return data


def save_registry(registry: BotsRegistry) -> None:
    with REGISTRY_LOCK:
        save_model(BOTS_REGISTRY_FILE, registry)


def find_local_bot(registry: BotsRegistry, bot_id: str) -> BotRegistryItem | None:
    for bot in registry.bots:
        if bot.bot_id == bot_id:
            return bot
    return None


def sync_bots(
    client: HttpClient,
    runner_data,
    progress_callback: ProgressCallback | None = None,
    failure_callback: FailureCallback | None = None,
    install: bool = True,
) -> dict:
    """
    Sincroniza os bots vinculados ao runner com o registry local.

    Com install=False só atualiza os dados (rápido, uma chamada à API): a task que
    precisar de um bot ainda não instalado prepara apenas aquele bot.
    """
    with SYNC_LOCK:
        return _sync_bots_locked(client, runner_data, progress_callback, failure_callback, install)


def _sync_bots_locked(
    client: HttpClient,
    runner_data,
    progress_callback: ProgressCallback | None,
    failure_callback: FailureCallback | None,
    install: bool,
) -> dict:
    payload = {
        "uuid": runner_data.uuid,
        "token": runner_data.runner_token,
        "host_name": runner_data.host_name,
        "ip": runner_data.ip,
    }

    response = client.post(SYNC_PATH, payload)

    remote_bots = response.get("bots", [])
    registry = load_registry()
    updated_items: list[BotRegistryItem] = []

    installed_count = 0
    updated_count = 0
    failed_count = 0
    deferred_count = 0

    for bot_data in remote_bots:
        bot_id = str(bot_data.get("bot_id") or bot_data.get("id") or "")
        if not bot_id:
            continue

        local = find_local_bot(registry, bot_id)

        if local:
            current = local
            current.linked = True
            current.name = bot_data.get("name", current.name)
        else:
            current = BotRegistryItem(
                bot_id=bot_id,
                name=bot_data.get("name", ""),
                linked=True,
            )

        current.bot_version_id = bot_data.get("bot_version_id")
        current.technology = bot_data.get("technology")
        current.source_type = bot_data.get("source_type")
        current.repository_url = bot_data.get("repository_url") or bot_data.get("source_url")
        current.artifact_path = bot_data.get("artifact_path")
        current.branch = bot_data.get("branch")
        current.entrypoint = bot_data.get("entrypoint")
        current.requirements_file = bot_data.get("requirements_file")
        current.timeout_default = bot_data.get("timeout_default")
        current.checksum = bot_data.get("checksum")
        current.expected_version = bot_data.get("version")
        current.expected_commit = bot_data.get("commit_hash")
        current.execution_mode = bot_data.get("execution_mode") or current.execution_mode or "background"
        current.auto_update = bool(bot_data.get("auto_update", False))
        current.last_sync_at = datetime.now(timezone.utc)

        if needs_install(current):
            if not install:
                # Só registra os dados do bot; a instalação fica para depois (ou para a task).
                updated_items.append(current)
                continue

            now = datetime.now(timezone.utc)

            if not install_retry_allowed(current, now):
                # Falhou há pouco: espera o intervalo de backoff antes de tentar de novo.
                updated_items.append(current)
                continue

            exclusive = bot_usage.try_exclusive(current.bot_id)

            if exclusive is None:
                # Bot em execução agora: não mexe nos arquivos dele, tenta no próximo ciclo.
                deferred_count += 1
                updated_items.append(current)
                continue

            try:
                with exclusive:
                    had_local_install = bool(current.local_path and current.venv_path)

                    _notify(
                        progress_callback,
                        f"Bot {current.name or current.bot_id} precisa ser preparado pelo worker.",
                    )

                    result = install_or_update_bot(
                        current,
                        progress_callback=progress_callback,
                    )

                current.local_path = result.local_path
                current.venv_path = result.venv_path
                current.installed_version = current.expected_version
                current.installed_commit = result.installed_commit
                current.requirements_hash = result.requirements_hash
                current.last_install_status = "ok"
                current.last_install_message = result.message
                register_install_success(current)

                if had_local_install:
                    updated_count += 1
                else:
                    installed_count += 1

            except Exception as exc:
                current.last_install_status = "error"
                current.last_install_message = str(exc)
                register_install_failure(current, datetime.now(timezone.utc))
                _notify(
                    progress_callback,
                    f"Falha ao preparar bot {current.name or current.bot_id}: {exc} "
                    f"(nova tentativa após {current.next_install_retry_at})",
                )
                failed_count += 1

                if failure_callback:
                    failure_callback(current, str(exc))
        else:
            current.last_install_status = "ok"
            current.last_install_message = "Bot alinhado com a versão esperada."
            register_install_success(current)

        updated_items.append(current)

    for local_bot in registry.bots:
        if not any(bot.bot_id == local_bot.bot_id for bot in updated_items):
            local_bot.linked = False
            local_bot.last_sync_at = datetime.now(timezone.utc)
            local_bot.last_install_message = "Bot não retornou no sync atual."
            updated_items.append(local_bot)

    _merge_and_save(updated_items)

    runner_data.config.polling_interval = response.get(
        "polling_interval",
        runner_data.config.polling_interval,
    )
    runner_data.config.max_concurrency = response.get(
        "max_concurrency",
        runner_data.config.max_concurrency,
    )

    if installed_count or updated_count or failed_count or deferred_count:
        _notify(
            progress_callback,
            (
                "Sync de bots concluido pelo worker: "
                f"instalados={installed_count} atualizados={updated_count} "
                f"falhas={failed_count} adiados_por_uso={deferred_count}"
            ),
        )

    return {
        "total": len(updated_items),
        "linked": len([b for b in updated_items if b.linked]),
        "installed": installed_count,
        "updated": updated_count,
        "failed": failed_count,
        "deferred": deferred_count,
        "polling_interval": response.get("polling_interval"),
        "max_concurrency": response.get("max_concurrency"),
    }


def _merge_and_save(updated_items: list[BotRegistryItem]) -> None:
    """
    Grava o registry sem sobrescrever o que tasks em execução alteraram durante
    o sync (instalações feitas pela própria task, commit instalado etc.).
    """
    with REGISTRY_LOCK:
        fresh = load_model(BOTS_REGISTRY_FILE, BotsRegistry) or BotsRegistry(bots=[])
        fresh_by_id = {bot.bot_id: bot for bot in fresh.bots}

        merged: list[BotRegistryItem] = []

        for item in updated_items:
            current = fresh_by_id.get(item.bot_id)

            # Se uma task preparou este bot enquanto o sync rodava, mantém os
            # dados de instalação mais novos dela.
            if (
                current is not None
                and current.last_install_status == "ok"
                and current.installed_commit
                and item.last_install_status != "ok"
                and not item.installed_commit
            ):
                item.local_path = current.local_path
                item.venv_path = current.venv_path
                item.installed_version = current.installed_version
                item.installed_commit = current.installed_commit
                item.requirements_hash = current.requirements_hash
                item.last_install_status = current.last_install_status
                item.last_install_message = current.last_install_message
                item.install_failures = 0
                item.next_install_retry_at = None

            merged.append(item)

        save_model(BOTS_REGISTRY_FILE, BotsRegistry(bots=merged))


def _notify(progress_callback: ProgressCallback | None, message: str) -> None:
    if progress_callback:
        progress_callback(message)
