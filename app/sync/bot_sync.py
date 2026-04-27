from __future__ import annotations

from datetime import datetime, timezone

from app.core.config_models import BotRegistryItem, BotsRegistry
from app.core.constants import BOTS_REGISTRY_FILE
from app.core.http_client import HttpClient
from app.core.json_store import load_model, save_model
from app.sync.bot_installer import install_or_update_bot


SYNC_PATH = "/api/v1/worker/sync"


def load_registry() -> BotsRegistry:
    data = load_model(BOTS_REGISTRY_FILE, BotsRegistry)
    if not data:
        return BotsRegistry(bots=[])
    return data


def save_registry(registry: BotsRegistry) -> None:
    save_model(BOTS_REGISTRY_FILE, registry)


def find_local_bot(registry: BotsRegistry, bot_id: str) -> BotRegistryItem | None:
    for bot in registry.bots:
        if bot.bot_id == bot_id:
            return bot
    return None


def sync_bots(client: HttpClient, runner_data) -> dict:
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
        current.last_sync_at = datetime.now(timezone.utc)

        needs_install = _needs_install(current)

        if needs_install:
            try:
                had_local_install = bool(current.local_path and current.venv_path)

                result = install_or_update_bot(current)

                current.local_path = result.local_path
                current.venv_path = result.venv_path
                current.installed_version = current.expected_version
                current.installed_commit = result.installed_commit
                current.requirements_hash = result.requirements_hash
                current.last_install_status = "ok"
                current.last_install_message = result.message

                if had_local_install:
                    updated_count += 1
                else:
                    installed_count += 1

            except Exception as exc:
                current.last_install_status = "error"
                current.last_install_message = str(exc)
                failed_count += 1
        else:
            current.last_install_status = "ok"
            current.last_install_message = "Bot alinhado com a versão esperada."

        updated_items.append(current)

    for local_bot in registry.bots:
        if not any(bot.bot_id == local_bot.bot_id for bot in updated_items):
            local_bot.linked = False
            local_bot.last_sync_at = datetime.now(timezone.utc)
            local_bot.last_install_message = "Bot não retornou no sync atual."
            updated_items.append(local_bot)

    new_registry = BotsRegistry(bots=updated_items)
    save_registry(new_registry)

    runner_data.config.polling_interval = response.get(
        "polling_interval",
        runner_data.config.polling_interval,
    )
    runner_data.config.max_concurrency = response.get(
        "max_concurrency",
        runner_data.config.max_concurrency,
    )

    return {
        "total": len(updated_items),
        "linked": len([b for b in updated_items if b.linked]),
        "installed": installed_count,
        "updated": updated_count,
        "failed": failed_count,
        "polling_interval": response.get("polling_interval"),
        "max_concurrency": response.get("max_concurrency"),
    }


def _needs_install(bot: BotRegistryItem) -> bool:
    if not bot.local_path or not bot.venv_path:
        return True

    if bot.installed_version != bot.expected_version:
        return True

    if bot.expected_commit and bot.installed_commit != bot.expected_commit:
        return True

    if bot.last_install_status in ("error", "not_installed", "outdated"):
        return True

    return False
