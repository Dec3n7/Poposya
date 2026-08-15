# ADR-013 — Доступ WARDEN к Docker — через фильтрующий socket-proxy

- **Статус:** Accepted (2026-08)
- **Связано:** [ADR-004](ADR-004-warden-independent.md), первоисточник — [Technical_Audit_v2 §5, §49](../plans/Poposya_WARDEN_Technical_Audit_v2.md), код — `WARDEN/docker-compose.yml`

## Контекст

Чтобы перезапускать зависшие контейнеры, WARDEN нужен Docker API. Прямой монтаж
`/var/run/docker.sock` даёт доступ ко всему демону — фактически root на хосте:
компрометация WARDEN превращается в компрометацию хоста. Промежуточный `tecnativa/
docker-socket-proxy` с `CONTAINERS=1 POST=1` лучше, но `POST=1` — это **все** POST
по секции containers (`create/start/stop/kill/update/rename/exec`), а не whitelist
одного `restart`. Control surface оставался слишком широким.

## Решение

Доступ к Docker — только через **фильтрующий прокси с path-level allowlist на
уровне regex** (`wollomatic/socket-proxy`, образ закреплён по digest). WARDEN не
монтирует сокет сам, ходит на `tcp://docker-socket-proxy:2375` в отдельной сети
`internal: true`. Разрешено буквально:

```text
GET  /version
GET  /containers/<name>/json      # inspect
GET  /containers/<name>/stats     # память цели
POST /containers/<name>/restart   # единственное действие
```

Всё остальное (`create/start/stop/kill/exec/update/…`) режется regex **до** сокета.

## Последствия

- **Плюсы:** даже при RCE в WARDEN Docker-поверхность ограничена ровно
  `restart` конкретного контейнера — «host compromise» через прокси закрыт;
  сеть прокси изолирована.
- **Минусы / цена:** ещё один сервис в compose; allowlist-regex надо
  поддерживать при изменении набора нужных вызовов.
- **Риски / что осталось открытым:** привязка к именам контейнеров (см.
  [ADR-004](ADR-004-warden-independent.md) — переход на Docker labels); токены
  control-plane стоит сверять `hmac.compare_digest` (defence-in-depth).
