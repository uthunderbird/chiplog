# Stateful-профиль транскриптов R13

**Статус: design, 2026-09-20.** Это спецификация будущего stateful runner,
не свидетельство исполнения. Основной [формат Markdown](../../../TRANSCRIPTS.md)
един для старых сценариев и этого набора. Текущий compiler поддерживает этот профиль, базовый fenced-формат
и совместимость с HTML v1. Компиляция раскрывает зависимости и варианты,
но не заменяет отсутствующие production bindings.
Новых ACTIVE T-ID и заявления об исполняемом покрытии здесь нет.

## Структурированный исходник и чтение

`format_version: 2` задаёт контейнер: канонические реплики и блоки
`yaml transcript`. `protocol_version: 2` задаёт stateful-семантику этого профиля:
варианты, prefix, captures, faults, context, calls и events. Эти версии независимы.

`scenario` задаёт мир и фикстуры, локальный `step` — вход и конец шага,
`design` объясняет механику, `expect` задаёт проверки, `forbid` — запреты.
Определение шага находится рядом с его действием. Вызовы, ожидаемые результаты
и состояние видны сразу. Только подробные events, связи и context могут быть
в `<details>` того же шага; HTML-комментариев с контрактом нет.

Для одного шага допустимы несколько `expect`-блоков с непересекающимися полями:
видимые `calls`, подробные `events/context`, видимые `baseline/state`. Они
объединяются по `step`; повтор поля — ошибка. `design` не создаёт ожидания:
`expect_ref` и `fixture_ref` связывают пояснение с объявленными данными.

Состояние хранится единожды в YAML. Его таблица или diff могут быть сгенерированы,
но не поддерживаются второй ручной копией. Фактический отчёт отдельно показывает
actual рядом с expected; ожидаемые значения не выдаются за наблюдённые.

## Scenario, варианты и шаги

`scenario` содержит паспорт, `arrange`, `initial_context`, `policy`, `fixtures`
и `variants`. Определения шагов — отдельные блоки `step` возле действий.
`variants.steps` содержит только последовательность их ID. Истории и общая
библиотека имеют `version: 4` после переноса в единый формат.
`common.md` с `scenario.kind: library` не исполняется как самостоятельная история.

`use: file.md#/scenario/arrange` — локальная ссылка на JSON Pointer в объединённом
документе. `$name` — параметр варианта либо captured value, `$name.field` — поле
захваченного объекта. Обычная строка без `$` — литерал. Параметры объявлены в
`variants.parameters`, captures — на input/call result/event. Неизвестная,
неоднозначная, будущая ссылка и цикл запрещены. `source` prefix — относительный
путь к сценарию, а не ссылка на готовый результат.

Каждый вариант исполняется в отдельной БД. `variants.steps` задаёт последовательность
ID локальных блоков `step`; перечисленные варианты не продолжают друг друга.
`allowed_outcome` — имя разрешённого исхода, не замена assertions.
`requires` и `requirements` задают preflight: неполная production binding или
неподходящая Turn-schema означает NOT_RUNNABLE, а не PASS.

Prefix содержит `source`, `variant`, `until`, `capture_namespace`, `inherit_current`,
`inherit_context`, `fresh_run`. Драйвер выполняет настоящий prefix до наблюдённого
события `until` и останавливается ДО следующего действия. Незавершённое окно
родительского сценария не объявляется PASS. Экспортируются только уже достигнутые
captures, immutable current и реально полученный контекст. Полный end-to-end тест
исходной истории остаётся самостоятельной проверкой. `prefix.*` — namespace этих
событий и значений. Prefix входит в общий budget варианта.

Каждый шаг имеет `id`, точный `input` и обязательный `end`. Начало окна — приём
этого input, конец — зарегистрированное наблюдение `end`, в пределах его Run/Turn/
call lineage. `step.start` — observer baseline до предметных действий шага.
Для stale baseline `after_fault_before_adoption` явно переносит границу за легальную
смену authority head. `end` не достигнут, budget исчерпан или fault не исполнен — FAIL.

Виды input: `message` (message_ref), `authenticated_adoption` (principal, proposal,
display_digest и независимый authority act), `authenticated_adoption_replay`
(те же bytes акта), `owner_command_replay` (тот же envelope), `owner_command_fault`
(явная мутация копии), `resume` (продолжение реального loop), `observe` (checkpoint
наблюдателя). `resume` не вызывает tools и не подделывает owner events.
`fault_before_adoption` выполняет fault внутри окна шага, перед передачей
аутентифицированного акта владельцу (`<step>.adoption`); baseline снимается после fault.
`different_from` в fault/input требует значения того же типа, не равного captured;
`replace` меняет только названное поле, остальные байты/значения сохраняются.

Видимые ответы Chiplog — exemplar, не golden strings. Пользователь говорит
о своих делах обычными словами; внутренние названия сущностей остаются в технических
блоках. Chiplog называет конкретное действие и его результат, не пересказывает
протокол. Ссылки на результат и служебные ID не проговариваются как часть реплики.
Входные сообщения берутся из единственной канонической реплики
`**Пользователь · id:** текст`; input и initial_context используют `message_ref`.
Prefix наследует уже разрешённые сообщения вместе с контекстом. Если input уже есть в initial messages,
`already_in_initial_context: true` запрещает добавлять его повторно.

## Три разных состояния

- `arrange` — предметные записи и fixture bindings до начала прогона.
- `initial_context` — точная граница видимости модели непосредственно перед первым
  model call: ordered messages/screens, bytes, tool schemas, instruction artifact,
  prior tool results, summaries, attention tail. Пустые коллекции явные.
- `expect.state` — скрытые проверки наблюдаемого состояния в конце шага.

В prefix-вариантах исходный контекст сначала получает source-сценарий; после prefix
унаследованный actual context заменяет initial context, не склеивается с ним и не
сбрасывается. История, доступная только через tool, заранее в prompt не попадает.
Runtime tenant/principal/heads binds не становятся управляемыми моделью аргументами.

`current` — ordered screen ID → immutable snapshot reference; JSON `screens` в
общей фикстуре задаёт начальный порядок. Snapshot несёт content bytes, revision,
source/provenance, disclosure label, внутренний frontier либо внешнюю observation
identity и явную staleness. Это draft DTO для публичных `DashboardScreen` /
`ScreenSnapshotRef`, не второй источник Plan. Разные presentation revision допустимы
только там, где это явно разрешено, например при replay с той же identity.

Обновление в `expect.context.updates` имеет `screen`, `from_revision`, `after_event`
и ровно один источник: `from_observed_result` либо `injected_snapshot`. Оно проверяет
публикацию actual snapshot и его передачу следующему model call; assertion не
устанавливает экран. Injection разрешён только на объявленной fault/external
boundary. Нельзя инъецировать Planning success. Неверный predecessor, ранний показ,
циклическая ссылка или неизвестное событие — ошибка. Sealed visibility manifest
предыдущего вызова не меняется задним числом.

Без обновления экраны carry; отсутствие `current` означает carry, `{}` — ни одного
экрана. Carry не обновляет свежесть и повторно проверяет disclosure. Observer
снимает actual context на каждом model call. Необязательный `target` — только
assertion экрана, не источник следующего current; этот набор его не требует.

## Вызовы, результаты и фикстуры

`expect.calls` хранит `id`, `boundary`, `operation`, точные `arguments` либо полный
`envelope`, `count`, необязательные `after/before` и `result`. Boundary различает
`model_tool` и `owner`: owner execute от adoption-handler не доказывает выбора
инструмента моделью. `calls: []` запрещает model tools в окне, но не служебные
owner events. Запрет owner operation задаётся отдельно `count: 0` или `forbid`.

`arguments` сравниваются по точному набору ключей, типам, значениям и порядку
массивов. Owner arguments — draft projection полного envelope: production binding
должна также проверить целиком command, owner-allocated IDs, freshness binding,
tenant/principal/contour и authenticated act. Пропуск runtime-поля недопустим.
`envelope.eq` сравнивает весь captured envelope, `eq_input` — весь текущий input
после явно объявленного fault. Replay требует идентичности bytes, не только ID.

`count: 1` — ровно один; `0` — отсутствие во всём окне; `{min: 0, max: 1}` —
допустимый ранний отказ. Если вызова нет, его result не требуется и capture не
возникает. При наличии проверяется каждый result. Незаявленные model tools
запрещены, дополнительные разрешённые вызовы требуют конечного диапазона.

`result.fixture` ссылается на ответ внешней/hermetic boundary; fixture имеет
`boundary`, структурированный `match` и типизированный `result`. Она отвечает
только на фактический совпавший запрос, не делает вызов и не удовлетворяет count
своим существованием. `fixtures.use` может разрешаться через параметр варианта.
Поле fixture `steps` ограничивает её активность перечисленными локальными шагами.
Model fixtures — ответы модели, не tool results; они проходят обычный capture,
schema validation и semantic acceptance. Готовый verdict инъецировать нельзя.

`result.source: real_boundary` требует настоящего owner result при будущем запуске;
это требование сценария, а не запись уже наблюдённого результата. `fields` — точные
проверки явно выбранных полей результата/события; остальные поля не игнорируются
схемой и сохраняются в full trace, но не обязаны совпадать с authored exemplar.
`capture` сохраняет поле полного actual объекта, а не текст модели. `kind` задаёт
тип результата, который связывается с production schema до запуска.

У вызова автоматически есть события `<id>.call` и `<id>.result`. `expect.events`
проверяет owner/observer события с `id`, `operation`, `count`, `fields`, `capture`.
`same_call_as` связывает result-event с call identity; это тот же наблюдённый
результат, не второе выполнение. Capture результата и совпадающего event атомарен.
`after/before` образуют DAG обязательных наблюдений; отсутствующий узел — FAIL.
`concurrent_with` допускает оба порядка, не требует реальной одновременности.

`faults` содержат точную boundary, мутацию, причинную привязку, `count: 1` и
`require_reached: true`. Observer обязан записать `fault.reached`. Смена head
проверяет dependency реального binding; не тот head — setup FAIL. Disclosure fault
должен означать реальное расширение получателей без основания.

## Проверки состояния и запреты

`expect.state` — карта observation path → один оператор. Paths определены в
`common.md.scenario.observations` и должны быть привязаны к публичным owner/observer
данным. Binding каждого observation path явно задаёт тип: счётчик, значение или объект.
`eq` над счётчиком сравнивает cardinality, над объектом — полное значение;
тип не выводится из правой части сравнения. Область — текущий subject/command, а не размер всей БД.

| Оператор | Значение |
|---|---|
| `eq` | Точное значение или полный captured объект; для count-binding — cardinality |
| `delta` | Изменение cardinality относительно указанного baseline |
| `unchanged: true` | Те же полные значения/identity относительно baseline |

`expect.relations` сравнивает явно названные operands через `eq/not_eq/unchanged`.
`response.semantics` остаётся оценкой смысла текста. Оно не заменяет hard gates.
`forbid.operations` запрещает операции во всём указанном scope; `predicates` —
имена запретов из таблицы ниже. При `use` запреты объединяются, не переопределяются;
`steps` сужает только добавленный локальный запрет. Неизвестный predicate — ошибка,
его реализация до runnable-статуса должна иметь негативный контрольный вход.

| Класс predicate | Что наблюдается |
|---|---|
| `external_effect_before_authority`, `planning_commit_before_exact_adoption` | Effect/commit предшествует соответствующему authenticated exact adoption |
| `provider_or_real_recipient_exposure` | Выход на реальный provider/recipient |
| `claim_current_calendar_availability_from_stale_observation` | Принятый claim об актуальной занятости без свежего evidence |
| `fixture_as_evidence_of_internal_commit`, `inject_authoritative_proposal`, `inject_acceptance_verdict` | Подмена owner/display/acceptance результата фикстурой |
| `hidden_oracle_in_model_context` | Попадание expect/forbid/variants/unused fixtures в model input |
| `infer_current_purpose_from_past_journal_or_stale_calendar` | Proposal без полученной истории в unavailable-ветке |
| `treat_scripted_adapter_as_evidence_of_history_understanding` | Заявление model-understanding coverage только по scripted adapter |
| `reuse_old_adoption_for_new_display`, `mutate_existing_adoption` | Иная display identity либо изменение уже принятого акта |
| `treat_unreached_authority_fault_as_stale_coverage`, `count_unreached_fault_as_tested` | Успешная сертификация ветки без достигнутой инъекции |
| `new_command_id_for_replay`, `overwrite_prior_committed_result` | Изменение ID replay либо старого полного результата |
| `infer_deduplication_from_call_count_only`, `infer_no_write_from_unchanged_screen` | PASS без независимого сравнения owner publications |
| `treat_fault_as_authorized_adoption_change` | Fault получает authority обычного adoption |
| `replace_typed_assertion_with_unverified_commentary` | Negative fixture не содержит требуемый DeliveryAssertion |
| `rollback_legal_planning_commit_on_completion_rejection` | Исчезает законный R/намерение после rejection |
| `accept_schema_valid_json_without_semantic_check` | Acceptance без проверки claim/evidence/disclosure |
| `count_schema_rejection_as_missing_evidence_coverage` | Schema rejection засчитывается как semantic rejection |
| `accept_mixed_internal_frontier` | Принят внутренний batch из разных frontier |
| `emit_context_with_wrong_snapshot_revision` | Подменённый snapshot дошёл до model emission |
| `broaden_disclosure_without_authority` | Принята публикация более широкому набору получателей |
| `replace_typed_rejection_with_model_refusal` | Требуемый boundary rejection подменён обычной репликой |

## Версионирование, результаты и подключение

Сценарий хранит только authored ожидания. Result artifact отдельно сохраняет
фактические calls/results, screens, effects, trace и feature vector, связывая их
с scenario ID/version, authored digest и compiled bundle digest. Исходные файлы
не переписываются фактическими результатами.

Bundle включает раскрытые imports/fixtures/prefix contracts, ordered inputs,
snapshots, matchers, faults, bindings, prompt/model/tool/policy/budget/clock/adapter
artifacts. Изменение common или prefix меняет digest каждого потребителя.
Design/видимая история меняют source digest; исполняемые поля — также bundle.

Закрытая структурная схема, parser и проверка imports/variants/steps/captures
реализованы в verification.transcript_stateful и transcript_schema.
До runnable-статуса ещё нужны точные versioned operation/event/observation bindings,
публичные fixture adapters, matcher checks и исполнение stateful driver. Символический reference, preflight или parsing YAML сами по себе не дают
покрытия production поведения. HTML v1 остаётся отдельным decoder без permissive fallback. Stateful-профиль
выбирается явно по protocol_version; неизвестная версия отвергается.

Обязательные плохие входы будущего runner: extra/missing argument, missing/duplicate
call, неверный result, перестановка обязательных событий, недостигнутый fault,
неверная snapshot revision, broken/forward reference, повтор поля expect, незакрытое
окно, незаметное изменение общего fixture. Каждый даёт FAIL/compile error.

## Набор

1. [История и создание намерения](01-intention-from-history.md): quarterly, annual, unavailable.
2. [Подтверждение](02-adoption.md): stale с rebuild/old replay/fresh adopt, wrong-display, wrong-principal.
3. [Команда](03-replay.md): идентичный replay и тот же ID с другим payload.
4. [Завершение](04-completion.md): semantic rejection, локальный control, отдельный schema-unavailable.
5. [Workspace](05-workspace.md): external lag, mixed frontier, wrong revision и disclosure до/после коммита.

R12 проверяет workspace component ports; R13 — единственный production loop через
hermetic leaves. Полный T01 и effect/recovery относятся к R16–R18. Этот draft-набор
не заменяет DoD Run/Turn/ModelCallAttempt и не даёт cohort-visible permit.
Runnable [local-planning-receipt.md](../../local-planning-receipt.md) уже использует
базовый fenced-формат с каноническими репликами и локальными шагами; его узкий driver
не исполняет описанные здесь stateful-сценарии.


## Неподключённые параметры и расхождения с текущим R13

Этот перенос меняет представление, но не объявляет draft API контрактом production.
До runnable-статуса необходимо разрешить следующие различия:

- `history.read` / `planning.propose_intention` против текущих ToolSpecs
  `propose_planning` / `propose_intent`; история сейчас входит в исходный workspace.
- Новый proposal в stale-ветке против допустимого повторного display того же proposal.
- Draft `result.id` / `result.intention_id` против `PlanningCommittedResult.result_id`
  / `intention_line_id`; assertion ссылается на `LocalPlanningReceipt.evidence_id`.
- Календарный assertion отсутствует в текущей Turn-schema: semantic-ветка NOT_RUNNABLE.
- Budget 8 model / 16 tool calls — выбор сценария; текущая политика по умолчанию
  ограничивает 16 Turns и 8 tool calls в одном ответе, а не во всём сценарии.
- F1/A1, owner/tenant-fixture, clock, labels, screen revisions и observation paths
  требуют явных fixture/observer bindings. Нельзя подставлять их как production DTO.

Неизвестные bindings перечислены в common как `production_schema: null`.
Эти разрывы не устраняются переименованием Markdown-блоков и не считаются покрытыми
проверкой внутренней согласованности YAML.


## Общий compile/preflight/run

Команда `uv run python -m chiplog.verification.transcript_suite` принимает файл
или каталог и обязательный `--artifact` для нового JSON-отчёта. Каждый вариант
получает собственный результат. При отсутствии production binding результат
NOT_RUNNABLE, даже если изменить status на executable. Библиотека common получает
COMPILED; имеющийся локальный R13 исполняется через production loop и получает PASS
только после проверок. Повреждённый контракт получает FAIL, а не NOT_RUNNABLE.
