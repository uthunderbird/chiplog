---
date: 2026-10-15
# trigger: cadence | incident
trigger: cadence
# outcome: change | skip
outcome: change
# id правила, если outcome: change
rule: .harness/scripts/checkpoint.py
# почему нет, если outcome: skip
reason:
---

## Что перечитано

При заведении записи `new-retro.sh` встраивает сюда точный stdout штатного сборщика.
Числа остаются рядом с командами, которыми получены; переписывать их по памяти нельзя
(`AGENTS.md` §6).

```text
входы ретро — с последней ретро (4d59376)

1. КОПИЛКА  .harness/observations.md
   записей: 28   (grep -c '^- 2[0-9][0-9][0-9]-' .harness/observations.md)
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
   - 2026-10-14 | R14 restored fixture rejected before target read | Старый `pytest-9070` fixture восстановили из 44-файловой копии; размер, SHA-256, режим и mtime файлов совпали. Текущий runtime отверг cut до целевого чтения с общей ошибкой `selected native accounting source differs from historical cut`. Валидатор проверяет source bytes/stamp, tenant/head/authority, preparation bindings, lineage, inventory, seal и fingerprint; лог не локализует конкретное несовпавшее поле. Поэтому факт только в том, что совпавшие файловые атрибуты не дали принятого cut; причина пока неизвестна. Следующую диагностику вести на новом валидном fixture или сначала локализовать поле. Единичное продуктовое наблюдение, не агентное правило; источник: `design-docs/R14-PERFORMANCE-DIAGNOSIS.md`, «Differential follow-up: saved-cut validity and live one-seal attribution» | нет
   - 2026-10-15 | SUSP canonical-versus-journal command hidden by stub | При расширении OPEN prefix на SUSP два cold-обзора не нашли расхождения команд: unit-stub давал одинаковые представления, а реальный `_publication` убирает `expected_preimage_commitment`, сохранённый `_command`. Mounted `[after_commit-suspension]` остался красным; MCP изолировал единственное поле. В `943e4a4` сравнение нормализовано как у штатного SUSP validator, stub воспроизводит асимметрию; три mounted границы прошли. AGENTS §6 и потолок §1 уже применимы; независимого agent replay нет, новую норму не вводить. | нет
   вопрос: что здесь ломается ПОСТОЯННО, а не что сломалось

2. ДИФФЫ  4d59376..HEAD
   1e1f7c4 perf(r14): keep derived recovery cache immutable
   e7c6639 fix(r14): reuse verified history without weakening call admission
   599b794 perf(r14): derive recovery commands once per native validation
   a34a212 test(r14): reject damaged pending suspension histories
   c8c9620 test(r14): enforce the complete fanout envelope byte boundary
   8dff0ff perf(r14): reuse authenticated capture and accounting artifacts
   b56dbba perf(r14): reuse held history in unresolved OPEN recovery cuts
   объём: 25 files changed, 1667 insertions(+), 69 deletions(-)   (git diff --shortstat 4d59376..HEAD | sed 's/^ *//' | grep . || echo 'нет изменений')
   незакоммиченного сейчас (в период НЕ входит): 19   (git status --porcelain | wc -l | tr -d ' ')
   коммитов периода: 7   (git rev-list --count 4d59376..HEAD)
   коммитов с R8 catalog: 0   (git rev-list --count 4d59376..HEAD -- src/chiplog/inert_shared/r8-implementation-v1.json)

3. ХВОСТЫ  .harness/handoff.md
   записей: 22   (python3 .harness/scripts/checks/handoff_pending.py --status | sed -n 's/^handoff_total=//p')
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
   · R14.6: внедрить causal OPEN-source fold из captured prefixes, закрыть три красных family-теста и mounted accepted cross-seal interrupted-recovery/substitution проверки до закрытия R14.6
   · R14.6: подтвердить mounted путь с обычными production clocks и admission-бюджетами на свежем допустимом fixture до закрытия R14.6
   · R14.6: mount per-call OPEN writer/read/replay APIs and full outer ABSENT/COMPLETE pending lifecycle recovery with corrupted-predecessor/orphan controls
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
| Agent replay | кейсов нет | регрессия вакуумна — прогонять нечего; встроенный снимок: `find .harness/reproducers -name case.md 2>/dev/null \| wc -l \| tr -d ' '` → 0 | вакуум |

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
| Checkpoint в worktree наследовал `.venv` `chiplog.pth` основного checkout; без явного source root тесты проверяли другой код. Минимальный тест через реальный launcher воспроизвёл импорт `foreign/sentinelpkg` и упал | Сгладить: передавать pytest `ROOT/src` первым, оставив остальные `PYTHONPATH` entries | `.harness/scripts/checkpoint.py`; регрессия в `tests/tooling/test_checkpoint_gate.py` |
| Длинный mounted R14 прогон не локализует стоимость до запуска и может тратить часы на исход, уже объяснимый истёкшим mandate. Профилировать безопасные read-only компоненты перед повтором; acceptance часы и budgets не менять | Сгладить диагностикой отдельных чтений; не запускать повторный full сценарий без нового различающего входа | `design-docs/R14-PERFORMANCE-DIAGNOSIS.md` — уже есть рабочий WIP, в этот коммит не включается |

## Классы, а не случаи

Назови повторяющуюся серию и проверь, образует ли она один класс. Например, три
«дочиненных» теста в разных файлах могут быть одним классом — но только если назван
общий механизм.

Для межкомпонентной гарантии проверь, проходил ли тест через байты реального
producer до consumer, и отвергал ли consumer одностороннюю подмену выбранного
поля или идентичности. Если нет, запиши конкретный пропущенный стык и реши,
нужен ли совместный плохой вход; отдельные успешные тесты сторон его не заменяют.

Период: семь коммитов `4d59376..HEAD`, 25 файлов, 1,667 добавлений и 69 удалений
(`./.harness/scripts/retro-inputs.sh`; сводка штатного снимка). В продуктовой линии
повторные recovery derivations/captures ограничены authenticated read-cut; коммит
`1e1f7c4` закрыл конкретный callback mutation входом, который проверяет прежнее
доказательство после отказа записи в immutable cache. Это продуктовый дефект,
исправлен узко; новая агентная норма из него не следует.

Harness-класс: selected checkpoint запускается без привязки import source к
собственному `ROOT`. В текущей сессии два запуска из candidate worktree без
`PYTHONPATH` фактически разрешили `.venv` `chiplog.pth` в основной checkout;
первый неверный запуск дал иной runtime verdict. Новый отрицательный tooling-тест
прошёл через `checkpoint.py` → `clean-git-env.sh` → fake `uv` → pytest: до фикса
он упал с `foreign/sentinelpkg/__init__.py` в assertion, после фикса выбрал
candidate-пакет. Класс шире конкретного R14: любой checkpoint с общим editable
environment мог проверить не тот checkout. Исправление задаёт текущий `ROOT/src`
первым в дочернем `PYTHONPATH`, не удаляя переданные пути.

## Кандидаты в правила

| Находка | Класс | Адрес / результат |
|---|---|---|
| Проверка checkpoint может импортировать код соседнего checkout через общий `.venv`/`PYTHONPATH` | Проверяемая toolchain-привязка к source root | `.harness/scripts/checkpoint.py`; отрицательная регрессия `tests/tooling/test_checkpoint_gate.py` показывает чужой путь до и кандидатный путь после исправления |
| Recovery callback мог подменить mutable derived evidence | Продуктовая граница R14 | Исправлено `1e1f7c4`; mounted regression в `tests/composition/test_r14_read_derived_recovery.py` |
| Долгий R14 AUTHORIZE/acceptance остаётся незакрытым по production-clock критерию | Продуктовое наблюдение, не ошибка harness | Сохранено в roadmap и WIP diagnosis; текущая ретро не объявляет R14.6 завершённым |

Ворота: есть ли прогоняемый вход. Здесь отрицательный вход найден и прошёл через
фактический checkpoint launcher, поэтому инструментальный ремонт выполнен.

У сбоя либо есть одна устранимая причина, либо так шумит сама система. Шуму системы
не отвечают правилом — ему отвечают системой.

Форму нового правила сверить с `.harness/HARNESS_MODEL.md`, маршрут «С чего начать»: он решает,
это раздел `AGENTS.md` или правило-файл, и какие поля обязательны.

## Инварианты модели и долги

`.harness/HARNESS_MODEL.md` задаёт владельца вердикта для каждого инварианта. Ретро сводит
результаты всех шести: 1, 3 и 4 устанавливают проверки, а 2, 5 и 6 — сама ретро.

| # | Инвариант | Как проверить | Держится? |
|---|---|---|---|
| 1 | у правила-файла есть вход либо непросроченная норма | `UV_CACHE_DIR=/private/tmp/chiplog-retro-uv-cache UV_NO_SYNC=1 sh .harness/scripts/gate.sh --self-test` → парный self-test прошёл; статус правил из снимка: 2 | держится для найденных 2 правил |
| 2 | у каждого раздела `AGENTS.md` есть гейт или пометка `[без гейта]` | `grep -c '^## [0-9]' AGENTS.md` → 11; глазами прочитаны все 11 носителей | держится |
| 3 | у каждого файла проверки есть парный блок в `--self-test` | тот же `gate.sh --self-test` → 14 layout и 58 adapter/checkpoint tests прошли | держится в охвате self-test |
| 4 | каждое сообщение о провале несёт метку и владельца | тот же self-test прошёл парные хорошие/плохие входы; общие сообщения вне охвата этим не доказаны | держится в охвате |
| 5 | из `gate.sh` не убыло строк `run` без записи ретро | `git diff 4d59376..HEAD -- .harness/scripts/gate.sh` → пусто | держится за период |
| 6 | ни один порог не в двух местах исполняемого кода | просмотр `.harness/scripts/thresholds.sh`, `.harness/scripts/checks/` и `.harness/scripts/new-case.sh`; полного поиска числовых условий не проводил | частично: локальный обзор, глобальный аудит не выполнен |

**Долги.** Список — в `.harness/HARNESS_MODEL.md`, раздел «Текущие долги». Два вопроса, не один:

1. **Сработал ли крючок?** Сработал — исход на этой ретро. Отсрочка одна, с причиной в журнале
   решений; на второй решает человек.
2. **Сколько ретро долгу?** Три без закрытия — исход обязателен независимо от крючка.

| Долгов было | Стало | Закрыто | Заведено |
|---|---|---|---|
| нет открытых долгов по `.harness/HARNESS_MODEL.md` | нет | 0 | 0 |

Handoff: 22 активных `следующим` хвоста по штатному снимку; старший выбор
`max_attempts = 3` уже принят пользователем в предыдущем продолжении. Точные строки
не дублируются здесь: снимок выше авторитетен.

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

Durable-метрик срабатываний за период нет; вывод о частоте highlights не делаю.

## Пропускная способность

Есть ли файл харнесса, который перестал читаться за один присест? Он идёт под нож:
сжать, слить, вынести в скрипт.

В этот период такого файла не выявлено. `AGENTS.md` и модель харнесса остались
читаемыми по нужным разделам; длинный автоматически встроенный снимок — evidence,
его не превращаю в ежедневную инструкцию.

## Итог

Одна из двух веток, обе легальны, обе оставляют след:
- `outcome: change` — урок доведён до правки харнесса, указан `rule`;
- `outcome: skip` — правки нет, указан `reason`.

Ретро заканчивается коммитом.

### Пять линз и итог

1. Хорошая работа: selected recovery derivation/capture переиспользуется только в
   удостоверенном cut; immutable-cache regression подтверждает сохранность evidence.
2. Первая неудача: checkpoint не закреплял import identity. Узкий source-root test
   сначала показал фактический чужой путь; после ремонта тест проходит.
3. Харнесс: штатный retro-inputs snapshot и self-test работают; agent replay пуст,
   поэтому регрессия кейсов вакуумна. Полный R8 catalog не менялся за период.
4. Фрикшены: wrong-checkout checkpoint сглажен этим изменением; дорогой mounted R14
   путь остаётся и требует отдельного read-only профиля. Production clock/budgets не
   смягчались.
5. Правка: дочерний checkpoint pytest получает `ROOT/src` первым; регрессия
   подтверждает реальный импорт. Адрес основной правки — `.harness/scripts/checkpoint.py`.

`UV_NO_SYNC=1 .venv/bin/pytest -q tests/tooling/test_checkpoint_gate.py` → 25 passed
за 4.45s; `.venv/bin/ruff check .harness/scripts/checkpoint.py
tests/tooling/test_checkpoint_gate.py` → All checks passed. Ретро-скилл просит
холодный red-team, но для промежуточного checkpoint пользовательская `AGENTS.md`
§7 прямо запрещает red-team; для этого коммита применён checkpoint-режим.

`outcome: change`; правило/адрес: `.harness/scripts/checkpoint.py`.
