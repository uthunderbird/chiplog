# Аудит избыточной сложности

Исследовательская ветка: `research/overengineering-audit`.
Исходный код: `5c18b0782e75ae4a7ba39c8be22ee29c038ef01b`.
Результат — первый структурный обзор всей кодовой базы с углублённой проверкой
выбранных механизмов. На момент исходного аудита реализация, тесты, правила и
каталог источников не менялись. Последующие изменения отмечены ниже.

## Итог очистки, 2026-10-21

Все подтверждённые кандидаты аудита обработаны; функционально нужные компоненты
сохранены по конкретным текущим потребителям. Код проверки: `10dee4c`.
Полный гейт выполнен на `2f98e48`; между этими commits source/tests не менялись.

| Пункт | Результат |
|---|---|
| OE-01 | Begin/seal используют snapshot подготовки; locked publication recheck сохранён |
| OE-02 | Отменённые H1 inventory/enrollment/custody/read-plan семьи удалены |
| OE-03 | Не смонтированная terminal-fault диагностика и её fixture-графы удалены |
| OE-04 | SQLite AST/SQL source-proof engine и исключительные тесты удалены |
| OE-05 | Неиспользуемый common-driver API удалён |
| OE-06 | Неиспользуемые operation/runtime registry DSL и custody socket удалены |
| OE-07 | Ненужные scheduler startup/service-proof листья удалены |
| OE-08 | Исторический grant API, direct-wire fallback и journal fallback удалены |
| OE-09 | Исторический API freeze/extensions удалён; import/ownership границы сохранены |
| OE-10 | Полезный planning-adoption сценарий сохранён как явно обозначенная offline simulation |
| OE-11 | Полный pytest исполняет оставшиеся cases без вложенного stage0/dedup/receipts |
| Дополнительно | Архив genesis заменён текущим bootstrap; planning aliases и overflow execution compatibility удалены |

Сохранены реальные Run V2/V3, текущий V1 scheduler producer, owner isolation,
auth/CAS/integrity, model/run/effect recovery, no-repeat SEND и общие workspace
контракты. У них есть действующие функциональные потребители. Строгий source
catalog остаётся явным требованием AGENTS §7. Неподтверждённые гипотезы ниже
не выдаются за доказанную ненужность и не создают новых обязательств инфраструктуры.

Полная проверка:

```sh
CHIPLOG_TEST_POSTGRES_DSN='host=127.0.0.1 port=55439 user=postgres dbname=postgres' \
UV_CACHE_DIR=/private/tmp/chiplog-uv-cache \
CHIPLOG_COMMIT_MODE=full sh .harness/scripts/gate.sh
```

Exit 0, **3948 passed in 1192.03s**, гейт открыт. Это весь собранный pytest-набор,
включая PostgreSQL race/recovery сценарии, без delegated deselection. Самотесты
харнесса, строгий каталог, fast verification, Ruff/format и mypy также прошли.
Лог: `/private/tmp/chiplog-overengineering-full-gate.log`.

Один холодный swarm-red-team всего source diff `5c18b07..10dee4c`:
**0 подтверждённых P0/P1**, с явными пределами same-model/source review.
Root отдельно закрыл отсутствовавший у reviewer PG DSN полным прогоном выше.
Артефакт: `/private/tmp/chiplog-overengineering-whole-cold-review.md`.
Обязательная ретро завершена в `2f98e48`, без нового правила или инструмента.
Ветка локальная; merge/push этой исследовательской ветки не выполнялись.

## Исходный вывод (до очистки)

Главные кандидаты на упрощение — оставшиеся системы для уже отменённых задач:
H1-инвентаризация, SQLite source-proof, отдельная terminal-fault диагностика,
исторические протоколы и дублирующая orchestration. Их тесты продолжают делать
старые контракты обязательными, хотя продуктовый scope их отменил.

Есть и конкретная избыточность в текущем PostgreSQL runtime: два метода повторно
получают полный snapshot, имея snapshot подготовки. Это небольшой первый срез,
который позволяет упростить живой путь без новой архитектуры.

Тестовая инфраструктура тоже входит в аудит. Вложенный stage0 требует протокола
делегирования и подтверждения покрытия, чтобы справиться с повторным исполнением,
созданным самой оркестрацией. Убирать receipt-проверки отдельно нельзя; можно
упростить способ запуска, сохранив полную функциональную регрессию.

Приоритет ниже означает порядок исследования/упрощения, а не тяжесть бага.
Не измерены ускорение, экономия памяти или безопасность будущего удаления.

## Критерий

Основание — [AGENTS.md §11–12](../AGENTS.md),
[текущий R14 scope](R14-FUNCTIONAL-SCOPE.md) и
[функциональные исходы R15–R17](R15-R17-FUNCTIONAL-PLAN.md).

Для находки нужны существующий механизм, его потребители, нынешнее требование,
цена сложности и меньший способ получить тот же функциональный исход.
Размер файла, старое имя, номер версии и отсутствие импорта из native-chat
по отдельности этого не доказывают. Тест или проверка каталога — потребитель
кода, но не самостоятельное обоснование продуктовой функции.

Сохраняются: авторизация и tenant scope, целостность авторитетных данных,
атомарность, CAS/отказ устаревшему writer, обычное восстановление после сбоя,
долговечные retry/lineage и запрет повторного SEND при неопределённом исходе.
Общие Planning, R13, calendar и workspace функции нельзя удалить только потому,
что текущий CLI использует PostgreSQL.

## Охват и пределы

Реестр содержит 921 tracked-файл и 214 822 физических строки: Python, shell,
SQL, TOML, JSON/YAML, hooks и `.gitignore`, включая конфигурацию и тестовые данные.
Это не число строк бизнес-логики. Все файлы распределены между восемью областями;
сверка множеств не нашла пропусков или пересечений.

| Область | Файлы | Строки | Точечный углублённый разбор |
|---|---:|---:|---:|
| Composition и публичные входы | 83 | 24 249 | 16 |
| Agent-loop contracts и домен | 107 | 28 826 | 18 |
| Остальные capabilities и primitives | 108 | 19 288 | 19 |
| Platform и adapters | 73 | 20 451 | 24 |
| Verification, architecture, inert shared | 51 | 16 329 | 20 |
| Capability tests и support | 205 | 46 819 | 8 |
| Composition/platform/CLI tests | 170 | 37 617 | 15 |
| Tooling, hooks, остальные tests/config | 124 | 21 243 | 28 |
| Всего | 921 | 214 822 | 148 |

Структурный просмотр: определения, импорты, ответственность файла и поиск
потребителей. Углублённый: тела выбранных механизмов, границы и прямые потребители;
это не утверждение о чтении каждой строки 148 файлов. Дополнительный проход
живого runtime и холодная проверка отдельно рассмотрели ключевые пути.
Compressed fixtures и lockfile не анализировались как исходный код; документация
читалась для определения актуальных требований, не как полный архив истории.
`.env.example` дополнительно просмотрен как пример конфигурации.

Воспроизводимый размер реестра на исходном коммите:

```sh
git ls-files -z | python3 -c '
import sys
from pathlib import Path
suffixes = {".py", ".sh", ".sql", ".toml", ".json", ".yml", ".yaml"}
names = [n for n in sys.stdin.buffer.read().decode().split("\0") if n]
files = [Path(n) for n in names if Path(n).suffix in suffixes
         or n.startswith(".githooks/") or n == ".gitignore"]
print(len(files), sum(len(p.read_text().splitlines()) for p in files))
'
```

Отдельно проверены исключённые расширения и shebang: неназначенных JS/TS/Rust/C/Go
или исполняемых скриптов не найдено. Числа углублённого разбора — учёт чтения
исполнителей, не независимое доказательство полноты.

## Подтверждённые кандидаты

### OE-01. Повторное чтение snapshot в живом runtime

**Первый небольшой срез.**
[begin_execution](../src/chiplog/composition/postgres_execution.py#L434)
получает snapshot в строке 436, затем вызывает `execution_record` в строке 441.
[seal_execution](../src/chiplog/composition/postgres_execution.py#L574)
повторяет это в строках 576/581.
[execution_record](../src/chiplog/composition/postgres_execution.py#L209)
идёт через `snapshot()` и заново открывает аутентифицированную транзакцию,
читает публикации и декодирует Run ancestry. Это полный read, не точечный lookup.

Меньший вариант: выбрать Run из уже полученного snapshot через `_runs(snapshot)`,
сохранив absent/stale ошибки. Публичный reader остаётся для самостоятельных вызовов.
Не нужен кеш между командами, новый backend или универсальный selector-framework.

Контраргумент — второй read раньше заметит отзыв прав или смену worker.
Но [publish_execution_batch](../src/chiplog/composition/postgres_execution.py#L242)
снова аутентифицирует actor, строит текущий snapshot в locked transaction и
проверяет исходный tenant head до записи. `_actor` требует `peer == self.peer`.
Replay уже возвращается после первого snapshot, не выполняя второй read.
Точка диагностического отказа при гонке может измениться; нужный отказ перед
публикацией должен сохраниться.

Условия среза: реальные begin/seal/replay/reopen из
[test_postgres_execution.py](../tests/composition/test_postgres_execution.py),
отказ при revocation/stale worker и отсутствие записей при смене head перед commit.
Не заменять это тестом, который только считает вызовы snapshot.
Не распространять reuse на post-model capture, SEND и другие границы внешнего действия.

### OE-02. Оставшаяся H1 inventory/enrollment инфраструктура

Удалённые `h1_inventory_{call,baseline,completion,workspace_planning,leaf_contracts}`
образовывали отдельную грамматику scope/record edges. Импорты найденной семьи
остаются внутри неё и в тестах.
Удалённые `h1_launch_enrollment`, `h1_registration_custody`
и `h1_completion_readplan_registry` сохраняли enrollment, marker/lock/resource
lifecycle и исторические планы чтения.
Текущий native PG путь их не монтирует; scope прямо отменяет H1 whole-runtime closure.

Меньший вариант: удалить замкнутые семьи вместе с исключительными тестами/support,
после проверки точных source и dynamic-manifest ссылок. Не создавать общий
inventory registry взамен. Shared workspace policy и DTO с живыми потребителями
сохраняются. Контрпримером будет конкретный нынешний launcher, вызывающий эти factory.

Сохранить native two-seal/reopen, completion current-source и ingress corruption
сценарии. H1-префикс сам по себе не даёт права удалять соседние файлы.

### OE-03. Не смонтированная terminal-fault диагностика

Удалённые `recovery_diagnostic_contracts`, `terminal_recovery_fault_contracts`
и `terminal_fault_observer_sources` задавали capture inventories, observer registry,
issuance, provenance и fresh/historical
joins. Для верхнего diagnostic module не найден source-потребитель; найденные
нижние связи замыкаются на этот протокол и его tests/support.
Отдельная durable terminal-fault recipe явно отложена в B-R14-A08.

Меньший вариант: убрать эту отдельную функцию и её исключительные fixture-графы,
пока клиенту не нужен terminal fault, отличный от HOLD/abort. Обычные typed integrity
errors, HOLD, abort и исходные доказательства внешнего действия сохраняются.
Новая logging-инфраструктура для такого удаления не нужна.

Контрпример — действующий операторский/client flow, который производит и потребляет
эти записи. Его не установили. Сохранить реальные model/run/effect recovery
сценарии, а не превращать отказ чтения в частичный успешный результат.

### OE-04. SQLite AST/SQL source-proof после отмены задачи

Удалённые `planning_descriptor_chain`, `protected_anchor_chain`,
`authority_descriptor_closure`
и supporting SQL-family/engine-evidence анализаторы проверяли исторический SQLite
reader/anchor маршрут. Встречаются собственная модель части Python, точные AST
выражения, SQL expansion и фиксированные стадии proof. Потребители верхнего API
найдены в verification/tests, не в текущем native admission.

Удалённые `test_planning_descriptor_chain` и `test_authority_descriptor_closure`
пинили старые source paths, подстроки реализации и цепочку стадий. Это создавало
синхронную правку runtime → recognizer → текстовые mutants → каталог для задачи,
которую текущий R14 scope отменил. Полнота global admission этими helper не заявляется.

Меньший вариант: убрать feature и исключительно обслуживающие её проверки,
сохранив любой helper с отдельным актуальным потребителем. Не переносить
SQLite proof-engine на PostgreSQL. Контрпример — текущий admission consumer,
которому этот fragment действительно нужен; не просто другой verifier или gate.
Native corrupt-read, auth/CAS, atomicity и no-repeat tests остаются.

### OE-05. Второй, неиспользуемый API общего driver

`composition/common_execution_driver_contracts.py` (удалён в OE-05)
определяет command/source variants, reader bindings, fingerprint domains,
Drive/Advance/Lookup и receipts. Source-импортера модуля не найдено.
Текущий [postgres_application](../src/chiplog/composition/postgres_application.py)
использует `NativeIngressReceiptV1` и существующий native runtime напрямую.

Меньший вариант: удалить старый API и его исключительные shape/inventory tests.
Не оборачивать работающий driver в эти DTO ради создания потребителя.
Сохранить authenticated ingress → общий driver → completion/delivery и reopen
без второго обращения к модели. Контрпример — нынешний функциональный клиент
этого точного API; старый публичный интерфейс сам по себе не требует совместимости.

### OE-06. Ещё две неиспользуемые поверхности

Независимые срезы:

- Удалённые `platform/operation_registry_contracts.py`,
  `platform/production_operation_inventory.py` (удалён вместе с OE-05)
  и их исключительные `platform/runtime_surface_contracts.py`
  описывали роли, cardinality, C_OPEN/UNMOUNTED и applicability без текущего bridge
  к native publication. У верхнего inventory не найден source-импортер.
- Удалённый `adapters/driven/cli_custody_socket.py`
  содержал отдельный challenge/nonce/inode/deadline socket protocol.
  Source-конструктор не найден; найден whitelist в verifier.
  `native-chat` использует [postgres_native_cli](../src/chiplog/composition/postgres_native_cli.py)
  с trust/current UID и PG ingress. Отдельный рабочий socket-channel adapter
  [native_ingress_cli](../src/chiplog/adapters/driven/native_ingress_cli.py)
  тоже сохраняется.

Меньший вариант: удалить каждую неиспользуемую поверхность со своими tests.
Сначала проверить отдельные потребители общих types и whitelist.
Авторизация publication, OS-peer, exact-byte replay и ACK после custody остаются.
Контрпример — нынешний источник/операция, который реально использует эти механизмы.

### OE-07. Scheduler startup/service proof и отдельный AST-интерпретатор

Удалённый `capabilities/agent_loop/scheduler_startup.py`
восстанавливал полный historical materialized cut;
`scheduler_service_acceptance_contracts.py`
переносил records, их bytes, preparations и inventory через повторные joins.
Для этих верхних entrypoints найдены тестовые, но не source-потребители.
Удалённый `verification/scheduler_schema.py`
добавлял field-sensitive AST abstract interpreter с фиксированными codec hashes;
его manifest API тоже используется тестами.

Текущий [postgres_scheduler](../src/chiplog/composition/postgres_scheduler.py)
использует shared domain/client contracts и собственные native records/checks.
Меньший вариант — удалить именно неиспользуемые proof/startup листья и их tests,
сохранив текущие scheduler semantics, нормальные bounds, lease fences и overflow.
Это не разрешение удалить всю `scheduler_*` семью: preparation handlers и общие
contracts ещё имеют настоящие регистрации.

Сохранить atomic occurrence/Run, stable replay identities, overflow resolution,
takeover и stale-lease отказ. Контрпример — текущий client flow, достигающий
этих точных historical entrypoints, или нужная гарантия, не обеспеченная native path.

### OE-08. Узкие compatibility-only ветки и byte goldens

Три разных небольших среза:

- [prepared_external_delivery_contracts](../src/chiplog/capabilities/deployment_trust/prepared_external_delivery_contracts.py):
  удалённая в OE-08 V1 grant/anchor/current-reader family была явно сохранена для historical byte compatibility.
  У неё не найден отдельный source-потребитель. Общие Source/Resources/Mandate V1
  используются V2 scope и остаются, несмотря на имя V1.
- [deployment_trust/_r7_process](../src/chiplog/capabilities/deployment_trust/_r7_process.py):
  удалённый дополнительный old direct-wire decoder выдавал UNSUPPORTED, тогда как текущий
  маршрут использует pinned wrapper. Можно сохранить один текущий decoder и
  обычный protocol refusal; сам embedded payload type остаётся.
- [_journal](../src/chiplog/adapters/driven/deployment_trust/_journal.py)
  и [test_journal_native_json](../tests/platform/test_journal_native_json.py):
  удалены отдельная поддержка исторических UTF-16/non-standard envelope forms и тест
  вызова fallback parser. У текущего writer другой нормальный контракт.

Меньший вариант: удалить только обещание принимать старую форму и его исключительные
фикстуры/ветки. Oversized integer rejection, HMAC/predecessor integrity, точные bytes
нынешнего writer, реальная V2 lifecycle authority и malformed-input refusal остаются.
Выбор одного parser должен сохранять его текущую семантику отказа.
Контрпример — текущий producer этих old forms, а не fixture ради совместимости.

### OE-09. Immutable freeze плюс отдельные current extensions

Удалённые `architecture/r4_r5_freeze.py` и `current_contract_extensions.py`
поддерживали неизменный исторический universe и отдельный список additions.
Второй модуль прямо называет это registration представлений, не running authority.
Найденная source-связь — extension → freeze; проверки вызываются из tests/verification
lanes. Native runtime этими списками не авторизуется.

Меньший вариант: убрать историческую неизменность и исключительно её goldens,
оставив конкретные нужные проверки текущих import/ownership границ. Удаление этих
таблиц не распространяется на действующие owner manifests и shared runtime.
Контрпример — текущая authority граница, реально обеспечиваемая этой equality
с historical universe, а не сходством слов «owner»/«boundary».

### OE-10. Evaluation выполняет другой AgentLoop

[verification/loop_scenario](../src/chiplog/verification/loop_scenario.py#L1)
называет Run/Turn decisions исполнением production service, но вызывает
[composition/r13](../src/chiplog/composition/r13.py#L17) и отдельный
[AgentLoop](../src/chiplog/capabilities/agent_loop/application.py).
Этот путь используется transcript runner, поэтому он не мёртвый.
Пользовательский native PG driver — другой.

Подтверждены дублирование orchestration и ограниченность такого production witness.
Меньший вариант: сопоставить полезные assertions единственного работающего сценария
с native сценариями и перенести недостающие в текущий путь. После этого убирать
старую assembly и исключительные adapters. Если нужен самостоятельный offline
simulator, назвать его отдельный сценарий и перестать выдавать его за native evidence.
Общие `contracts`, label joins, R13/calendar/workspace не удалять.

Это требует semantic mapping, а не немедленного удаления всей transcript-функции.
Контраргумент — полезные быстрые authored сценарии без PG. Их ценность нужно
сохранить узким способом; новый универсальный transcript engine не требуется.

## OE-11. Упростить вложенный stage0 запуск

**Подтверждён механизм; смена workflow — отдельный срез.**
[test_profiles:53](../tests/verification/test_profiles.py#L53) из полного pytest
вызывает stage0; [runner:231](../src/chiplog/verification/runner.py#L231)
запускает pytest для восьми frozen slices. Это не рекурсивный полный suite.
Чтобы не исполнять те же cases ещё раз во внешнем наборе,
удалённый `tests/support/stage0_delegation.py` менял collection,
удаляет внешние items, пишет дочерние phase reports и сверяет covered union.
Его собственные meta-tests добавляют ещё subprocess scenarios.

Пока nested design существует, guards полезны: без них «зелёный» внешний запуск
может скрыть потерянные cases, skip или xfail. Отдельное удаление dedup/receipt
проверок не является допустимым упрощением.

Меньшая композиция: один полный pytest исполняет все функциональные cases один раз;
planning/aggregation runner тестируются через controlled results и один ограниченный
реальный subprocess smoke. Самостоятельный stage0 CLI можно сохранить, если у его
evidence есть отдельный потребитель; это не требует вызывать все его срезы из его
же regression test. Полная регрессия и failure propagation остаются.

Сохранить refusal неизвестного profile, неполного/дублированного increment set,
отрицательного child exit, неизменное HOLD deployment eligibility и artifact integrity.
Будущий diff должен доказать равенство реально исполненного набора, не просто count.
Strict R8 catalog продолжает действовать до отдельного решения по нему.

Для масштаба стоимости есть отдельный исторический факт: полный gate на `5c18b07`
завершился за 1820.96 s, pytest — за 1729.01 s; исходные локальные receipt/log:
`/private/tmp/chiplog-cleanup-full-gate.json` и `.log`.
Это время всего запуска в том окружении. Оно не измеряет стоимость stage0,
AST-проверок или ожидаемое ускорение любой рекомендации этого аудита.

## Где вывода об удалении пока нет

| Кандидат | Почему недостаточно доказательств | Следующий дискриминатор |
|---|---|---|
| Один Run format вместо V2/V3 | Native runtime реально создаёт/читает оба; `oneOf == 2` не доказывает лишнюю функцию | Сопоставить каждый variant с нынешними сценариями; затем решить единый текущий формат |
| PostgreSQL trust journal + materialization | Запись идёт в одной transaction, но shared service служит другим store и умеет corruption repair | Назвать native failure modes, требующие независимой materialization; сохранить auth/recovery |
| Owner subprocess/attestation и цепочки ROUTES | Есть живые generic R13/workspace consumers; isolation может быть реальной границей | Нынешний adversary/сценарий и нужный isolation contract, затем отдельный срез |
| Одинаковые workspace DTO в нескольких owners | Дублирование явно есть, но может сохранять независимые policy/import boundaries | Проверить разрешённый inert sharing и актуальные owner consumers, не централизовать policy |
| All-source exact hash catalog | Native client напрямую его не потребляет, но strict full catalog явно установлен §7 | Отдельно решить требуемую build/deployment identity; не удалять норму молча |
| Stateful transcript compiler/YAML platform | Часть вариантов NOT_RUNNABLE; authored требования могут оставаться полезными | Нынешний authoring workflow и native consumer, отдельно от старого AgentLoop |
| Вся physical preparation/dispatch семья | Часть composition graph выглядит отключённой, но dynamic owner registrations и shared helpers требуют closure | Точный dependency cut; например, `r16_dispatch_registry.SEMANTICS` используется PG effects |

Завершённые migration maps и одноразовый recording plugin — низкоприоритетная
архивная уборка: текущего gate consumer не найдено, но carrying cost невелик.
Транзакционный live-worktree integration verifier тоже требует отдельного анализа
installation contract; перенос в disposable repo нельзя выдавать за доказанное
эквивалентное упрощение. Ни один из этих пунктов не должен задерживать продукт.

## Что сохранять

- Claim перед внешним SEND, независимый receipt/reconciliation и состояние
  неопределённого исхода. Обычная телеметрия не заменяет durable факт отправки.
- Текущую actor/session/tenant authentication, CAS и atomic publication membership.
  Доменный журнал решений — не автоматически «диагностическая информация».
- Reopen/replay, model-start ACK-loss, stale writer/lease, исходные obligation refs
  и долговечный read-only лимит трёх попыток.
- Реальные PG atomicity, corruption и native-channel горизонтальные tests.
  Их subprocess/DB setup имеет конкретный функциональный смысл.
- Полезные generic R13/calendar/workspace проверки текущих сценариев.
- Простые конкретные helpers и защиту реальных developer-data failures; количество
  файлов и отсутствие недавнего сбоя не делают их лишними.

Функциональные опорные наборы для будущих срезов:
[execution](../tests/composition/test_postgres_execution.py),
[two seals](../tests/composition/test_postgres_two_seals.py),
[run recovery](../tests/composition/test_postgres_run_recovery.py),
[effects](../tests/composition/test_postgres_effects.py),
[completion](../tests/composition/test_postgres_completion.py),
[scheduler](../tests/composition/test_postgres_scheduler.py),
[scheduler actions](../tests/composition/test_postgres_scheduler_actions.py),
[ingress](../tests/composition/test_postgres_ingress.py),
[public client](../tests/composition/test_postgres_client.py).
Это список требуемого сохранения смысла, не новые результаты запусков.

## Порядок следующих срезов

1. OE-01: два текущих метода, тот же preparation cut, неизменные проверки commit.
2. OE-05 и узкие части OE-08: небольшие замкнутые удаления без нового слоя.
3. OE-02/OE-03/OE-06: целые отменённые подсистемы вместе с исключительными tests/support.
4. OE-04/OE-07/OE-09: убрать отменённую assurance и её фиксацию исторической формы.
5. OE-10/OE-11: отдельно согласовать и упростить evaluation/test orchestration,
   сохранив исполнение актуальных требований.

Это предложения для выбора следующей работы, не уже обещанные реализации и не
новые обязательные гейты. Каждый будущий срез требует своего точного closure,
реального положительного сценария и релевантных отрицательных проверок.
Не создавать compatibility aliases, новые универсальные registry, caches или
fixtures-фреймворк ради сохранения удаляемого контракта.

## Swarm и качество вывода

Модератор выбирал следующий шаг; восемь read-only исполнителей собирали факты.
Пять фиксированных моделируемых линз: Ousterhout — сложность интерфейсов,
Feathers — тестовое сцепление, Hickey — простые представления, Helland — границы
данных, Norman — пользовательский исход. Это не реальные участники и не внешняя
экспертная верификация.

Ход: E1 — branch/inventory; E2 — все восемь областей; A — различия и объединение
пересекающихся находок; E3 — холодные контрпримеры; G — ранжирование; H — отчёт.
Совпадения по H1, diagnostic и SQLite proof объединены, не посчитаны повторно.

Холодный исполнитель без родительского контекста проверил четыре утверждения:
OE-01, exact H1 islands, верхний SQLite descriptor API и предложение по stage0.
Блокирующего функционального контрпримера не найдено. Принята его поправка о
frozen slices вместо «полного recursive suite». Он также подтвердил, что удаление
одних coverage receipts разрушает assurance. Проверялись исходники, не будущий diff.

Pre-mortem пяти линз: скрытый dynamic caller; уникальный негативный тест; новая
абстракция вместо удаления; потерянная свежесть после внешнего действия; отдельный
полезный authored workflow. Это условия следующих срезов и предел уверенности.

Режим завершения — ограниченный первый аудит: весь реестр просмотрен структурно,
но безопасность удаления каждой строки и эквивалентность ещё не написанного
рефакторинга не доказаны. Архитектурные вопросы из таблицы гипотез намеренно не
выдаются за решённые. Изолированный контекст той же модели не снимает общий
модельный потолок.

Во время исследования не запускались тесты, нагрузка, live API или новая диагностика.
Продуктовый код и тесты не менялись. Для Markdown-коммита используется штатный
checkpoint; результаты прежних полных прогонов не переобъявлены результатами аудита.

## Выполненные срезы

### OE-01 — повторное чтение snapshot

`begin_execution` и `seal_execution` выбирают Run из уже полученного snapshot
подготовки. Общий выбор Run вынесен в узкий приватный метод; публичное чтение
по-прежнему получает актуальный snapshot. Проверки целостности и транзакционные
проверки перед записью сохранены. Путь capture с внешним вызовом не менялся.

Проверка: `CHIPLOG_TEST_POSTGRES_DSN='host=127.0.0.1 port=55439 user=postgres
dbname=postgres' uv run pytest -q tests/composition/test_postgres_execution.py
tests/composition/test_postgres_application.py
tests/composition/test_postgres_model_recovery.py` — 23 passed.
Четыре добавленных случая проверяют отказ без частичных записей после реальной
конкурентной публикации или отзыва доступа между подготовкой и записью; они
проходили и до рефакторинга. Ruff и mypy изменённого runtime проходят.
Ускорение не измерялось.

### OE-05 и верхний inventory из OE-06 — неиспользуемый driver API

Удалены `composition/common_execution_driver_contracts.py`,
`platform/production_operation_inventory.py` и два их исключительных тестовых
модуля. Это были DTO/Protocol и незавершённые декларации операций без текущего
runtime-потребителя. Их проверки сериализации и замороженного состава не
исполняли native driver. Прямых импортов, динамической регистрации или именованных
verification selectors для удаляемой связки не найдено; холодная проверка плана
не нашла функциональных блокеров. Независимый hash domain в
`r14_execution_inbox_records.py` и общие registry/runtime-surface типы сохранены.

Проверка: с локальным `CHIPLOG_TEST_POSTGRES_DSN` команда `uv run pytest -q
tests/composition/test_postgres_application.py
tests/composition/test_postgres_ingress.py
tests/composition/test_native_channel_delivery.py
tests/composition/test_postgres_completion.py` — 26 passed. Это действующие
ingress, driver, завершение, доставка, отказ при подделке/смене источника,
откат и reopen без повторного вызова модели или отправки.
`uv run pytest --collect-only -q` собирает весь оставшийся набор без ошибок;
Ruff затронутого функционального среза проходит. Каталог источников требует
обычного обновления перед полной интеграцией, а не repin на checkpoint.

### OE-08 — исторический V1 grant

Удалены исторические V1 grant/anchor/current-reader/result, их исключительные
тест и builder, а также V1 byte-stability test из V2-набора. Source-потребителей
и динамической регистрации этой семьи не найдено; холодная проверка подтвердила
замкнутость удаления. Общие Source/Resources/Mandate DTO сохранены для V2.
Текущие lifecycle, подписи команд, источник полномочий и ограничения SEND
не менялись. Это первый из трёх независимых срезов OE-08.

Проверка: `uv run pytest -q
tests/capabilities/deployment_trust/test_prepared_external_delivery_grant_v2_contracts.py
tests/capabilities/deployment_trust/test_operator_grant_command_verifier.py
tests/capabilities/effects/test_h1_scoped_preparation.py
tests/capabilities/effects/test_h1_prepared_delivery_basis.py` — 57 passed.
С локальным `CHIPLOG_TEST_POSTGRES_DSN` команда `uv run pytest -q
tests/composition/test_postgres_completion.py
tests/platform/test_operator_grant_key_pin.py` — 18 passed.
Полная collection, Ruff и mypy изменённого source проходят.
### OE-08 — старый direct wire и journal parser fallback

Owner читает только текущий pinned wrapper; старый bare read получает обычный
`PROTOCOL_REJECTED`. Добавлены положительный wrapper-dispatch и отказ при
отозванном credential с настоящими закодированными событиями trust snapshot.
Journal использует один parser, соответствующий текущему writer. Убраны два
fallback, legacy acceptance и счётчики вызовов parser/writer. Сохранены opaque
payload roundtrip, неизменность записанного префикса, oversized integer refusal,
HMAC/цепочка и отказ при повреждении durable sources.

Удалён исключительно исторический readonly V1 bundle test. Оставшийся fixture
затем удалён отдельным срезом ниже с сохранением startup integrity tests.

Проверка перед финальным удалением одного исторического теста: `uv run pytest -q
tests/capabilities/deployment_trust/test_output_scope_owner_seam.py
tests/capabilities/deployment_trust/test_h1_broker_evidence_contracts.py
tests/platform/test_journal_native_json.py tests/platform/test_journal_exact_body.py
tests/platform/test_readonly_decision_journal.py
tests/platform/test_existing_only_decision_journal.py
tests/composition/test_journal_restart_and_plan.py` — 121 passed.
Эта же функциональная выборка повторяется checkpoint после удаления.
Ruff и mypy двух изменённых source проходят. Остальные пункты остаются в очереди.

### Исторический genesis fixture

Startup integrity tests теперь готовят базу через текущий `open_r7_runtime` и
bootstrap, а не распаковывают архив старого checkout. Все прежние assertions
сохранены (AST-сравнение с заменой только tenant literal), включая пять записей
INITIALIZE, отказ до mutating constructors, полный/частичный/отсутствующий prefix,
изменение источников и восстановление без повторного journal append.
Удалены gzip fixture, инструкция его воспроизведения и capture script, который
требовал историческую ревизию. Текущий producer по-прежнему использует собственную
схему genesis V1; её номер сам по себе не является compatibility-механикой.

`uv run pytest -q tests/composition/test_r14_startup_trust_prefix.py` — 24 passed
на реальных owner subprocess. Ruff и format check проходят. Холодная проверка
плана не нашла блокеров или дополнительных потребителей удалённых файлов.

### OE-02 — отменённая H1 inventory/enrollment подсистема

Удалены восемь source-модулей inventory, enrollment, custody и read-plan registry,
их одиннадцать исключительных тестовых модулей и три support-модуля.
Прямых, динамических или manifest-потребителей за пределами этой связки не найдено;
холодная проверка дополнительно сопоставила публичные имена с остальным source.
Нового registry или compatibility API не добавлено.

`test_prepared_policy_owner_route.py` сохранён: импорт двух ID из installed helper
заменён теми же локальными значениями. AST всех его функций совпадает с исходным;
проверки подписи, чужого ключа, stale observation и result binding остаются.
Shared workspace policy/DTO и r12/r13 workspace consumers не менялись.

С локальным `CHIPLOG_TEST_POSTGRES_DSN` команда `uv run pytest -q
tests/platform/test_prepared_policy_owner_route.py
tests/composition/test_postgres_two_seals.py
tests/composition/test_postgres_completion.py
tests/composition/test_postgres_ingress.py` — 26 passed.
`uv run pytest --collect-only -q` собирает оставшийся набор без ошибок;
`mypy` по настроенному source scope проходит. Ruff и format check изменённого
policy-route теста проходят. Каталог источников обновляется перед full integration.

### OE-03 и registry DSL из OE-06 — отдельная terminal-fault диагностика

Удалены diagnostic capture/observer/issuance/provenance протоколы, terminal fault
owner shapes, fault-rule registry, исключительные source refs и operation/runtime
registry DSL. Удалены их восемь тестовых модулей и два больших fixture builder.
В общем owner-publication codec удалена только diagnostic authentication ветка,
её operation literal и validator; остальные auth/envelopes сохранены.

Source/import/dynamic-manifest проверка и холодный разбор не нашли действующего
runtime-потребителя удалённой семьи. Общие recovery/frontier/wire DTO и их тесты
остаются; typed integrity отказ, HOLD/abort и существующее model/run/effect
recovery не заменялись диагностикой или частичным успешным результатом.

С локальным `CHIPLOG_TEST_POSTGRES_DSN` команда `uv run pytest -q
tests/platform/test_owner_publication_contracts.py
tests/composition/test_postgres_model_recovery.py
tests/composition/test_postgres_run_recovery.py
tests/composition/test_postgres_effects.py
tests/composition/test_postgres_two_seals.py` — 33 passed.
`uv run pytest -q tests/platform/test_broker_contract.py
tests/platform/test_broker_routing.py tests/platform/test_broker_peer_authentication.py`
— 17 passed. Полная collection, mypy по source, Ruff и format изменённого codec
проходят. CLI custody socket из OE-06 остаётся отдельным следующим срезом.

### OE-06 — неиспользуемый CLI custody socket

Удалён отдельный `CliCustodySocket` и его исключение в import-boundary whitelist.
Source-конструкторов, direct/dynamic consumers и manifest registration не найдено.
Рабочий `native_ingress_cli` и общие custody DTO/валидация сохранены.
Матрица отрицательных import cases теперь испытывает оставшийся native leaf;
отдельный отрицательный случай подтверждает отказ для удалённого socket leaf.

С локальным `CHIPLOG_TEST_POSTGRES_DSN` команда `uv run pytest -q
tests/verification/test_r8_surface.py tests/cli/test_native_chat.py
tests/composition/test_postgres_ingress.py` — 85 passed.
Холодная проверка не нашла блокеров. Каталог обновляется перед full integration.

### OE-04 — отменённый SQLite source-proof engine

Удалены четырнадцать verification-модулей: planning/protected-anchor/descriptor
цепочки, selected engine evidence/contracts, SQL corpus, assembly forwarding,
read graph, SQLite preparation, database routes и SQL expression/family expanders.
Удалены их двенадцать исключительных тестовых модулей. Зависимости замкнуты
внутри этой семьи; отдельных runtime, selector или manifest consumers не найдено.
Холодный разбор подтвердил границы удаления.

Runtime `startup_trust_prefix`, `readonly_sqlite_snapshot` и их функциональные
проверки сохранены. Current import-boundary, source identity и PostgreSQL
admission/integrity/auth/CAS проверки не менялись.

С локальным `CHIPLOG_TEST_POSTGRES_DSN` команда `uv run pytest -q
tests/verification/test_postgres_import_boundary.py tests/verification/test_r8_surface.py
tests/platform/test_readonly_sqlite_snapshot.py tests/composition/test_postgres_planning.py
tests/composition/test_postgres_execution.py` — 109 passed.
Полная collection и mypy по source проходят. Исторические записи выполнения
source-proof в design-docs/ретро не являются оставшимися runtime obligations.

### OE-07 — неиспользуемые scheduler startup/service-proof листья

Удалены `scheduler_startup`, `scheduler_service_acceptance_contracts` и
`verification/scheduler_schema`, три исключительных тестовых модуля и service
acceptance builder. Source/dynamic/selector consumers вне этой связки не найдены.
В смешанном mandate-preparation тесте удалены только historical selected-index
helper и его проверка; две текущие owner-dispatch проверки сохранены AST-identical.
Scheduler domain, current client contracts, native records и зарегистрированные
preparation handlers не менялись.

С локальным `CHIPLOG_TEST_POSTGRES_DSN` команда `uv run pytest -q
tests/capabilities/agent_loop/test_scheduler_mandate_preparation.py
tests/composition/test_postgres_scheduler.py
tests/composition/test_postgres_scheduler_actions.py` — 25 passed.
Это актуальные occurrence/Run atomicity, replay, overflow, takeover и lease fences.
Полная collection, mypy по source, Ruff и format изменённого теста проходят.
Холодный разбор не нашёл блокеров. Каталог обновляется перед full integration.

### OE-09 — исторический freeze и current extensions

Удалены два source-модуля исторического universe/extensions и три исключительных
freeze/shape тестовых модуля. Смешанный convergence модуль заменён одним
`test_parallel_capability_imports.py`: перенесённый import-boundary тест имеет
неизменный AST. Удалена только freeze-equality проверка в `test_trust.py`.
Selectors stage0 runner обновлены на оставшийся тест. Действующие manifests,
verifier, ownership/commit-boundary и отрицательные CROSS_OWNER_COMMIT проверки
сохранены; новой заморозки API взамен не добавлено.

`uv run pytest -q tests/architecture/test_parallel_capability_imports.py
tests/capabilities/deployment_trust/test_trust.py
tests/capabilities/planning/test_planning.py tests/verification/test_manifest_contract.py
tests/verification/test_manifest_mutants.py` — 85 passed.
Полная collection, mypy по source и Ruff изменённых файлов проходят.
Холодная проверка не нашла runtime-потребителей или блокеров. Историческая
`quality/test-migration.json` не используется как текущий selector registry.

### OE-11 — один полный pytest вместо вложенной оркестрации

Profile aggregation теперь испытывает управляемые исходы всех R1–R8 срезов,
их точные непустые selectors, PASS/FAIL и неизменный HOLD eligibility.
Два ограниченных настоящих subprocess probe проверяют returncode 0/1 и PASS/FAIL.
Standalone stage0 CLI сохраняет свои реальные срезы; полный pytest больше
не вызывает их из profile regression test. Удалены plugin collection-dedup,
coverage receipts и их исключительные meta-tests; PostgreSQL plugin сохранён.
Каждый оставшийся функциональный case должен исполниться обычным полным pytest.

`uv run pytest -q tests/verification/test_profiles.py tests/verification/test_stage1.py`
— 40 passed. Проверки failed increment, malformed registry, unknown profiles и
artifact integrity сохранены. Ruff и format проходят; полная collection успешна.
Первый прогон обнаружил ожидаемую строгую несовместимость старого каталога с
накопленными удалениями. После сверки с рассмотренным source diff каталог обновлён;
`python3 .harness/scripts/checkpoint.py --strict-r8` проходит. Это обновление
учитывает предыдущие срезы и не заменяет финальную полную регрессию.

### OE-10 — offline planning-adoption сценарий сохранён по текущему требованию

Проверка потребителей и холодный semantic mapping выявили непокрытый native PG
эквивалент: model planning proposal → неизменный display → exact authenticated
adoption → planning receipt. Native driver HOLDs изменяющие вызовы; его тест
проверяет продолжение после отмены, а не planning adoption. PG planning.create
покрывает прямое создание, но не эту цепочку. Удаление R13 loop сейчас потеряло бы
действующее функциональное покрытие: неверный peer, подмену receipt, replay и stale
display. Поэтому отдельный offline сценарий сохранён, бизнес-логика в PG не добавлена.

Убрано ложное утверждение об evidence текущего native production path:
docstrings/docs прямо называют offline simulation; artifact содержит
`execution_kind=OFFLINE_R13_SIMULATION`, `offline_runtime_manifest`,
`offline_evaluation_manifest`, `offline_loop_id`. Старых misleading полей нет.
Тест подтверждает маркировку и сохранение HOLD. Общие R13/calendar/workspace
компоненты остаются ради конкретного исполняемого сценария, а не совместимости.

`uv run pytest -q tests/evaluation/test_loop_scenario.py
tests/evaluation/test_stateful_transcripts.py tests/composition/test_loop_adoption.py`
— 61 passed. Ruff, mypy и строгий каталог проходят. Это проверка offline пути;
native PG evidence остаётся в его собственных функциональных тестах.

### Финальные compatibility-хвосты

Удалены `_PlanningCommittedResult`/`_PlanningOutcome` aliases; use case и SQLite
adapter используют те же публичные типы напрямую. У executable overflow decoder
удалены V1 acceptance, legacy selected/unsupported DTO и повторный canonicalizer.
V2 corruption mutations по-прежнему проверяют настоящий decoder.

Холодная проверка остановила более широкое удаление: V1 hold всё ещё производит
текущий scheduler preparation и использует native client-контракт. Producer,
его fixture и функциональный тест полного hold/resolution сохранены. Номер схемы
не служит основанием для удаления действующей функции.

С локальным `CHIPLOG_TEST_POSTGRES_DSN` команда `uv run pytest -q
tests/capabilities/planning/test_planning.py
tests/capabilities/agent_loop/test_scheduler_overflow_hold_records.py
tests/capabilities/agent_loop/test_scheduler_client_contracts.py
tests/capabilities/agent_loop/test_scheduler_preparation.py
tests/composition/test_postgres_planning.py tests/composition/test_postgres_scheduler.py`
— 82 passed. Ruff, mypy и строгий каталог проходят.
