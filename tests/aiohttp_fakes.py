"""Лёгкие заглушки aiohttp для тестов провайдеров — без сети.

FakeSession имитирует async-контекстный ClientSession с методами get/post,
возвращающими FakeResponse (тоже async-контекст). capture копит параметры
последнего запроса, чтобы проверять url/params/headers/payload."""


class FakeResponse:
    def __init__(self, status=200, json_data=None, text_data="", headers=None):
        self.status = status
        self._json = json_data
        self._text = text_data
        self.headers = headers or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self):
        return self._json

    async def text(self):
        return self._text


class FakeSession:
    def __init__(self, response=None, exc=None, capture=None):
        self._response = response if response is not None else FakeResponse()
        self._exc = exc
        self.capture = capture if capture is not None else {}
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def get(self, url, params=None, headers=None):
        self.capture.update(method="GET", url=url, params=params, headers=headers)
        if self._exc:
            raise self._exc
        return self._response

    def post(self, url, json=None, data=None, headers=None):
        self.capture.update(method="POST", url=url, json=json, data=data, headers=headers)
        if self._exc:
            raise self._exc
        return self._response

    async def close(self):
        self.closed = True


def patch_session(monkeypatch, module_path, *, response=None, exc=None, capture=None):
    """Подменяет aiohttp.ClientSession в указанном модуле фабрикой FakeSession.
    module_path, напр., 'src.infrastructure.cinema.tmdb'."""
    session = FakeSession(response=response, exc=exc, capture=capture)
    monkeypatch.setattr(f"{module_path}.aiohttp.ClientSession", lambda **kw: session)
    return session
