# Chiplog

## Стримы разработки

Разработка Chiplog идёт в прямом эфире каждый четверг в 19:00 по времени Алматы:

- [Актуальные стримы на Twitch](https://www.twitch.tv/uthunderbird)
- [Архив стримов на YouTube](https://www.youtube.com/@udthunderbird)
## Локальный запуск

Авторизация модельного транспорта через Codex OAuth:

Нужен запущенный PostgreSQL. Задайте операторское подключение к одному инстансу;
его роль должна уметь создавать базы. Для каждого пользователя приложение
создаёт отдельную database по фиксированному отображению идентичности:

```sh
export CHIPLOG_POSTGRES_DSN='host=localhost dbname=postgres'
```

Пароль храните в защищённом PostgreSQL service/passfile либо локальной
конфигурации, без добавления в репозиторий.

```sh
uv run chiplog auth login
uv run chiplog auth status
uv run chiplog auth logout
```

Вход открывает браузер; OAuth-сессия сохраняется в `~/.chiplog/codex-oauth.json`
с правами `0600`. Каталог задаётся через `CHIPLOG_HOME`. Личная сессия Codex
не импортируется. Refresh выполняется перед запросом при истечении токена.
По умолчанию используется `gpt-5.6-terra`, effort `low`; их можно изменить
через `native-chat --model ... --effort ...`. Доступность модели зависит от аккаунта;
автоматической подмены модели нет.

Транспорт использует backend Codex, по примеру локального проекта `operator`
(`providers/codex.py`) и `oauth-cli-kit==0.1.3`. Это не публичный Responses API
с API-ключом. Контракт и проверяемые границы: [CODEX-CLI-AGENT.md](design-docs/CODEX-CLI-AGENT.md).

Справка по CLI:

```sh
uv run chiplog --help
uv run chiplog bootstrap --help
uv run chiplog create --help
uv run chiplog show --help
```

Операции требуют `CHIPLOG_R6_OPERATOR_SECRET` и параметров идентичности.
CLI использует PostgreSQL и deployment gate: успешный `bootstrap` сам по себе не разрешает `create`.
Без соответствующего deployment entitlement операция возвращает `HOLD`.

## Native CLI

`native-chat` использует аутентифицированный durable inbox, общий native driver
и доставку в приватный inbox текущего UID. OAuth разрешает обращение к модели, но не заменяет
tenant/principal/credential/session и не выдаёт deployment entitlement.

Для нового пользователя сначала выполните `bootstrap` с нужной идентичностью.
Создайте приватный каталог состояния, например `mkdir -m 700 ~/.chiplog/native`.
Оператор отдельно предоставляет актуальный подписанный `CurrentEntitlement`
для `delivery.first_send`: JSON, Ed25519-подпись (64 raw bytes) и доверенный
публичный ключ (32 raw bytes). Команда не создаёт READY и не заменяет уже
выбранный другой entitlement.

```sh
uv run chiplog native-chat \
  --tenant me --principal me --credential local --session shell \
  --request-id message-001 --message 'Помоги продумать план на неделю' \
  --state-dir ~/.chiplog/native \
  --entitlement /path/to/grant.json \
  --entitlement-proof /path/to/grant.sig \
  --verification-key /path/to/operator-public.key
```

Нужны `CHIPLOG_POSTGRES_DSN`, `CHIPLOG_R6_OPERATOR_SECRET` и, для провайдера
Codex по умолчанию, сохранённая OAuth-сессия. `--model` и `--effort` задают модель.
Повторите тот же request-id с теми же сообщением, каталогом и настройками,
чтобы прочитать уже принятый ответ без нового model/SEND. Изменённые входные
байты под тем же ID отклоняются. Перед выводом stdout команда подтверждает
точные принятые байты в локальном inbox; stdout не считается durable receipt.
Запрос модели на consequential action возвращает HOLD до явного принятия через
native effects API, а не выполняется командой автоматически.

Для герметичного offline-прогона есть явный `--model-provider hermetic` и
повторяемый `--response-file` с точными model JSON bytes; автоматического fallback
с Codex нет. При replay завершённого Run файлы ответов не нужны.

## Программный native client

Новый default entrypoint для V2/V3 execution —
`from chiplog.client import open_execution_client`. Он открывает PostgreSQL-базу
пользователя через `CHIPLOG_POSTGRES_DSN` и текущую доверенную идентичность;
SQLite fallback и параметра выбора backend нет. Этот клиент монтирует model
custody/recovery, history/read-only successor, original effects, continuation,
CompleteAcceptance и durable delivery. SEND требует отдельно подписанного
актуального deployment entitlement и зарегистрированного transport.

До завершения текущего роудмапа проект не поддерживает legacy и обратную
совместимость ([AGENTS.md, §12](AGENTS.md#12-продуктовый-режим-без-legacy-до-завершения-роудмапа)).
Старые SQLite execution/dispatch factories, CommonCli/H1 kernel и chat importer
удалены. Текущие функциональные сценарии используют PostgreSQL native client.
Остальные общие компоненты сохраняются по их актуальным потребителям.

## Тесты

```sh
uv run pytest
sh .harness/scripts/test.sh
```

Focused PostgreSQL integration запускается на отдельном тестовом инстансе:

```sh
CHIPLOG_TEST_POSTGRES_DSN='host=localhost dbname=postgres' uv run pytest -q tests/platform/test_postgres.py tests/composition/test_postgres_execution.py
```

Fixture создаёт уникальные базы и удаляет только их. Без этой переменной
PostgreSQL integration помечается как skipped. Для подтверждения функциональных
сценариев PostgreSQL этот DSN должен быть задан явно.

Полный pytest выполняет каждый функциональный тест во внешнем наборе.
Агрегация stage0 проверяется управляемыми исходами срезов; небольшой реальный
subprocess smoke проверяет успешный и неуспешный exit status. Самостоятельный
stage0 profile по-прежнему запускает свои реальные срезы, но полный pytest
не запускает их повторно и не требует plugin делегирования или coverage receipts.

История из 1001 команды R6 подготавливается один раз для текущих исходников,
настроек и версий Python/SQLite. Каждый boundary-тест получает собственную копию
SQLite и связанных файлов; команда R7 №1002 и чтение выполняются заново.
Кеш расположен в системном временном каталоге; его повреждение или изменение
ключа приводит к повторной подготовке. Это кеш данных fixture, а не результатов тестов.

Для диагностики доступны исходные дорогие пути:

```sh
# Построить историю настоящими командами заново, без чтения кеша.
CHIPLOG_FRESH_BOUNDARY_SEED=1 uv run pytest
```

`CHIPLOG_BOUNDARY_SEED_CACHE` позволяет задать отдельный каталог кеша для эксперимента.
