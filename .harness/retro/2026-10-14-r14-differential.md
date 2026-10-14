---
date: 2026-10-14
# trigger: cadence | incident
trigger: cadence
# outcome: change | skip
outcome: skip
# id правила, если outcome: change
rule:
# почему нет, если outcome: skip
reason: Шесть коммитов локально сократили повторные R14 history/replay derivations, но подтверждённая задержка остаётся продуктовой и отдельного падающего agent replay на дыру харнесса нет. Действующие AGENTS.md §§6–7 и 11 уже требуют наблюдаемого исхода, отложенных тяжёлых проверок и локальных решений по реальным свидетельствам; новую норму без такого входа не добавляем.
---

## Что перечитано

При заведении записи `new-retro.sh` встраивает сюда точный stdout штатного сборщика.
Числа остаются рядом с командами, которыми получены; переписывать их по памяти нельзя
(`AGENTS.md` §6).

```text
входы ретро — с последней ретро (e335548)

1. КОПИЛКА  .harness/observations.md
   записей: 26   (grep -c '^- 2[0-9][0-9][0-9]-' .harness/observations.md)
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
   - 2026-10-12 | test-run phase overstated in handoff | Handoff №28 говорит, что targeted pytest selector «did not start» за 268.74 s, но доступная запись не подтверждает фазу остановки. Точный поддерживаемый вывод — mounted попытка была прекращена без функционального verdict; не утверждать, что selector или тест не стартовал без сохранённого trace. AGENTS §6 уже требует наблюдаемого результата. Это единичная агентская формулировка без самостоятельного replay; пока наблюдение, не новое правило. Исправить Handoff №28 и при повторе сохранить лог/фазу до остановки | нет
   - 2026-10-13 | handoff identifier reuse after close | `.harness/scripts/handoff.sh --close 30` удалил последнюю запись, после чего `.harness/scripts/decide.sh` снова выдал ID `30`. Это воспроизводит нарушение AGENTS.md §10 «Идентификаторы правил и долгов не переиспользуются» для журнала handoff; `handoff.py:add` вычисляет следующий ID только из текущих записей. Один вход выявил конкретную дыру, класс и минимальный фикс надо проверить в отдельном harness-срезе; сейчас не расширять R14 работу. | нет
   вопрос: что здесь ломается ПОСТОЯННО, а не что сломалось

2. ДИФФЫ  e335548..HEAD
   8b2bc24 perf(r14): reuse authenticated native history commands
   e454b4d perf(r14): avoid duplicate named readiness validation
   fd1d99b perf(r14): reuse authenticated loop journal cut
   1e761bc perf(r14): reuse validated terminal decision entry
   b062130 fix(r14): reuse authenticated V3 history snapshot
   1a7670e docs(r14): record short mixed profile
   объём: 11 files changed, 1185 insertions(+), 78 deletions(-)   (git diff --shortstat e335548..HEAD | sed 's/^ *//' | grep . || echo 'нет изменений')
   незакоммиченного сейчас (в период НЕ входит): 17   (git status --porcelain | wc -l | tr -d ' ')
   коммитов периода: 6   (git rev-list --count e335548..HEAD)
   коммитов с R8 catalog: 0   (git rev-list --count e335548..HEAD -- src/chiplog/inert_shared/r8-implementation-v1.json)

3. ХВОСТЫ  .harness/handoff.md
   записей: 19   (python3 .harness/scripts/checks/handoff_pending.py --status | sed -n 's/^handoff_total=//p')
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
   · Локализовать задержку R16 закрытия исхода после evidence и восстановить быстрый смонтированный путь до R14 direct terminal/next-turn
   · R14.3 resume/successor: transaction-local full frontier classification and same-run/successor publication with restart, stale cut and no-partial-write proofs
   · R14.3 durable terminal-fault publication: define the selected typed fault record and atomic effects before treating TERMINAL_RECOVERY_FAULT as a durable result
   · R14.3 same-Run resume with open original obligations or nonempty activation blockers; bind each frozen cause to its typed closure predicate and selected resolver proof
   · Reduce the original-resolution mounted witness to bounded, focused fixtures that still prove closure, selected outcome, reduction and reopen; rerun end-to-end only after the fixture no longer expands journals beyond the execution budget
   · Reduce the mounted negative successor HOLD test to a focused witness under 180 seconds, while preserving real authenticated open-obligation evidence and no-publication assertion
   · Сделать смонтированный direct-terminal→reopen→next-turn прогон bounded и повторить его с двумя member seal; текущий прогон остановлен после 2 минут без результата, последние marks: accounting-cut-2 и writer-decision-guard
   · После проходного горизонтального сценария измерить рост времени/памяти при увеличении seals и history в V3 decode и historical suffix; оптимизировать повторный full-history проход только если профиль подтвердит стоимость. Не считать A54 bounds лимитом истории.
   · Снять низкообъёмный writer trace на следующем естественном проходе R14: один медленный accepted-call с раздельными CPU/IPC/gate/suffix и победителем deadline min
   вопрос: попадала ли сюда одна формулировка дважды
   DURABLE DEFECTS  .harness/deferred
   записей нет

4. КЕЙСЫ  .harness/reproducers/*/case.md
   кейсов: 0   (find .harness/reproducers -name case.md 2>/dev/null | wc -l | tr -d ' ')
   регрессия вакуумна — прогонять нечего, отметь это, а не «держит»

5. ИНВАРИАНТЫ И ДОЛГИ
   правил-файлов: 2   (python3 .harness/scripts/checks/rules_have_reproducers.py --status | sed -n 's/^rules_total=//p')
   разделов AGENTS.md: 11   (grep -c '^## [0-9]' AGENTS.md)
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

## Пять линз

| Линза | Наблюдение за период | Вывод |
|---|---|---|
| Что получилось хорошо | Шесть коммитов остались внутри R14.6. Профиль последовательно отверг owner-prefix scan как существенную стоимость на измеренном cut, отделил локальное повторное вычисление от независимой проверки источника и не стал называть неизвестный выигрыш speedup. | Сохранять дифференциальный ход: фиксировать измеренный вход и текущность источников, затем оптимизировать только конкретную локальную повторность, не удаляя отдельные authority/source/head checks. Это уже соответствует AGENTS.md §11. |
| Что не получилось | Длинные mounted-прогоны не дали полного функционального verdict; измерения одного sealed cut не объясняют multi-seal writer STALE. Восстановленный по содержимому старый fixture был отвергнут текущим runtime до целевой операции. | Не переносить выводы между разными cuts и не выдавать восстановленные bytes за действующий источник. Это продуктовые/измерительные проблемы; агентского нарушения инструкции не подтверждено. |
| Что из харнесса работает | В периоде R8-каталог изменён в 0 из 6 коммитов; checkpoint путь сохранял узкие тесты без repin. `gate.sh --self-test` завершился 58 passed; сообщения о проверках и правила прошли checks. | Срезы и наблюдаемые результаты удерживались без полного регрессионного прогона. Ноль reproducer-кейсов означает вакуумную проверку поведения агента, а не доказательство, что инструкции держатся. |
| Где был friction | Диагностическая стоимость: прерванные длинные прогоны и непригодность восстановленного fixture. | Длинный standalone повтор **не встречать**: следующий differential trace присоединять к функционально необходимому mounted пути. Fixture-путь **сгладить** продуктово: сохранять целостную runtime/custody identity или строить новый валидный fixture; content hash сам по себе недостаточен. |
| Какие изменения харнесса нужны | Для задержек R14 найдено несколько конкретных reader costs, но нет падающего agent replay, показывающего, что действующие нормы привели к ошибочному выбору или отчёту. | Новое правило или дополнительный процессный барьер не вводить. Один непригодный восстановленный fixture записан как наблюдение для будущей родни. |

### Границы измерений

Профиль короткого mounted mixed-V3 selector прошёл за 110.48 s с профайлером; это другой workload, чем двух-seal writer отказ. На сохранённом read-cut `_pending` повторно декодировал те же три payload 21 раз и дал 3.140 s включённого raw-decode CPU, но вызовы пришли из разных семантических валидаторов; это вклад, не весь bottleneck и не основание для общего кэша. Последний профиль одного live one-seal selector показал 11,867 вызовов `RecoveryDTO.canonical_bytes` и 18.92 s включённого времени, но не доказывает, что сериализовались одинаковые DTO или что результат можно переиспользовать между границами. Экспонента и GIL contention не установлены. Следующий полезный замер — только на обязательном функциональном witness, с CPU/временем/размером по границам и slack до deadline.

Межкомпонентная граница остаётся незакрытой: новые тесты этих шести коммитов проверяют локальную устойчивость, чужой runtime и повторную валидацию unnamed-кандидата; они не заменяют mounted multi-seal producer→consumer history и cross-seal tamper, требуемые R14.6. Это открытая продуктовая работа, не утверждение о завершённом контракте.

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
| — | регрессия вакуумна — `find .harness/reproducers -name case.md` дал 0; replay запускать нечего | — | вакуум |

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
| Повторный профиль исторического cut был заменён на восстановленный fixture, который совпадал по файловым атрибутам, но текущий runtime отверг его на общей проверке выбранной accounting source. Какое поле не совпало — не установлено. | Сгладить: до измерения проверить, что текущий reader принимает fixture; при отказе сначала локализовать конкретное несовпавшее поле. Не считать одинаковые file hashes доказательством валидного cut. | `.harness/observations.md`; диагностический след в `design-docs/R14-PERFORMANCE-DIAGNOSIS.md` |
| Долгие mounted запуски несколько раз закончились без verdict и потребовали повторного анализа сохранённых профилей. | Не встречать: не запускать тот же multi-minute selector только ради ещё одного профиля; присоединить низкообъёмный trace к следующему необходимому R14.6 функциональному прогону. | План следующего измерения в `design-docs/R14-PERFORMANCE-DIAGNOSIS.md`; нового правила нет |

## Классы, а не случаи

Назови повторяющуюся серию и проверь, образует ли она один класс. Например, три
«дочиненных» теста в разных файлах могут быть одним классом — но только если назван
общий механизм.

Для межкомпонентной гарантии проверь, проходил ли тест через байты реального
producer до consumer, и отвергал ли consumer одностороннюю подмену выбранного
поля или идентичности. Если нет, запиши конкретный пропущенный стык и реши,
нужен ли совместный плохой вход; отдельные успешные тесты сторон его не заменяют.

Класс ошибок агента не установлен: все шесть коммитов тематически остаются на R14;
изменения — локальные оптимизации и их тесты. Систематического обхода AGENTS.md,
ложного сообщения о полном pass или переноса на другой R-милстоун в этом периоде
не обнаружено. Продуктовые пробелы R14.6 остаются открытыми в roadmap.

## Кандидаты в правила

Ворота: есть ли прогоняемый вход. Есть и падает — правило рождается, сколько бы раз
провал ни случался. Нет — наблюдение ждёт родню в копилке; признак созревания не число
повторов, а формулировка: класс назван так, что под него подходят все накопленные записи.

У сбоя либо есть одна устранимая причина, либо так шумит сама система. Шуму системы
не отвечают правилом — ему отвечают системой.

Форму нового правила сверить с `.harness/HARNESS_MODEL.md`, маршрут «С чего начать»: он решает,
это раздел `AGENTS.md` или правило-файл, и какие поля обязательны.

## Инварианты модели и долги

`.harness/HARNESS_MODEL.md` задаёт владельца вердикта для каждого инварианта. Ретро сводит
результаты всех шести: 1, 3 и 4 устанавливают проверки, а 2, 5 и 6 — сама ретро.

| # | Инвариант | Как проверить | Держится? |
|---|---|---|---|
| 1 | у правила-файла есть вход либо непросроченная норма | `rules_have_reproducers.py --status`; check в `gate.sh --self-test` | 2 правила; norm-ревизия актуальна; гейт прошёл |
| 2 | у каждого раздела `AGENTS.md` есть гейт или пометка `[без гейта]` | `grep -n '^## [0-9]' AGENTS.md` и ручная сверка | 11 разделов; носитель есть у каждого |
| 3 | у каждого файла проверки есть парный блок в `--self-test` | `sh .harness/scripts/gate.sh --self-test` | 12 `run`-строк; self-test прошёл, включая парные входы |
| 4 | каждое сообщение о провале несёт метку и владельца | `python3 .harness/scripts/checks/messages_are_actionable.py` | Exit 0 |
| 5 | из `gate.sh` не убыло строк `run` без записи ретро | `git diff e335548..8b2bc24 -- .harness/scripts/gate.sh` | Пустой diff; gate.sh не менялся |
| 6 | ни один порог не в двух местах исполняемого кода | `grep -R -n -E '^[[:space:]]*(HC_[A-Z0-9_]+|RETRO_EVERY|MAX_CONSECUTIVE_SKIPS)[[:space:]]*=' .harness/scripts --include='*.sh' --include='*.py'` | 8 `HC_*` thresholds определены один раз в `thresholds.sh`; `RETRO_EVERY` и `MAX_CONSECUTIVE_SKIPS` — по одному разу в `checks/retro_due.py`. Их потребители читают именованные значения. Это аудит объявлений, не поиск всех дублирующих numeric literals во всём коде. |

**Долги.** Список — в `.harness/HARNESS_MODEL.md`, раздел «Текущие долги». Два вопроса, не один:

1. **Сработал ли крючок?** Сработал — исход на этой ретро. Отсрочка одна, с причиной в журнале
   решений; на второй решает человек.
2. **Сколько ретро долгу?** Три без закрытия — исход обязателен независимо от крючка.

| Долгов было | Стало | Закрыто | Заведено |
|---|---|---|---|
| 0 | 0 | — | — |

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

Нет durable-журнала срабатываний, поэтому частоту жёсткого уровня и самый частый
highlight за период установить нельзя. Это уже записано наблюдением от 2026-09-04
«highlight observability». В текущем дереве HC_FILES_HARD равен 40 и объявлен
в одном месте; это не говорит о частоте его прошлых срабатываний.

## Пропускная способность

Текущие опорные файлы харнесса — AGENTS.md (93 строки), HARNESS_MODEL.md (130),
retro/TEMPLATE.md (129), observations.md (41) и handoff.md (109); при чтении этой ретро
проблемы восприятия не обнаружены. `R14-PERFORMANCE-DIAGNOSIS.md` вырос до 1,759 строк,
но это продуктовая диагностическая запись, а не файл харнесса; в этой ретро его не
перестраиваю и не объявляю нечитаемым без наблюдаемого сбоя.

## Итог

`outcome: skip`: продуктовые задержки требуют продолжения R14.6 работы, но не выявили воспроизводимую дыру процесса или агента. Действующие нормы уже требуют локального среза, измеренного результата и отложения тяжёлых проверок; добавлять новую формулировку сейчас означало бы закрепить правило без падающего входа. Непригодность восстановленного current-code fixture записана наблюдением ниже.

### Наблюдение без класса правила

`2026-10-14 | R14 restored fixture rejected before target read |` Старый `pytest-9070` fixture удалили и восстановили из 44-файловой копии; размер, SHA-256, режим и mtime файлов совпали. Текущий runtime остановил запуск до целевого чтения с общей ошибкой `selected native accounting source differs from historical cut`. Проверка включает несколько независимых связей source/stamp/tenant/head/authority/preparation/lineage/inventory/seal/fingerprint; лог не показывает, какая именно не совпала. Поэтому причина отказа неизвестна; установлен только факт, что совпавшие файловые атрибуты не дали принятого cut. Профиль по артефакту недействителен; следующую диагностику вести на новом валидном fixture или сначала локализовать несовпавшее поле. Единичное продуктово-диагностическое наблюдение, не агентное правило. Источник: `design-docs/R14-PERFORMANCE-DIAGNOSIS.md`, раздел «Differential follow-up: saved-cut validity and live one-seal attribution» | нет

Перед коммитом пересказать все 19 записей handoff по одной строке.
