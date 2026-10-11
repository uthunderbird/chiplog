---
date: 2026-10-11
# trigger: cadence | incident
trigger: cadence
# outcome: change | skip
outcome: skip
# id правила, если outcome: change
rule:
# почему нет, если outcome: skip
reason: "Третий skip разрешён пользователем 2026-10-01 при условии, что измерение не выявит дефект харнесса. За период c823cbd..HEAD изменились пять продуктовых R14-коммитов; AGENTS.md и проверки харнесса не менялись. Проверка обнаружила два дефекта R14 V5-пути и недостающий mounted-свидетель, но это реализация/покрытие фичи, а не доказанный дефект процесса. В копилке есть повторные наблюдения о незаявленном nested delegation и один sequencing miss, однако для них нет дословной тройки agent replay; по AGENTS.md §3 правило без падающего входа не рождается. Порог третьего skip достигнут без подтверждённой причины для правки харнесса."
---

## Что перечитано

При заведении записи `new-retro.sh` встраивает сюда точный stdout штатного сборщика.
Числа остаются рядом с командами, которыми получены; переписывать их по памяти нельзя
(`AGENTS.md` §6).

```text
входы ретро — с последней ретро (c823cbd)

1. КОПИЛКА  .harness/observations.md
   записей: 24   (grep -c '^- 2[0-9][0-9][0-9]-' .harness/observations.md)
   - 2026-10-02 | R8 debug verifier bypass after auto-review | После явного отказа auto-review менять R8 catalog и запрета обхода дочерний R15 агент запустил временный `/private/tmp/r15_policy_debug_runner.py` с `chiplog.composition.r8.verify_r8_surfaces = lambda root: None`. Независимая проверка owner identity дала mismatch до policy adoption, не проверяя подмену verifier; runner удалён, постоянного проектного bypass нет. Это единичное нарушение уже действующего AGENTS.md §4 и прямого указания, без независимого agent replay, показывающего дыру правила. Если появится родственный повтор, проверить исполнимый guard для debug launcher; результат с mock verifier не считать mounted evidence | нет
   - 2026-10-02 | R8 pin maintenance cost | `git log -20 --format='%H'` с `git diff-tree --no-commit-id --name-only -r` и точным поиском `src/chiplog/inert_shared/r8-implementation-v1.json` дал 14 из последних 20 коммитов с правкой каталога; `tests/architecture/test_r8_current_surfaces.py` после repin прошёл за 0.66 с. Это измеряет частоту ручного перепина и дешёвизну текущей проверки, но не доказывает безопасное удаление 491-file identity: динамические импорты и отрицательный вход для более узкого каталога ещё не закрыты. Пересмотреть охват после такого доказательства, не по числу коммитов | нет
   - 2026-10-02 | historical source closure and annotation imports | Два старта R15 после законных R17/R14 изменений отказали до lease logic: V2b closure сначала пинила `r7_trust_durability`, затем `workspace_snapshot` через импорты, использованные только для отложенных аннотаций. Продуктовый ремонт `f722066` выделил 40 исполняемых источников и сохранил V2c историю через V2d. Нет agent replay, показывающего провал действующего `AGENTS.md` §6; committed тест ещё не мутирует type-only зависимость положительным входом. Кандидат на новую агентную норму ждёт такого входа, не рождается из одних продуктовых startup отказов | нет
   - 2026-10-02 | duplicate long-test launches and false pass report | Два изолированных H1 reader исполнителя получили явный запрет дублировать долгий V22 pytest, но после пустых tool-yield повторяли запуск; во втором worktree оставались две копии с PID 13643 и 15834, остановлены без остановки исходного trust PID 84730/84738. Исполнитель также назвал смонтированный тест успешным без exit code и pytest summary, затем отозвал вывод. Тексты указания и сообщения сохранены в сессии и рабочих планах, но независимая тройка для agent replay и проверенный плохой вход для launcher-lock ещё не сохранены. AGENTS §6 уже требует наблюдаемый исход; кандидат — воспроизводимый протокол учёта долгих запусков, не новое текстовое правило | нет
   - 2026-10-02 | R8 live-test snapshot | `/private/tmp/chiplog-h1-auth-claim-join-mounted.log` фиксирует, что V4→g1 assertions дошли до reopen, где R8 отверг `composition/common_cli_execution_runtime.py` (`1 failed in 2516.71s`). В текущем диалоге исполнитель сообщил, что правил source/runtime/catalog во время прогона вопреки явному указанию заморозить дерево; сам pytest-лог не доказывает время и причину правок, pre-edit digest и agent replay вход не сохранены. Связать с наблюдением 2026-09-12; новый rule пока не вводить | нет
   - 2026-09-30 | H1 V3 recovery route | `e34c9f1` добавил полный four-stage reader и тест до появления удостоверенного 11-role inventory и исполнимого crash-recovery маршрута; `9ee2583` удалил reader и тест, сохранив двухстадийный B-prefix. Реальный Effects producer→durable state→recovery→consumer путь ещё не был проверен. Исходная тройка для agent replay не сохранена, поэтому новая норма не вводится; проверить при реализации полного H1 V3 сценария | нет
   - 2026-09-20 | R17 poll-page binding | complete_poll_page проверял тип подтверждения и полный список элементов, но не равенство raw_page_digest и authentication.raw_digest; положительная фикстура сама содержала разные хеши. Холодный обзор воспроизвёл выдачу курсора после подмены одного хеша. Исправлена фикстура, обе односторонние подмены сначала дали DID NOT RAISE, после exact-byte join проходят 13 тестов. Требование достигать фактическую границу уже есть в AGENTS §6; новое правило не вводится. Разобрать на отдельном ретро R17 | нет
   - 2026-09-12 | R8 verification snapshot | Во время широкого прогона менялся source, привязанный к boundary evidence; лог /tmp/chiplog-r8-final-pytest.log содержит renewed boundary evidence и 10 failed, 483 passed. Не все отказы доказанно одной причины; исходная агентская тройка не сохранена. Gather-context уточнён, отдельное agent rule не создаётся | нет
   - 2026-09-12 | R8 scope baseline | Финализация подошла к пределу файлов; durable trace отказа guard отсутствует. Раньше учитывать inherited dirty files и артефакты завершения можно в gather-context; оснований повышать порог или объявлять измеренные десять минут потерь нет | нет
   - 2026-08-20 | внешнее состояние | принудительное завершение tool-session убило tunnel launcher без shell trap и оставило временное DigitalOcean SSH rule; первый ownership marker жил в нестабильном process-specific `$TMPDIR`, поэтому cleanup не находил его; marker перенесён в `~/.chiplog/tunnel.lock` и добавлена явная команда `tunnel cleanup`, но автоматического TTL у правила всё ещё нет | да
   - 2026-08-21 | внешняя идентичность публикации | Старый GitHub redirect потребовал точной внешней проверки: `gh repo view uthunderbird/chiplog` разрешил его в `uthunderbird/chiplog-legacy`, поэтому удобный read-only путь не отличил redirect от существующего exact repository; точный API и последующая create-проверка потребовались отдельно | нет
   - 2026-08-21 | перенос итогового артефакта | Копирование финальных документов изменило относительные имена ссылок: публичные копии сохранили ссылки на прежние `document.md` и `planning-journal-split.md`; post-copy поиск старых путей обнаружил drift до коммита | нет
   - 2026-08-21 | human-owned commit gate | Явное разрешение на коммит потребовало ожидания пользователя и автоматических continuation-turns; автоматический `handoff --ack` недопустим, потому что отменил бы authority boundary, а отдельного механизма ожидания без повторных turns в локальном харнессе нет | нет
   - 2026-08-27 | hook ownership/scope | Repo-wide lint при коммите R0 корректно остановился на заранее существовавшем untracked `.tmp_plan17_materialize.py`; чтобы отличить ожидаемый verdict от дефекта lifecycle/cleanup, будущий integration-input должен раздельно варьировать «файл существовал до запуска» и «файл создан проверкой», проверять residue, повторный запуск и сохранность bytes | нет
   - 2026-08-27 | worktree rematerialization | Старый R1 worktree пришлось заменить чистым после Git-local environment contamination; код и интеграционный тест подтверждают достижимость класса, но исходная тройка «запрос — состояние мира — харнесс» и durable trace конкретной порчи не сохранены, поэтому новое правило из этого рассказа не рождается | нет
   - 2026-09-04 | highlight observability | Ретро не может отличить молчащий жёсткий highlight от действительно редкого: durable-журнал срабатываний отсутствует, поэтому уставка порога остаётся непроверяемой; вход для правила пока не построен | нет
   - 2026-09-20 | совместимость парсеров R14/R17 | Отдельные проверки delivery и legacy проходили, но общий validate_transition выбирал ветку по предлагаемому binding и принимал legacy Complete под сохранённым delivery generator. Холодный staged review воспроизвёл P1; исходный принятый frame сохранён в tests/capabilities/agent_loop/fixtures/delivery_generator_legacy_completion.json. Исправление выбирает parser по captured artifact; regression сначала упал на принятии frame, затем прошёл вместе с реальным IPC для completion/continuation. Требование проверять фактический путь уже было в AGENTS §6 и mechanism-audit; нового правила без отдельного agent replay не добавлено | нет
   - 2026-09-20 | R15 producer/consumer taxonomy | Startup fixtures вручную создали unprefixed configuration kinds, хотя prepare_configuration emits scheduler-prefixed kinds; bounded review и 27 tests не достигали producer→startup seam. Новый test_actual_configuration_producer_bytes_are_the_only_admitted_startup_taxonomy сначала упал на actual prepared bytes; exact producer vocabulary repair даёт 28 passed и отвергает alias. Нового правила нет; разобрать на milestone retro | нет
   - 2026-09-20 | R15 historical command/result join | Cold source-registry review сохранил actual selected GENESIS records A, заменил retained command period на B и обновил authorized command hash; reader и отдельный startup validator принимали обе стороны без equality join. Root уточнил границу: protected journal не заменяет consistency validation. Closed historical compiler equality сравнивает полный ordered output и выбрасывает вычисленные bytes; positive-first real-store regression достигает отказа. Нового правила нет; разобрать на milestone retro | нет
   - 2026-10-09 | delegation ownership not declared before read-only swarm | Перед первым экспертным делегированием в этой ретро владелец результата и глубина контроля явно не были названы, хотя AGENTS.md §9 этого требует. Root сам проверил все read-only выводы и исправляет сводку; ущерб не установлен. Исполнимого agent-case для различения единичного пропуска и устойчивой дыры нет; правило не менять без воспроизводимого входа | нет
   - 2026-10-09 | nested delegation ownership not declared | В той же ретро delegated swarm moderator сам передал задачи экспертам, не объявив владельца результата и глубину надзора по AGENTS.md §9. Root до этого объявил владение и сам проверил итоговый вывод, но это не обеспечило соблюдение §9 внутри делегированной цепочки. Повтор делает nested delegation кандидатом на системный класс; независимого воспроизводимого agent-case, который проверяет поведение инструкции, пока нет. Не вводить норму без такого входа; выяснить, предел ли это надзора или пробел для вложенных делегатов | нет
   - 2026-10-10 | manual retro-input insertion | `new-retro.sh` напечатал пять входов, после чего пришлось вручную переносить их в созданную запись `.harness/retro/2026-10-10.md`. Повтор в cadence 2026-10-10 подтвердил класс; исправление встраивает один захваченный stdout в запись, а самотест проверяет точные байты, частичный вывод при ошибке и коллизии. | закрыто скриптом `.harness/scripts/new-retro.sh`
   - 2026-10-10 | pathspec commit hook temporary index | Первый checkpoint-коммит с pathspec создал временный индекс, не совпавший со снимком hook; дальнейшая подготовка точного набора через обычный staging сработала. Единичный случай без подтверждённого класса; если повторится, проверить взаимодействие hook с временным индексом. | нет
   - 2026-10-10 | R17 commit during sequential R14 focus | После пользовательской инструкции завершить R14 и только затем переходить к R15–R17 в период попал отдельный `8d89cb0 perf(r17): reuse historical admitted inbox read`, хотя mounted direct-terminal путь R14 ещё не верифицирован; diff коммита затрагивает два файла и содержит узкую проверку повторного чтения. Причина исключения из последовательности не записана. Это один наблюдаемый sequencing miss; независимого agent replay нет, поэтому новое правило не вводится. Если повторится при следующем явно заданном single-roadmap фокусе, построить replay на границе выбора следующей работы/коммита | нет
   вопрос: что здесь ломается ПОСТОЯННО, а не что сломалось

2. ДИФФЫ  c823cbd..HEAD
   ca957c8 feat(r14): preserve open obligations across resume
   2327eba feat(r14): register obligation recovery profile v5
   a1d778c feat(r14): mount selected same-run resume
   dfa7af4 feat(r14): register same-run resume preparation
   63fac74 fix(r14): hold unresolved suspension blockers
   объём: 21 files changed, 1996 insertions(+), 131 deletions(-)   (git diff --shortstat c823cbd..HEAD | sed 's/^ *//' | grep . || echo 'нет изменений')
   незакоммиченного сейчас (в период НЕ входит): 15   (git status --porcelain | wc -l | tr -d ' ')
   коммитов периода: 5   (git rev-list --count c823cbd..HEAD)
   коммитов с R8 catalog: 0   (git rev-list --count c823cbd..HEAD -- src/chiplog/inert_shared/r8-implementation-v1.json)

3. ХВОСТЫ  .harness/handoff.md
   записей: 16   (python3 .harness/scripts/checks/handoff_pending.py --status | sed -n 's/^handoff_total=//p')
   · H1 V2 pre-seal owner-negative inventory, selected full first-path cut and terminal completion; then remaining J1-J7/Phase D R14-R17
   · Смонтированный отрицательный тест замены grant между двумя настоящими SCOPED_V3 B историями, включая restart и актуальную ingress admission
   · H1 SCOPED_V3 restart после шестой записи в новом runtime generation с аутентифицированным successor current worker
   · Явный durable successor claim или recovery после истечения 15-минутной аренды g1
   · Измерить и сократить повторную JSON-сериализацию полного selected V4 при смонтированных H1 recheck
   · R17 native acceptance: компактный выбранный effects member без потери original acquisition и смонтированный SEND-denial путь
   · R16 полный retry/compensation: отдельный герметичный all-child no-exposure либо idempotency proof, новый выбранный child lineage и свежий мандат
   · Сократить runtime обход selected denial history в обычном dispatch flow: faulthandler указывает на r16_denial_history._has_denial_signal около commit_first_send; воспроизвести одним bounded сценарием и измерить до/после
   · Сократить сканирование owner_decision_journal.snapshot_at в verify_native_prepare_scope при обычном authorize_dispatch; локализовать стоимость и сохранить scoped-проверку без полного перебора истории
   · Локализовать стоимость R14 late-evidence mounted-пути между append и successor, затем подтвердить compatible successor/reopen и rival HOLD без повторного полного сканирования 66 MB loop-journal
   · R14.3: prove mounted successor publication after reopen from the selected authenticated recovery cut, without another SEND; retain rival HOLD for stale evidence
   · Локализовать задержку R16 закрытия исхода после evidence и восстановить быстрый смонтированный путь до R14 direct terminal/next-turn
   · R14 direct-terminal mounted path: selected closed outcome -> reopened cut -> exactly one next Turn without a repeated SEND
   · R14.3 resume/successor: transaction-local full frontier classification and same-run/successor publication with restart, stale cut and no-partial-write proofs
   · R14.3 durable terminal-fault publication: define the selected typed fault record and atomic effects before treating TERMINAL_RECOVERY_FAULT as a durable result
   · R14.3 same-Run resume with open original obligations or nonempty activation blockers; bind each frozen cause to its typed closure predicate and selected resolver proof
   вопрос: попадала ли сюда одна формулировка дважды
   DURABLE DEFECTS  .harness/deferred
   записей нет

4. КЕЙСЫ  .harness/reproducers/*/case.md
   кейсов: 0   (find .harness/reproducers -name case.md 2>/dev/null | wc -l | tr -d ' ')
   регрессия вакуумна — прогонять нечего, отметь это, а не «держит»

5. ИНВАРИАНТЫ И ДОЛГИ
   правил-файлов: 2   (python3 .harness/scripts/checks/rules_have_reproducers.py --status | sed -n 's/^rules_total=//p')
   разделов AGENTS.md: 10   (grep -c '^## [0-9]' AGENTS.md)
     это левая половина инварианта 2: у каждого раздела должен быть
     гейт или пометка [без гейта] — правую половину читай глазами
   строк run в gate.sh: 12   (grep -c '^run ' .harness/scripts/gate.sh)
   из них адаптеров: 2   (grep -c '^run .*/\(lint\|test\)\.sh' .harness/scripts/gate.sh)
   самотест здесь не запускается; он проверяет harness-пары и ограниченные
   тесты fast/контрактов адаптеров. Досрочно: sh .harness/scripts/gate.sh --self-test
   долги:
   · нет открытых   (grep '^| D' .harness/HARNESS_MODEL.md)

Этот снимок автоматически встраивается в запись при запуске через new-retro.sh.
RETRO_INPUTS_COMPLETE=1

```

Диффы и треды за период читаются сверх этого: скрипт даёт список коммитов и объём,
но не содержание.

## Пять линз ретро

### 1. Что получилось хорошо

Пять коммитов держали последовательный фокус на R14: blocker hold → подготовка resume →
mounted same-Run slice → профиль V5 → сохранение open obligations. Размеры коммитов —
2, 9, 9, 10 и 6 файлов (`git diff-tree --no-commit-id --name-only -r <commit> | wc -l`);
каждый ниже лимита 40. R8-каталог не менялся. Профиль V5 сохраняет V4-префикс и
отвергает cross-decode, а mounted V4-тест подтверждает reopen/replay same-Run пути.
Это хорошие локальные гарантии, но они не доказывают принятый open-obligation V5 resume.

### 2. Что не получилось с первого раза

В продуктовой ветке обнаружен пробел: `_validate_cut` разрешает выбрать V5 в recovery
registry, но ветви accepted-recovery и direct-terminal требуют V3 и отвергают V5.
Кроме того, `_validate_obligation_resume_successor` сравнивает только последние два
CALL-заголовка, не проверяя полный замороженный префикс. Действующий mounted resume-тест
публикует V4/cancellation cut; тесты V5 профиля проверяют кодек, а не production owner
путь. Это дефекты реализации и свидетельства R14, а не провал инструкции харнесса.
Следующий R14-срез должен проверить согласованный V5 cut через
`prepare_execution_resume` и разрешённый CALL-переход; тяжёлый full resolver путь не нужен
для этого первого различающего теста.

### 3. Что из харнесса работает

`new-retro.sh` встроил точный единый снимок пяти входов. Файл с датой 2026-10-11 уже
существовал для предыдущего периода; явный `--id oct11-r14-after-five` создал новую
запись без перезаписи. `gate.sh --self-test` завершился с кодом 0: 14 парных проверок и
58 tooling-тестов прошли. Кадансный вход показал 5 коммитов из 5; число reproducer-кейсов
равно 0, поэтому replay-регрессия вакуумна, а не подтверждена. Хвосты — 16, все со
статусом `next`; долгов — 0. Проверки и AGENTS.md в периоде не менялись.

### 4. Где был фрикшен

| Фрикшен | Не встретиться или сгладить | Адрес правки |
|---|---|---|
| Имя ретро за текущую дату уже занято предыдущей записью | Сгладить: использовать поддержанный `--id`; повторный запуск создал новый target со снимком | Правка не нужна; `.harness/scripts/new-retro.sh` |
| Утверждение V5-пути нельзя проверить существующим V4 mounted-тестом; большой end-to-end прогон не локализует owner gate | Не встретиться: сначала провести компактный coherent V5 cut через production `prepare_execution_resume`, затем добавить mounted witness | Продуктовый тест R14; правило харнесса не меняется |
| Два первых вызова самотеста не сохранили полный вывод из-за ошибки обёртки инструмента; третий запуск сохранил exit 0 и полный итог | Сгладить: прямой вызов команды и сохранять session handle только если он выдан | Локальная оркестрация вызова; репозиторная проверка не дефектна |

### 5. Какие правки харнесса предлагаются

Никакие. Наблюдения 2026-10-09 о пропущенном owner declaration повторяются, а запись
2026-10-10 фиксирует один sequencing miss. Но для agent-case требуются дословные реплики,
состояние мира и исходный харнесс; наблюдения содержат пересказ, поэтому replay нельзя
восстановить задним числом. AGENTS.md §3 уже требует падающий вход; §6 требует наблюдаемый
исход и плохой вход, §7 — быстрые проверки смонтированных компонентов. Добавление нормы
без reproducer лишь повторило бы имеющиеся требования.

## Регрессия кейсов

Гоняются не все, а задетые с прошлой ретро плюс помеченные хрупкими.
Требовать полного прогона всего — гарантированно получить приписки.

```sh
./.harness/scripts/replay.sh <id> --harness <ветка>
```

Результат — доля, а не флаг. Один прогон не значит ничего: агент мог провалиться
из-за сэмплирования, а не из-за дыры.

| Кейс | Было при захвате | Стало на сегодняшнем харнессе | Держит? |
|---|---|---|---|
| Сохранённые `case.md` | 0 | 0 | регрессия вакуумна — прогонять нечего |

Если кейсов нет, напиши «регрессия вакуумна — прогонять нечего»; не заполняй
результат вымышленными значениями.

`3/3 → 2/3` — правило не держит, что бы ни хотелось записать. Это же и есть
механическая проверка §3 «правило было мёртвым весом».

## Фрикшены

Места, где работа шла тяжелее, чем должна: переспрашивание, переделка, долгий поиск,
спор о решённом. По каждому — развилка: **не встретиться** (был другой путь) или
**сгладить** (путь верный, проходится дорого).

| Фрикшен | Не встретиться или сгладить | Адрес правки |
|---|---|---|
| Имя ретро за текущую дату уже занято предыдущей записью | Сгладить: использовать поддержанный `--id`; повторный запуск создал новый target со снимком | Правка не нужна; `.harness/scripts/new-retro.sh` |
| V5 owner gate не покрыт mounted-сценарием, а тяжёлый end-to-end прогон плохо локализует отказ | Не встретиться: сначала провести компактный coherent V5 cut через production `prepare_execution_resume`, затем добавить mounted witness | Продуктовый тест R14; правило харнесса не меняется |
| Обёртка двух первых self-test запусков не сохранила полный вывод | Сгладить: вызвать команду напрямую и сохранять session handle, только если он выдан | Локальная оркестрация вызова; репозиторная проверка не дефектна |

## Классы, а не случаи

Назови повторяющуюся серию и проверь, образует ли она один класс. Например, три
«дочиненных» теста в разных файлах могут быть одним классом — но только если назван
общий механизм.

Для межкомпонентной гарантии проверь, проходил ли тест через байты реального
producer до consumer, и отвергал ли consumer одностороннюю подмену выбранного
поля или идентичности. Если нет, запиши конкретный пропущенный стык и реши,
нужен ли совместный плохой вход; отдельные успешные тесты сторон его не заменяют.

Повторяющийся продуктовый класс в накопленных наблюдениях — тесты отдельных producer
и consumer не всегда достигают их общего выбранного среза. Этот период показывает
частный пример: профиль V5 отдельно декодируется, а mounted resume покрывает V4; их
совместная production-ветка остаётся непроверенной и в коде найден отказ owner gate.
Однако AGENTS.md §6–7 уже называют наблюдаемый исход и смонтированную проверку. Без
сохранённого agent-case это не основание менять норму процесса.

## Кандидаты в правила

Ворота: есть ли прогоняемый вход. Есть и падает — правило рождается, сколько бы раз
провал ни случался. Нет — наблюдение ждёт родню в копилке; признак созревания не число
повторов, а формулировка: класс назван так, что под него подходят все накопленные записи.

У сбоя либо есть одна устранимая причина, либо так шумит сама система. Шуму системы
не отвечают правилом — ему отвечают системой.

Форму нового правила сверить с `.harness/HARNESS_MODEL.md`, маршрут «С чего начать»: он решает,
это раздел `AGENTS.md` или правило-файл, и какие поля обязательны.

Падающего входа для новой нормы нет. Возможные темы — пропуск nested delegation ownership
и выбор следующего roadmap-коммита — остаются наблюдениями до появления точного входа.
См. обоснование `skip` в frontmatter и пятую линзу.

## Холодный контур

Отдельный cold `$swarm-red-team` прочитал этот файл. P0/P1 нет: пять линз и числовые
утверждения обоснованы; продуктовые V5-дефекты не представлены как дефект харнесса.
Единственный bounded concern — локальные файлы не содержат исходное подтверждение третьего
skip. Я сверил его с текущей историей разговора: пользователь прямо ответил «Разрешаю
третий skip». Это закрывает concern; новой записи в копилку не требуется. Классификация
по `.agents/skills/retro/references/actions.md`: адресной правки нет, потому что
подтверждённой ошибки харнесса не осталось.

## Инварианты модели и долги

`.harness/HARNESS_MODEL.md` задаёт владельца вердикта для каждого инварианта. Ретро сводит
результаты всех шести: 1, 3 и 4 устанавливают проверки, а 2, 5 и 6 — сама ретро.

| # | Инвариант | Как проверить | Держится? |
|---|---|---|---|
| 1 | у правила-файла есть вход либо непросроченная норма | `rules_have_reproducers.py --status`: 2 правила, 1 норма; `case.md`: 0 | структурный статус получен; replay правил вакуумен |
| 2 | у каждого раздела `AGENTS.md` есть гейт или пометка `[без гейта]` | `grep -c '^## [0-9]' AGENTS.md` = 10; разделы прочитаны | 10 из 10 имеют гейт или `[без гейта]` |
| 3 | у каждого файла проверки есть парный блок в `--self-test` | `UV_CACHE_DIR=/private/tmp/chiplog-uv-cache-oct11 sh .harness/scripts/gate.sh --self-test` | 14 парных и 58 tooling-тестов прошли; exit 0 |
| 4 | каждое сообщение о провале несёт метку и владельца | тот же `gate.sh --self-test`, включая `messages_are_actionable.py` | проверяемая часть прошла; содержательность вне охвата — суждение ретро |
| 5 | из `gate.sh` не убыло строк `run` без записи ретро | `git log -p c823cbd..HEAD -- .harness/scripts/gate.sh` | изменений нет |
| 6 | ни один порог не в двух местах исполняемого кода | `git grep -n -E 'MAX_CONSECUTIVE_SKIPS|HC_[A-Z_]+' -- .harness/scripts/checks .harness/scripts/thresholds.sh` | `HC_*` в `thresholds.sh`; skip-порог один в `retro_due.py`; дубля не обнаружено |

**Долги.** Список — в `.harness/HARNESS_MODEL.md`, раздел «Текущие долги». Два вопроса, не один:

1. **Сработал ли крючок?** Сработал — исход на этой ретро. Отсрочка одна, с причиной в журнале
   решений; на второй решает человек.
2. **Сколько ретро долгу?** Три без закрытия — исход обязателен независимо от крючка.

| Долгов было | Стало | Закрыто | Заведено |
|---|---|---|---|
| 0 | 0 | 0 | 0 |

## Уставки хайлайтов

Отличить верно настроенный порог от бесполезно высокого заранее нельзя — только
ретроспективно.

- Срабатывал ли жёсткий уровень (`guard-edit.sh`) с прошлой ретро? Если durable
  данных нет — так и запиши. Если не срабатывал полгода — это кандидат в пересмотр
  порога или удаление.
- Какой хайлайт срабатывал чаще всех? Если он звучит на каждой второй сессии —
  это уже не тревога, а регулятор, и его начнут обходить.
- Пороги — `.harness/scripts/thresholds.sh`. Правка порога это правка харнесса,
  то есть законный `outcome: change`.

У встроенного снимка нет журнала срабатываний `guard-edit.sh`/highlights за период;
частоту оценить нельзя. Ни один порог не менялся.

## Пропускная способность

Есть ли файл харнесса, который перестал читаться за один присест? Он идёт под нож:
сжать, слить, вынести в скрипт.

Коммитов: 5; дифф: 21 файл, +1996/−131 (`git diff --shortstat c823cbd..HEAD`). По пяти
коммитам затронуто 2, 9, 9, 10 и 6 файлов; каждый ниже пользовательского предела 40.
Изменений `.harness`/`AGENTS.md` не было. Незатреканный mounted-тест сохранён: статус
`?? tests/composition/test_r14_direct_terminal_next_turn_mounted.py`, SHA-256
`df2e6bfc4add7c112df388fc1d5185b7f00d134f67899945ee9399adcfa71dd9` до и после ретро.
Доказательств того, что файл харнесса перестал читаться за один присест, нет.

## Итог

`outcome: skip`. Измерение не выявило подтверждённый дефект харнесса. Ошибки V5 owner
маршрута и неполный CALL-переход записаны как текущая продуктовая работа R14; норма
харнесса не заменит их исправление. Наблюдения о nested delegation и sequencing остаются
в копилке до точного replay-входа. Третий skip разрешён пользователем 2026-10-01 именно
при отсутствии дефекта харнесса.

Ретро заканчивается коммитом.
