---
date: 2026-10-16
# trigger: cadence | incident
trigger: cadence
# outcome: change | skip
outcome: skip
# id правила, если outcome: change
rule:
# почему нет, если outcome: skip
reason: "Bounded faithful agent-input construction recovered literal user rows and ed4b8bf tracked world, but not the complete historical dirty/untracked world; reduced-world replay is unvalidated. Existing AGENTS 6 and 11 apply, product repair does not establish a new agent rule, and no threshold or formal-debt trigger was found. Concrete class and launch evidence remain observations; R14 runtime work stays OPEN."
---

## Что перечитано

При заведении записи `new-retro.sh` встраивает сюда точный stdout штатного сборщика.
Числа остаются рядом с командами, которыми получены; переписывать их по памяти нельзя
(`AGENTS.md` §6).

```text
входы ретро — с последней ретро (e046e23)

1. КОПИЛКА  .harness/observations.md
   записей: 30   (grep -c '^- 2[0-9][0-9][0-9]-' .harness/observations.md)
   - 2026-10-16 | spawned diagnostic runner imports | Временный R14 continuation runner имел main guard, но импортировал тестовый модуль до него. Spawn повторно загрузил deployment_trust policy-модули в других owners; настоящий `AuthorityBrokerRuntime.attest()` остановил startup с identity mismatch до readiness. Минимальный `/private/tmp/r14_spawn_attest_bad.py` воспроизвёл отказ; перенос всех project imports внутрь main в `/private/tmp/r14_saved_cross_seal_continue.py --attest-smoke` дал 5 непустых owner attestations и exit 0. Проверка одного `runpy(..., run_name='__mp_main__')` не проверяла owner module closure. Действующий продуктовый гейт удержал границу; новая норма не вводится, независимого agent replay нет. Сохранённые head 28 и transfer count 1 перепроверены; неизменность всех startup sidecars не заявляется | нет agent replay; единичная ошибка diagnostic fixture
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
   - 2026-10-13 | handoff identifier reuse after close | `.harness/scripts/handoff.sh --close 30` удалил последнюю запись, после чего `.harness/scripts/decide.sh` снова выдал ID `30`: прежний allocator вычислял следующий ID только из live-записей. После нормы пользователя от 2026-10-15 исправлен в `b274a90`; `11e08a5` добавил проверку сбоя записи после резервирования ID. `handoff.py` сохраняет high-water mark в `handoff-sequence`; `uv run pytest -q tests/tooling/test_handoff_identifiers.py` на 2026-10-16 дал 8 passed in 0.18s. Устойчивость к удалению sequence-файла и аварии питания не заявляется. | закрыто `b274a90`, `11e08a5`; статус сверен ретро 2026-10-16
   - 2026-10-14 | R14 restored fixture rejected before target read | Старый `pytest-9070` fixture восстановили из 44-файловой копии; размер, SHA-256, режим и mtime файлов совпали. Текущий runtime отверг cut до целевого чтения с общей ошибкой `selected native accounting source differs from historical cut`. Валидатор проверяет source bytes/stamp, tenant/head/authority, preparation bindings, lineage, inventory, seal и fingerprint; лог не локализует конкретное несовпавшее поле. Поэтому факт только в том, что совпавшие файловые атрибуты не дали принятого cut; причина пока неизвестна. Следующую диагностику вести на новом валидном fixture или сначала локализовать поле. Единичное продуктовое наблюдение, не агентное правило; источник: `design-docs/R14-PERFORMANCE-DIAGNOSIS.md`, «Differential follow-up: saved-cut validity and live one-seal attribution» | нет
   - 2026-10-15 | SUSP canonical-versus-journal command hidden by stub | При расширении OPEN prefix на SUSP два cold-обзора не нашли расхождения команд: unit-stub давал одинаковые представления, а реальный `_publication` убирает `expected_preimage_commitment`, сохранённый `_command`. Mounted `[after_commit-suspension]` остался красным; MCP изолировал единственное поле. В `943e4a4` сравнение нормализовано как у штатного SUSP validator, stub воспроизводит асимметрию; три mounted границы прошли. AGENTS §6 и потолок §1 уже применимы; независимого agent replay нет, новую норму не вводить. | нет
   - 2026-10-16 | R14 typed provenance lost across recovery projections | В `d784095` исправлена серия реальных producer/consumer стыков: пропущенные resolver policy/invocation, R16 body-domain marker, selected-decision provenance raw/logical aliases. Полные типизированные references и односторонние mutation tests восстановили точные joins; subject/head не заменяют полный reference. Это продуктовая серия, не независимый old/new agent replay; AGENTS §6 уже требует наблюдаемую границу, новая агентная норма не вводится. Mounted resume и полный R14 ещё требуют отдельного результата; см. `design-docs/R14-VERIFICATION-STATUS.md`. | нет agent replay; продуктовый ремонт `d784095`, итоговый mounted proof OPEN
   вопрос: что здесь ломается ПОСТОЯННО, а не что сломалось

2. ДИФФЫ  e046e23..HEAD
   f66baee fix(r14): preserve native recovery aliases and resumed continuation
   b12d81a fix(r14): join original recovery witness independently of append evidence
   ed4b8bf fix(r14): authenticate resumed native cuts and repeated custody scenarios
   f6828a0 fix(r14): bind V3 writer preimage and qualify recovery membership
   f8a6844 fix(r14): preserve historical OPEN run and resume suffix on restart
   объём: 28 files changed, 3449 insertions(+), 190 deletions(-)   (git diff --shortstat e046e23..HEAD | sed 's/^ *//' | grep . || echo 'нет изменений')
   незакоммиченного сейчас (в период НЕ входит): 18   (git status --porcelain | wc -l | tr -d ' ')
   коммитов периода: 5   (git rev-list --count e046e23..HEAD)
   коммитов с R8 catalog: 0   (git rev-list --count e046e23..HEAD -- src/chiplog/inert_shared/r8-implementation-v1.json)

3. ХВОСТЫ  .harness/handoff.md
   записей: 24   (python3 .harness/scripts/checks/handoff_pending.py --status | sed -n 's/^handoff_total=//p')
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
   · R14.6: закрыть выявленные аудитом отдельные mounted controls для трёх A54 границ, selected read-only whole-seal HOLD/no-write, per-slice writer applicability и unsupported durable-version tuple; сопоставить multi-seal interleaving с фактическими наблюдениями
   · R14.6: исправить owner continuation join для original-resolver witness, отдельного от APPEND_EVIDENCE manifest; сохранить generic omission-negative и подтвердить readiness, next-turn и оставшийся cross-seal lifecycle
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

Регрессия вакуумна — прогонять нечего: встроенный штатный снимок содержит ноль
`case.md`. Это не положительная agent-регрессия и не доказательство силы инструкций.
Два rule-файла проверены отдельно штатной проверкой, их вердикт не вакуумный.

## Классы, а не случаи

Повторяется исключение реальной асимметрии producer/consumer из фикстуры.
Наблюдение SUSP 2026-10-15 уже описывает stub, уравнявший `_command` и
`_publication`, хотя один сохраняет `expected_preimage_commitment`, другой его
убирает. В `b12d81a` native-shaped effects fixture и новый join согласованно
использовали придуманный `schema:sha`; реальный `r16_dispatch_outcomes._row`
публикует ID `record.record.head`. Тест с настоящим `_row` упал за 0.85s.
Исторический authenticated resume-cut затем показал две законные logical/physical
проекции opening и terminal; positive test был красным за 0.69s. `f66baee`
исправляет оба стыка, сохраняет точные mutation negatives и generic omission
rejection. Это конкретный общий механизм, а не любой provenance defect.

AGENTS §6 требует наблюдаемую фактическую границу, §11 — реальные workflows и
исполняемые требования. Эти инструкции уже применимы. Продуктовые red inputs
не различают дыру агентной инструкции и неисполнение существующей; независимого
old/new agent replay нет. Попытка собрать вход выполнена, а не выведена из
отсутствия replay: аудит исходного root rollout до task boundary восстановил
60 literal user rows и точный tracked `ed4b8bf` через `git archive`; начальный
Add File теста также найден. Не собран полный dirty world: исторические bytes
untracked `.codex/config.toml`, recovery directory и SQLite sidecars не
подтверждены снимком. `world_state` содержит инструкции, не файлы; найденный
pre-task status даёт имена, не bytes. Дешёвая редукция до чистого Git world ещё
не доказана старым replay. Это bounded limitation, не доказательство, что backup
нигде нет. Выполненный script, строки лога, hashes и ограничения:
`/private/tmp/r14-native-agent-input-attempt.md` и
`/private/tmp/r14_replay_input_audit.py`; raw candidate —
`/private/tmp/r14-native-replay-candidate/`. `new-case.sh` в текущем main не
запускался: снял бы f66/current world и посторонние untracked вместо старого.
Агентные доли не заявляются. Механический критерий «fixture вызывает factory» также
не отличает использование настоящего результата от вызова с последующей ручной
подменой. Новое правило по имени функции переобучилось бы на один файл.

90670 — отдельная ошибка запуска: root начал процесс до последней source edit,
и startup остановил его с `runtime source drift detected`. Это не continuation
failure и не представитель carrier-shape класса. В 23827 исходники были стабильны;
он завершился реальным owner carrier fault до readiness. Оба процесса terminal.

## Что сработало

- Writer-derived fixture и односторонние подмены сделали фактический стык дешёвым.
- Authenticated historical cut проверил непустой original без нового тяжёлого
  runtime; он не выдаёт current authority и не заменяет mounted publication.
- Изолированный worktree позволил готовить next-Turn repair, сохранив основной
  source snapshot. Exact-match transfer и последующий root pytest проверили срез.
- Invocation-local artifacts переместили чистую деривацию до actor issuance.
  Публичная реконструкция и обе live-проверки остались; дорогой повторный cut OPEN.

## Фрикшены

| Фрикшен | Не встретиться или сгладить | Адрес и состояние |
|---|---|---|
| Долгий runtime нашёл invented native carrier | Не встретиться: реальный writer seam доступен до дорогого запуска | `tests/capabilities/agent_loop/test_original_continuation_witness.py`; продуктовый ремонт `f66baee` |
| Source edit при запуске 90670 | Не встретиться: закончить snapshot; параллельную подготовку вести в другом worktree | `design-docs/R14-VERIFICATION-STATUS.md`; отдельное наблюдение, guard удержал границу |
| Spawn imports сломали owner attestation | Не встретиться: проверять actual owner closure, не только main guard | Существующее наблюдение `spawned diagnostic runner imports`; не дублируем |
| Повторный полный cut после свежего actor | Сгладить: определить bounded reuse и независимые guards через красный count-test | `src/chiplog/composition/r14_recovery_publication.py`, handoff 32/42; OPEN |
| 24 переносимых handoff | Сгладить закрытием фактических обязательств, не удалением и не повышением порога | `.harness/handoff.md`; число из frozen snapshot, lifecycle не закрыт |

## Кандидаты в правила

| Находка | Класс и адрес | Решение по references/actions.md |
|---|---|---|
| Invented carrier и спрятанная alias асимметрия | Повторяющееся наблюдение; `.harness/observations.md` | Добавлена observation `native-shaped fixture agrees with consumer but not writer`; bounded input attempt не дал валидного полного/reduced agent case |
| Source edit во время запуска | Отдельное наблюдение; `.harness/observations.md` | Добавлена observation `source edit after runtime launch`; отдельный случай без прогоняемого agent input |
| Успешные producer/retained-cut checks | Уже действующие §6/§11 | Результат сохранён здесь; новая норма не предлагается, инструкция не дублируется |
| Частота highlights и формальные долги | `.harness/scripts/thresholds.sh`, `.harness/HARNESS_MODEL.md` | Нет durable частоты или наступившего debt trigger; не менять уставки |

Финальная классификация выполнена по `.agents/skills/retro/references/actions.md`:
провалы без валидного прогоняемого agent input → две названные строки копилки;
существующий spawn-import эпизод уже имеет строку и не дублируется. P1 холодного
черновика «skip без попытки построить вход» адресован здесь фактическим bounded
аудитом и точной границей восстановления. Исправляется обоснование этой ретро,
не создаётся новая норма. Успешные подходы не эскалируются в норму, поэтому нового
вопроса человеку нет. Продуктовые performance repairs остаются в handoff 32/42,
не выдаются за ремонт харнесса. Observation additions не засчитываются ремонтом.

## Инварианты модели и долги

| # | Проверка | Наблюдаемый вывод |
|---|---|---|
| 1 | `python3 .harness/scripts/checks/rules_have_reproducers.py --status` | `rules_total=2`, `rules_norms=1`, `norm_review_in=56`, `revision_days=90`; штатная проверка exit 0 |
| 2 | Root перечитал разделы 1–11 `AGENTS.md` | У каждого назван гейт/частичный гейт или `[без гейта]`; это суждение, не agent replay |
| 3 | `sh .harness/scripts/gate.sh --self-test` | Exit 0: плохие/хорошие пары, generation checks, 14 passed in 6.20s и 59 passed in 22.12s; полный продуктовый набор не запускался |
| 4 | `python3 .harness/scripts/checks/messages_are_actionable.py`, тот же self-test | Exit 0 в проверенном охвате; содержательность всех возможных сообщений не доказана |
| 5 | `git diff --name-only e046e23..f66baee -- AGENTS.md .harness .agents` | Только observations; `gate.sh` за период не менялся, строки run не удалялись |
| 6 | Root прочитал `thresholds.sh` и `retro_due.py`; `rg -n '^HC_[A-Z_]+=' .harness --glob '*.sh'` | HC определения только в thresholds; cadence/skip constants в своём check. Новых изменений уставок в периоде нет; исчерпывающий аудит всех числовых условий не заявляется |

Formal debt table HARNESS_MODEL пуста, что не обнуляет продуктовый handoff.
Встроенный снимок содержит 24 хвоста; ни один здесь не закрывается. Число 18
незакоммиченных путей относится к моменту генерации снимка, не классифицирует
их как inherited и не задаёт текущее состояние.

## Уставки хайлайтов и пропускная способность

Durable частоты hard/soft alerts нет; полугодовое молчание не установлено.
Порог не повышается по отсутствию событий. Auto-embedded input snapshot сохранил
точный вывод и `RETRO_INPUTS_COMPLETE=1`; самотест generation прошёл. Обязательный
snapshot не сжимаем вручную. Конкретный новый нечитаемый файл харнесса не выявлен.

## Холодный контур

Cold sol 6.1/low `$swarm-red-team` прочитал именно этот файл и нашёл один P1:
skip не был подкреплён попыткой построения входа. После реального bounded аудита
повторный обзор закрыл P1, новых P0/P1 нет:
`/private/tmp/r14-native-retro-draft-redteam-recheck.md`. Затем actions
классификация произвела две observation additions. Отдельный cold final-diff
`$swarm-red-team` прочитал итоговые файлы: P0=0, P1=0 в документальном scope,
`/private/tmp/r14-native-retro-final-diff-redteam.md`. Ни один обзор не проверял
продуктовый runtime и не подтверждает валидность agent replay. Audit script
читался, не перезапускался обзорами; первый recheck отдельно сверил hashes
экспортированных candidates. Исполнитель восстановления прочитал оба отчёта целиком; root проверяет результат.
Swarm artifact: `/private/tmp/r14-native-representations-retro-swarm.md`;
семь итераций, все пять линз. Контекст изолирован, внешней независимости от
семейства модели нет. Ретро незавершена до отдельного коммита.

## Итог

Исход `outcome: skip`: существующие инструкции адресуют выявленный класс;
полный старый dirty world не собран при описанной попытке восстановления;
валидность reduced world ещё не установлена. Ошибочного порога или
наступившего formal-debt trigger нет. Не вводится норма по названию writer.
Новые факты сохранены как observations с конкретными адресами; это не
substantive harness repair. Продуктовые фиксы уже в `f66baee`, повторно их не
засчитываем как ремонт харнесса. R14, current readiness, mounted next-Turn,
cross-seal lifecycle и repeated-cut performance остаются OPEN.

`python3 .harness/scripts/checks/retro_due.py --status` до создания записи дал
`retro_entries=57`, `retro_since=5`, `retro_skips=0`, `retro_max_skips=4`.
Один такой skip не превышает действующий лимит; blanket permission не меняет его.
Ретро незавершена до разрешённого пользователем коммита.
