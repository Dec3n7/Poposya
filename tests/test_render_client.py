"""Клиент рендера карточек (src/infrastructure/render/browser.py).

Браузер вынесен в отдельный сервис renderer; здесь проверяем тонкий HTTP-клиент:
шлёт html/размеры/scale на POST /render и возвращает PNG-байты, а на ошибку
сервиса бросает (верхний ког показывает текстовый фолбэк). Поднимаем настоящий
aiohttp-сервер-заглушку renderer — подменять клиента фейком значит проверять фейк.
"""

from aiohttp import web

from src.infrastructure.render.browser import CardRenderer


async def _serve(handler):
    app = web.Application()
    app.router.add_post("/render", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, f"http://127.0.0.1:{port}"


async def test_render_posts_payload_and_returns_png():
    seen: dict = {}

    async def handler(request: web.Request) -> web.Response:
        seen.update(await request.json())
        return web.Response(body=b"\x89PNG\r\n\x1a\nDATA", content_type="image/png")

    runner, url = await _serve(handler)
    renderer = CardRenderer(url, scale=2)
    try:
        png = await renderer.render("<html>карточка</html>", 300, 150)
    finally:
        await renderer.close()
        await runner.cleanup()

    assert png.startswith(b"\x89PNG")
    assert seen == {"html": "<html>карточка</html>", "width": 300, "height": 150, "scale": 2}


async def test_render_raises_on_service_error():
    async def handler(_: web.Request) -> web.Response:
        return web.json_response({"error": "render failed"}, status=500)

    runner, url = await _serve(handler)
    renderer = CardRenderer(url)
    try:
        try:
            await renderer.render("<html/>", 100, 100)
            raised = False
        except RuntimeError:
            raised = True
    finally:
        await renderer.close()
        await runner.cleanup()
    assert raised  # сбой renderer поднимается наверх, а не отдаёт битый PNG


async def test_close_is_idempotent_and_reopens():
    async def handler(_: web.Request) -> web.Response:
        return web.Response(body=b"\x89PNG", content_type="image/png")

    runner, url = await _serve(handler)
    renderer = CardRenderer(url)
    try:
        await renderer.start()
        await renderer.close()
        await renderer.close()  # повторный close не падает
        # после close клиент сам переоткрывается на следующем render
        assert (await renderer.render("<html/>", 10, 10)).startswith(b"\x89PNG")
    finally:
        await renderer.close()
        await runner.cleanup()
