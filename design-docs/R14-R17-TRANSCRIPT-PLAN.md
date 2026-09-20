# План транскриптов R14–R17

Статус: план разработки, 2026-09-20. Пользователь согласовал организацию по
историям и детализацию первых двух. Этот документ не является транскриптом,
исполняемым контрактом или свидетельством прохождения milestone.
[Формат транскриптов](TRANSCRIPTS.md) остаётся источником правил оформления.

## Основания и границы

Матрица опирается на IMPLEMENTATION-ROADMAP.md, разделы R14–R18 и таблицу
«Authored evidence fixtures», а также R14-R17-CONTRACT-FREEZE.md из ветки
`feat/r14-r17-contracts`. Общий freeze имеет статус candidate, consumer checks
ещё открыты. Наличие типов и отдельных reducers не доказывает интеграцию.

Снимок получен командами `git worktree list`, `git rev-parse --verify HEAD`
и `git status --porcelain` в соответствующих деревьях:

| Дерево | HEAD | Состояние при чтении |
|---|---|---|
| chiplog, master | `6f34dafc750ba894bbc7dd8a5a2659f61c87675e` | dirty; согласованные правки транскриптов и compiler |
| chiplog-r14-r17-contracts | `0168d61a89fdc4d9d76920fdef89ff8cb0e9c00d` | clean |
| chiplog-r14, chiplog-r15, chiplog-r16, chiplog-r17 | `0168d61a89fdc4d9d76920fdef89ff8cb0e9c00d` | каждое dirty; незавершённые реализации |

SHA-256 прочитанных файлов (Python `hashlib.sha256(Path(path).read_bytes())`):

- contracts/design-docs/IMPLEMENTATION-ROADMAP.md: `91072eddc889bf6a7ce074277dd9a848465b2834d5a496fd708eda42f8fae0c1`;
- contracts/design-docs/R14-R17-CONTRACT-FREEZE.md: `69913c5c031ef74a666d179e209b2d498e05c0b8f49f87e15b733c8b30069aa0`;
- r16/src/chiplog/adapters/driven/effects_hermetic.py: `1e41017d12fc26103123481cdd2c9b9db4676f6c5c8fce6ca59fa280f947c60c`.

Здесь contracts и r16 обозначают соседние worktrees `chiplog-r14-r17-contracts`
и `chiplog-r16`. Для остальных dirty-модулей ниже указаны места привязки,
а не зафиксированные реализации: перед подключением перечитать текущие bytes
и записать их digest в артефакт запуска. В этой работе тесты ворктри не запускались.

Полный T01 принадлежит **R18**, T03 — **R16**. R16 проверяет свой путь с fake-adapter;
R18 соединяет владельцев и проводит T01–T04 через production-loop driver R13.
Подготовка историй сейчас не переносит эту интеграционную обязанность в R16.
Реальные Calendar/Telegram и разрешения на внешние действия вне данного плана.

## Матрица историй

Идентификаторы P01–P08 — строки плана, не новые runtime API. A/V ниже — целевые
связи с требованиями roadmap, а не заявление об их полном покрытии одной историей.
Авторские сценарии P01–P08 оформлены в stateful v2; ссылки находятся в
[индексе набора](TRANSCRIPTS.md#сценарии). P01/P02 сохраняют исходные ID и имеют
version 3. P03–P08 пока являются компонентными историями, а не сквозными
диалогами production loop. Runtime bindings остаются открыты: отдельный
результат компиляции не меняет `NOT_RUNNABLE` на PASS.

| ID / история | Варианты и пользовательский исход | Целевые требования / проверки | Наблюдения и запрещённый исход | Подключение / зависимости |
|---|---|---|---|---|
| P01 / T01. Поставь завтра час на отчёт | Уточнение, показ, подтверждение, результат; duplicate, stale, crash | R16, R18; A06–A08, A30, A35, A37; V5/V6, T01 V9 | Display/adoption, atomic Plan/effect, внешний журнал, receipt; нет отправки до authority и второго события от replay | Сначала R16 owner path; полный диалог — R18, канал — R17. Детали ниже |
| P02 / T03. Получилось добавить? | Потерянный ответ, restart, сверка, поздние/противоречивые сведения | R14 + R16; A35, A37, A48; V5/V6, T03 V9 | Исходные intent/attempt/obligation, current reduction, внешний журнал; нет слепого повтора или ложного успеха | R16 lost-response path + R14 recovery; полный production-loop прогон — R18 |
| P03. Выполнение прервалось | Частичный fan-out, отмена до/после принятия, pending read, исчерпание бюджета | R14.1–4; A15, A38–A47, A49–A52, A54–A57, A59; V5/V6 | Полный ordered manifest, accounting отдельно от continuation, исходные counter/obligation; нельзя продолжить по неполному набору | Recovery driver и broker observers; затем повтор с writers R15–R17 |
| P04. Ответ модели потерялся | Possible exposure, доказанное no-exposure, поздний ответ старого поколения | R14.5; A108; V5/V6 | Пять состояний attempt, byte observer, proof и выбранное поколение; нет повторного запроса на основании timeout | Model-attempt fault hooks, зарегистрированная проверка proof |
| P05. Наступило время работы | Ноль/одно/несколько срабатываний, INDIVIDUAL/COALESCED/SKIPPED, overflow/resolution | R15.1–2; A17, A18, A98; V5/V6 | Полный eligible manifest, disposition и interval bound; нет пропуска члена, лишнего Run или раннего сдвига границы | Scheduler clock/owner/broker bindings; расписание задаётся fixture, не выдуманным разговорным API |
| P06. Работу продолжил другой исполнитель | Expiry без takeover, takeover перед submit, stale worker, rollover | R14.3–4 + R15.3; A40, A43, A47, A51, A53, A58, A99; V5/V6 | Lease/current epoch, stable lineage, исходный budget; stale writer не публикует, новый effect не создаётся | После P03/P05; writer registry повторно проверяется с R16/R17 |
| P07. Сообщение пришло повторно или во время сбоя | CLI/Telegram, crash вокруг custody/ack, poll page, quarantine, backlog/drain | R17.1–2/4; A19, A28, A100–A104; V4/V6/V7 | Exact raw bytes/token, ack/cursor, FIFO/deadline, loss obligation; нет раннего ack и повторного исполнения | Source-specific ingress drivers; отдельный полный технический набор всех ingress paths |
| P08. Результат готов, ответ не доставлен | Delivery ambiguity, смена endpoint/доступа, поздняя квитанция, CLI/Telegram parity | R16 + R17.3–4; A10, A32, A34, A107; V4/V6/V7 | Accepted render bytes, exact recipient, последний disclosure check, effect receipts; нет model rerun ради доставки или fallback адресата | Delivery/effects bindings и независимый транспортный журнал; сквозной календарный вариант после R18 |

Полнота A99 требует двунаправленного сопоставления реальных writer paths R14–R17
с registry; один успешный takeover её не доказывает. Для R17 отдельно проверяются
Telegram push/poll, CLI, provider callback/poll, reconciliation и tool-result ingress;
dashboard ingress пуст. Параметр `source` одной фикстуры не заменяет эти пути.
Числовые границы, все serial orders, omission/addition/substitution/duplicate/reorder
остаются технической матрицей соответствующего milestone, связанной с историями.

## P01 / T01 — календарный блок после подтверждения

Продолжить [существующий сценарий](transcripts/calendar-proposal-confirmation.md),
сохранить `id: calendar-proposal-confirmation`, повысить version при изменении
inputs/fixtures/checks. Теперь это design v3. В первоначальном design v2 вызовы `calendar.read`,
`display_calendar_proposal`, `authorize_displayed_proposal`, `calendar.create`
были проектируемыми; новые `cal.*` тоже являются локальными draft mappings,
а не ToolSpecs продукта.

### Исходные условия

Сохранить fixture clock `2026-08-22T09:00:00+05:00`, Asia/Almaty, пустой календарь
и отсутствие соответствующей записи в планах. Один principal, один актуальный
display. Ожидаемый интервал — 23 августа, 10:00–11:00. Fixtures задают только
внешнюю доступность и ответ провайдера; display, adoption, intent и результаты
владельцев получать от реальных границ, не вставлять как успешные ответы.
До каждого варианта runner снимает исходные journal heads и счётчики провайдера.

### Шаги и наблюдения

| Шаг | Вход и видимое поведение | Действие / результат | Проверка после шага |
|---|---|---|---|
| request | «Завтра надо час поработать над отчётом.» → уточнение времени | Production loop принимает сообщение; consequential command ещё нет | Plan без изменений; provider sends delta=0 |
| time | «В 10 утра.» → показать дату, время, название и вопрос о подтверждении | Read fixture совпадает по дате/zone; реальный display выдаёт capture | Read предшествует display; точная версия proposal; Plan и provider без изменений |
| adopt | «Да, добавь.» + независимая аутентифицированная привязка к display | Owner проверяет current head и authority; broker атомарно публикует требуемые owner results/intents | Все участники batch либо присутствуют вместе, либо отсутствуют; на этом cutoff ещё нет отправки |
| send | Событие среды: worker обработал принятую работу | Version/fence/authority checks, SEND_COMMITTED, затем fake-provider вне транзакции | В журнале провайдера ровно одна новая передача с точными bytes/identity; эффект совпадает с предложением |
| evidence | Внешний ответ с корректной корреляцией и аутентификацией | Evidence ingress и reducer фиксируют закрытое подтверждение результата | Receipt опирается на committed evidence исходного действия; одного локального send недостаточно |
| answer | «Добавил „Работа над отчётом“ на завтра, с 10:00 до 11:00.» — пример | Renderer формирует подтверждённый результат; доставка проверяется отдельно | Plan содержит принятую запись; fake-provider содержит ожидаемый эффект; связи source/adoption/intent/evidence замкнуты |

Имена adopt/send/evidence — точки планируемого разреза, не готовые значения
`step.end`. Их связывание с текущими events и observers — задача подключения.
Если driver не умеет остановиться до отправки, промежуточная проверка не считается
исполненной; требуется hook, а не синтетический snapshot из expect.

### Варианты

| Вариант | Достигнутая граница / вмешательство | Обязательное наблюдение | Запрещённое последствие |
|---|---|---|---|
| confirmed | Полный путь до closed evidence | Один batch, одна передача и один соответствующий эффект; подтверждённый receipt | Успех до evidence |
| duplicate-adoption | Повторить те же authenticated command bytes после потерянного ack и после завершения | Исторический результат replay; delta новых Plan/effect/transmission=0 | Повторное выполнение владельцев или enqueue |
| changed-replay | Сохранить command identity, изменить payload | Конфликт на реальной replay/publication boundary; журнал и sends unchanged | Принятие изменённой команды как replay |
| stale-display | После display изменить authoritative head, затем подтвердить старый display | Отказ старой привязки; актуальное предложение показывается заново | Автоматическое применение к новому состоянию; это ветка T04/R18 |
| preaccept-dispatch | Попытаться отправить до принятия вызова и atomic intents | Достигнут broker guard; independent provider log delta=0 | Отправка на основании одного proposal/DTO |
| crash-publication | Crash на обеих сторонах commit, затем restart/replay | До commit нет участников; после есть полный batch, replay не создаёт новый | Частичный Plan/effect batch |
| invalidated-before-send | Authority/version/fence меняется перед SEND_COMMITTED; выполнить обе serial orders | Invalidation-first: sends delta=0; send-first: исходная попытка сохраняется неизменной | Подмена binding или «отмена» уже отправленных bytes |

Повторное пользовательское «добавь ещё один час» не является duplicate-adoption:
оно требует отдельной истории authority и не включается в replay-вариант.

## P02 / T03 — ответ календаря потерян

Продолжить [существующий сценарий](transcripts/unknown-calendar-outcome.md),
сохранить `id: unknown-calendar-outcome`, повысить version при переработке.
Первоначальный design v2 начинался с authored booleans `authenticated/current/displayed`.
В design v3 они заменены prefix до реально наблюдаемого принятия команды;
сами boolean никогда не служат доказательством authority.

Подготовить prefix P01 через настоящий display/adoption/publication до send.
При изолированном owner-прогоне допустим документированный setup через реальные
owner/broker boundaries, но его результат не обозначать прохождением диалога.
Imports/prefix используют только действительно достигнутые captures. Общие
fixtures выносить в library после появления точных bindings.

### Различить знание наблюдателя и приложения

Основная ветка: fake-provider выполняет действие и теряет ответ. Внешний test observer
видит эффект, приложение видит UNKNOWN. Его секретный журнал не попадает в контекст
модели или reducer до разрешённого ingress доказательств.

Отдельная ветка: передача могла состояться, но подтверждения эффекта нет даже у
доступного observer. Здесь проверяем отсутствие дополнительной отправки и сохранение
неопределённости, не утверждаем число созданных событий. Для неё нужен собственный
fault binding; LOST_RESPONSE_AFTER_EFFECT не моделирует этот случай.

### Шаги и наблюдения

| Шаг | Вход / вмешательство | Проверяемый результат |
|---|---|---|
| accepted-prefix | Выполнить подтверждение из P01 | Захвачены исходные command/intent и актуальные heads; transmission identity появится только при send в P02 |
| response-lost | После внешнего эффекта скрыть ответ от приложения | Provider log содержит reached transfer; приложение фиксирует OUTCOME_UNKNOWN и исходное reconciliation obligation |
| inquire | «Получилось добавить?» | Ответ явно сохраняет неизвестность; проверка статуса не создаёт новое действие |
| reconcile-unavailable | Разрешённая read-only сверка исходной identity возвращает unavailable | Obligation остаётся открытым; нет SUCCESS/ABSENT/FAILED без evidence и нет повторного create |
| restart | Остановить процесс, открыть то же хранилище и выполнить ограниченное число допустимых worker steps | Сохранились original intent/attempt/obligation и counters; sends delta=0 относительно первого transfer |
| late-evidence | Подать аутентифицированное свидетельство по исходной попытке | Сначала raw evidence; затем original resolver closure и актуальный semantic reduction; только после этого разрешено соответствующее продолжение |
| answer | При закрытом успехе: «Да, событие есть в календаре: завтра с 10:00 до 11:00.» — пример | Ответ связан с evidence; provider sends всё ещё без прироста |

Промежуточный ответ, пример: «Календарь не ответил. Пока не могу подтвердить,
что событие создалось. Ещё раз отправлять запрос не буду, чтобы не сделать дубль.»
Не обещать фоновую проверку до подключения её worker. Сохранять различие между
terminal accounting и разрешением продолжить модель; `RECOVERY_REQUIRED` сам
по себе не означает continuation-ready.

### Варианты и пределы проверки

| Вариант | Fault / ввод | Наблюдения и запреты |
|---|---|---|
| unknown-held | После первого transfer сверка недоступна | Открытое исходное obligation, отсутствие ложного успеха; zero additional create |
| restart-held | Crash после durable unknown, повторное открытие store | Исходные identities и unknown сохраняются; никакого reset бюджета/новой попытки |
| takeover-held | Новый законный worker и попытка stale worker | Новый worker не получает право blind retry; stale worker ничего не публикует; independent sends unchanged |
| late-confirmation | Верное evidence, затем resolver closure | Evidence append отдельно не разблокирует continuation; закрытие связано с исходной попыткой |
| conflicting-evidence | Несовместимое evidence до/после closure, обе serial orders | Исторические результаты не переписаны; typed hold/fault по reducer, будущий успех не берётся из последнего raw append |
| wrong-correlation | Неверные principal/provider/attempt или неподтверждённый источник | Отказ на authentication/correlation boundary; не закрывает исходное obligation |
| replay-or-replacement | Exact replay; затем changed payload и попытка заменить identity того же действия | Exact replay возвращает прежний результат; конфликт/недопустимая замена не добавляют transfer |

«Нет повторной отправки» — утверждение на конечном trace: после первого transfer
до завершения всех записанных шагов, restart/takeover/replay и фиксированного
числа worker ticks. В варианте фиксируются clock, tick count и budgets до запуска;
ожидание wall-clock timeout не является проверкой. Ни вечное отсутствие повтора,
ни отсутствие эффекта у настоящего провайдера этим тестом не доказываются.

Безопасная повторная передача с зарегистрированным доказательством и компенсация
с новой authority — отдельные технические ветки R16; запрет blind retry не запрещает
их автоматически. Они не получают PASS от описанных выше вариантов.

## Привязки к коду и недостающая работа

Пути относительно `src/chiplog` в соответствующем worktree. Существование функции
ниже означает место исследования/подключения, а не готовый transcript binding.

| Binding / observer | Основание в коде | Что требуется для P01/P02 |
|---|---|---|
| Canonical loop + display/adoption | R13 loop и существующий local-planning runner в main | Реальный calendar proposal/authority bridge; не регистрировать старые design operation names без ToolSpec |
| Atomic owner publication | R16 `capabilities/effects/application.py:prepare_transition`, `adapters/driven/effects_broker.py:BrokerEffectsStore.publish` | Registered broker path, точный participant manifest, cutoffs до/после commit; полный T01 требует R18 |
| Send и внешний observer | R16 `adapters/driven/effects_hermetic.py:HermeticEffectsProvider.emit_issued`, `transfers` | Broker-issued ticket, независимые exact bytes/count; leaf сам не выдаёт authority |
| Потеря ответа | Там же `LOST_RESPONSE_AFTER_EFFECT`, `HermeticResponseLost` | Подключить fault после реального transfer; отдельно дать неизвестную передачу без доказанного эффекта |
| Calendar result mapping | Старые design `calendar.create` / `lookup_by_effect_identity` | Generic hermetic effect ещё не calendar event API; нужен точный mapping payload/result/lookup и observer событий |
| Recovery closure/continuation | R14 `capabilities/agent_loop/recovery_domain.py:sealed_call_accounting`, `model_continuation_ready` | Durable frontier/resolver/reduction/restart adapters и observer исходного obligation; pure join не заменяет transaction-local проверку |
| Receipt и delivery | R17 `capabilities/agent_loop/delivery_preparation.py:prepare_completion`, `revalidate_delivery` | Owner-query evidence, acceptance/render bridge, exact recipient, последний send-boundary check |
| Runtime faults | Stateful format умеет описывать faults/prefix | Реальные hooks crash/restart/takeover и подтверждение их достижения; compiler не выполняет эти действия |

Если хотя бы одна необходимая привязка отсутствует, весь соответствующий вариант
получает `NOT_RUNNABLE` с перечнем причин. Успех owner-теста хранится отдельно и
не повышает статус полного транскрипта. Ошибка контракта — `FAIL`.

## Порядок подготовки и критерий готовности

1. P01/P02: уточнить версии owner contracts и mapping календаря; расписать
   варианты в формате TRANSCRIPTS.md, сохранив естественные реплики и видимые calls/results.
2. Подключить R16 owner-прогоны confirmed/unknown; отдельно сохранить evidence
   достижения provider boundary, не выдавая его за сквозной T01.
3. P03/P04: recovery и model-attempt paths; добавить P02 restart/late evidence.
4. P05/P06: scheduler и takeover; повторно проверить P02 с новыми worker fences.
5. P07/P08: ingress/delivery, authentication и channel parity.
6. R18: T01/T03 через production loop, stale T04 и сквозная цепочка
   schedule → effect → lost response → restart → evidence → delivery.
   Общая matrix completeness и A99/ingress inventory проверяются отдельно.

Перед признанием варианта runnable: конкретные schema/entrypoint/observer/fault
зарегистрированы; prefix исполняется; каждый fault действительно достигнут;
baseline, clock и budgets явны; asserts читают фактическое состояние; отрицательная
ветка проверяет независимый forbidden-effect observer. Артефакт запуска связывает
source/bundle, код и bindings digests и содержит expected/actual.
Скриптовые model fixtures проверяют orchestration; качество понимания речи настоящей
LLM требует отдельного прогона и не следует из deterministic PASS.

Авторский набор создан по этому плану. Подключение runtime, полный production-loop
прогон и перечисленные интеграционные гарантии остаются отдельной работой.
