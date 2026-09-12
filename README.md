# Chiplog

## Стримы разработки

Разработка Chiplog идёт в прямом эфире каждый четверг в 19:00 по времени Алматы:

- [Актуальные стримы на Twitch](https://www.twitch.tv/uthunderbird)
- [Архив стримов на YouTube](https://www.youtube.com/@udthunderbird)
## Локальный запуск

Справка по CLI:

```sh
uv run chiplog --help
uv run chiplog bootstrap --help
uv run chiplog create --help
uv run chiplog show --help
```

Операции требуют `CHIPLOG_R6_OPERATOR_SECRET` и параметров идентичности.
CLI использует гейт R8: успешный `bootstrap` сам по себе не разрешает `create`.
Без соответствующего deployment entitlement операция возвращает `HOLD`.
