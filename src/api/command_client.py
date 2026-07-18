"""Панельная сторона командного моста: положить команду и подождать результат.

Панель кладёт команду в bot_commands (+ NOTIFY на Postgres), бот исполняет в
Discord и пишет результат обратно. Здесь — короткое ожидание результата, чтобы
вернуть его во фронт одним запросом; по таймауту отдаём status=pending
(«отправлено, применяется»).
"""

from src.api.container import ApiContainer
from src.infrastructure.commands.bridge import enqueue_command, wait_for_result


async def run_command(
    container: ApiContainer,
    guild_id: int,
    command_type: str,
    payload: dict,
    requested_by: int,
) -> dict:
    cmd_id = await enqueue_command(
        container.session_factory, guild_id, command_type, payload, requested_by
    )
    status, result = await wait_for_result(
        container.session_factory, cmd_id, timeout=container.settings.web_command_wait_seconds
    )
    return {"id": cmd_id, "status": status, "result": result}
