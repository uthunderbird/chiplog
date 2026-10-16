---
date: 2026-10-16
# trigger: cadence | incident
trigger: cadence
# outcome: change | skip
outcome: change
# id правила, если outcome: change
rule: .harness/scripts/retro-inputs.sh
# почему нет, если outcome: skip
reason:
---

## Что перечитано

При заведении записи `new-retro.sh` встраивает сюда точный stdout штатного сборщика.
Числа остаются рядом с командами, которыми получены; переписывать их по памяти нельзя
(`AGENTS.md` §6).

```text
входы ретро — с последней ретро (6fc1bd1)

1. КОПИЛКА  .harness/observations.md
   записей: 32   (grep -c '^- 2[0-9][0-9][0-9]-' .harness/observations.md)
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
   - 2026-10-16 | native-shaped fixture agrees with consumer but not writer | `b12d81a` invented effects physical ID `schema:sha` in fixture and consumer; actual `r16_dispatch_outcomes._row` uses `record.record.head`, and native opening/terminal expose distinct logical aliases on one carrier. Real-writer positives and mutation negatives repaired in `f66baee`. Related SUSP stub observation shows the shared producer/consumer-asymmetry class; AGENTS §6/§11 already apply. Faithful agent input construction was attempted from original rollout: literal user rows and ed4b8bf tracked world exported, but complete historical dirty/untracked world bytes were not recovered; a reduced input has no replay validation. No agent frequency or new-rule efficacy claimed; audit in `.harness/retro/2026-10-16-r14-native-representations.md`, «Классы, а не случаи». | нет agent replay; product repair f66baee, mounted R14 OPEN
   - 2026-10-16 | source edit after runtime launch | Diagnostic 90670 started before the final source edit and stopped at startup `runtime source drift detected`; this is a scheduling error, not the later stable-source 23827 carrier failure. Existing guard rejected drift. One distinct occurrence; no faithful standalone old/new agent replay established, no launcher rule inferred. Evidence and bounded input audit: `.harness/retro/2026-10-16-r14-native-representations.md`; product lifecycle remains OPEN. | нет
   вопрос: что здесь ломается ПОСТОЯННО, а не что сломалось

2. ДИФФЫ  6fc1bd1..HEAD
   b134fcc feat(r14): define explicit startup genesis version contracts
   e24d75e perf(r14): reuse unchanged resumed readiness inputs within publication
   4452ebb fix(r14): bind readiness writer to authenticated physical preimage
   d362548 test(r14): preserve readiness final scan and rollback on expiry
   c821421 fix(r14): preserve exact integers with native JSON decoding
   объём: 11 files changed, 875 insertions(+), 3 deletions(-)   (git diff --shortstat 6fc1bd1..HEAD | sed 's/^ *//' | grep . || echo 'нет изменений')
   незакоммиченного сейчас (в период НЕ входит): 18   (git status --porcelain | wc -l | tr -d ' ')
   коммитов периода: 5   (git rev-list --count 6fc1bd1..HEAD)
   коммитов с R8 catalog: 0   (git rev-list --count 6fc1bd1..HEAD -- src/chiplog/inert_shared/r8-implementation-v1.json)

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

Встроенный снимок: `find .harness/reproducers -name case.md` дал ноль agent-case.
Агентная регрессия вакуумна; доли old/new не измерены и не приписываются.
Продуктовые JSON/SQLite тесты не являются replay поведения агента.

Root выполнил `sh .harness/scripts/gate.sh --self-test`: 14 tests passed in 5.72s,
new-retro generation checks passed; 59 tests passed in 21.60s. Это существующие
пары и ограниченные адаптеры, не полная регрессия R14. Новый дефект ниже этим
прогоном не пойман: его вход пока не включён в проверку.

## Фрикшены

| Фрикшен | Не встретиться или сгладить | Адрес |
|---|---|---|
| Отказ источника превращён в неизвестную метрику и успешный полный снимок | Не встретиться: отказ обязан дойти до генератора, который не публикует запись | `.harness/scripts/retro-inputs.sh`, `tests/tooling/test_retro_inputs.py`, существующий generator self-test |
| Дорогой повтор полного cut после fresh actor | Сгладить, сохраняя authority: локальный witness, CAS и исходный full fallback | `r14_recovery_publication.py`, actual native cross-seal selector; native результат OPEN |
| Быстрый парсер меняет числовую семантику | Не встретиться: сравнивать значение и тип на исполнимой границе | `test_call_history_witness_mounted.py`; продуктовый ремонт c821421 |
| Fabricated guard test легко принять за native proof | Не встретиться: отдельные утверждения и явная граница фикстуры | docstrings и `R14-VERIFICATION-STATUS.md`; существующий AGENTS §6 |
| Большой handoff и отсутствие durable highlight history | Сгладить сохранением конкретных обязательств и честным unknown | этот файл; пороги без новых измерений не менять |

## Классы, а не случаи

Два продуктовых класса периода: (1) совпавшее внешнее представление скрывает
различие типа/физического состояния; oversized JSON integers и same-head SQLite
mutation имеют разные контракты, не один искусственный agent-rule; (2) повторная
полная проверка пересекает окно live authority. c821421 и 4452ebb чинят реальные
семантические границы, e24d75e сохраняет initial authentication, fresh non-SQL
inputs, SQL preimage и все live guards. Исход 32005 `STALE:authority-after-cut`
не раскрывает swallowed inner exception; точную native deadline-причину не
приписываем. Synthetic clock test доказывает только свою ветку rollback.

Новый подтверждённый класс харнесса: **обязательный источник сбора падает, а
агрегатор продолжает выдавать сертификат полноты**. В независимом временном PATH
shim `git rev-list` возвращал 23. Настоящий `retro-inputs.sh` вернул 0, напечатал
`коммитов периода: ? команда упала` и `RETRO_INPUTS_COMPLETE=1`.
Raw evidence: `/var/folders/sv/88_d3t_d7ts_p_1ssndp2ttw0000gn/T/r14-retro-source-failure-gamgja9i/`.
Root прочитал сам `num()`, status/fallback и pipeline ветки. Существующий
`new-retro.sh` правильно отказывает при nonzero collector, но существующий тест
подменяет collector целиком; producer→collector→generator стык не проверяется.

Это мета-ремонт инструмента по шагу 1 маршрута HARNESS_MODEL, а не новая норма
человека и не `kind: agent`. Нужен реальный механический плохой вход рядом с
ремонтом и здоровая пара; вымышленные agent replay и дублирующее правило не нужны.
Новые агентные правила про кодеки/фикстуры не предложены: AGENTS §6/§11 уже
относятся к этим обязанностям, а продуктовый дефект не доказывает дыру инструкции.

## Кандидаты в правила

| Кандидат | Падающий вход | Решение / адрес |
|---|---|---|
| Источник упал, полнота всё равно объявлена | PATH shim rev-list exit23 на actual collector | Чинить существующий collector и actual-collector generator regression, не добавлять норму |
| Ошибка producer скрыта pipeline или вложенной подстановкой | RED: status до wc/sed, initial rev-list перед diff | Тот же подтверждённый класс; pipefail и отдельно проверенный initial revision |
| Ещё одно агентное правило о точной семантике | Нет отдельного agent-case; продуктовые тесты — другой вход | Не вводить; существующие §6/§11 и продуктовая работа остаются |

## Инварианты модели и долги

| # | Проверка | Итог |
|---|---|---|
| 1 | `rules_have_reproducers.py --status`, штатный self-test | Два rule-файла: обычный вход и непросроченная норма; agent-case replay не заявляется |
| 2 | Read AGENTS 1–11 against named gates / `[без гейта]` | Все разделы имеют носитель; содержательность по-прежнему суждение |
| 3 | `sh .harness/scripts/gate.sh --self-test` | Штатные пары прошли; выявленный producer seam требует нового отрицательного входа |
| 4 | Штатный self-test/static actionable checker | Охваченные сообщения прошли; новый отказ должен назвать действие агента |
| 5 | `git diff 6fc1bd1..b134fcc -- .harness/scripts/gate.sh` | Пустой diff: run не удалён |
| 6 | thresholds/new-case/check constants read | Highlight thresholds sourced, case limits imported, cadence/skip limits owned by retro_due.py; дублирование не установлено |

Модель: долгов было 0, осталось 0, закрыто 0, заведено 0 (встроенный источник —
таблица `HARNESS_MODEL.md`). Deferred defect registry пуст. Handoff содержит
24 различных незавершённых продуктовых обязательства; это не нулевой долг R14
и не повод закрывать их по этому ретро. Ретро их не снимает.

## Уставки хайлайтов

Durable частота и жёсткие срабатывания за период не установлены. Cooldown/current
state не заменяет журнал частоты. Не утверждаем ни «чаще всех», ни полугодовое
молчание. Порог handoff во входе превышен; это сохранённые различные работы, а
не основание повышать порог или удалять записи. Пороги не меняются.

## Пропускная способность

Автоматически встроенный снимок длинный, но повторное ручное сжатие уничтожило бы
точный захват. Аналитическая часть отделена от снимка. Дорогой native сценарий
57173 продолжает работать в отдельном frozen checkout; R14.6–R14.13 OPEN.
Ни benchmark парсинга, ни приобретённый native cut, ни cold source audit не
заменяют успешную публикацию/next Turn/second OPEN/replay.

## Холодный контур и классификация

Отдельный cold sol6.1/low swarm-red-team прочитал фактический файл и исходники:
P0=0, P1=0 для черновика; вердикт не покрывает будущий ремонт. Root прочитал
полный отчёт `/private/tmp/r14-json-readiness-retro-redteam.md`, затем загрузил
`retro/references/actions.md` и выполнил последнюю содержательную классификацию:

| Находка | Класс actions/model | Изменяемый адрес |
|---|---|---|
| Failed source certified complete, включая pipeline/fallback | Мета-ремонт существующего инструмента; HARNESS_MODEL шаг 1, actions «путь верный, но дорог» | `.harness/scripts/retro-inputs.sh` + `tests/tooling/test_retro_inputs.py` + existing `self-tests/new-retro-generation.sh` |
| Успешные RED/real-writer/typed-boundary подходы | Существующие §6/§11, не новая норма без слова человека | Этот аналитический раздел, без новой инструкции |
| Неизмеренная highlight frequency | Наблюдение, уже сохранённое 2026-09-04; данных для настройки нет | `.harness/observations.md`, существующая строка; пороги не меняются |

Непокрытых P0/P1 черновика нет. Механический bad input и исправление — отдельная
исполнительская работа после этой классификации; исход change не объявлен до
RED→GREEN и actual generator проверки. Agent-case не создаётся: новый дефект
воспроизводится исполнением сборщика, а не поведением модели.

## Ремонт и проверка

Холодный план ремонта сначала получил P1: pipefail не ловит отказ внутри
`git diff $(git rev-list ...)..HEAD`, если внешний diff успешен. План исправлен,
повторный cold review — 0 критических. Initial revision теперь проверяется
отдельным присваиванием; отдельный first-retro test ломает только этот rev-list,
оставляя count рабочим. Обязательные source failures больше не нормализуются
в успех. Grep exit1 означает законный ноль; exit2 остаётся отказом. Пустой diff
получает display default только после успешной команды. Вызов через sh сохранён:
при отсутствии pipefail он переходит в установленный Bash, отказ exec не скрывается.

` .venv/bin/python -m pytest -q tests/tooling/test_retro_inputs.py -k actual_collector --tb=short`
до ремонта: **10 failed in 17.52s**. После ремонта тот же selected scope:
**10 passed in 28.75s**. Девять source faults проверяют реальный collector и
реальный generator: nonzero, нет complete marker/новой записи/остаточных temp,
прежние записи побайтно сохранены; снятие fault даёт успешный retry.
Десятый сценарий проверяет здоровые нулевые counts и пустой diff.

`.venv/bin/python -m pytest -q tests/tooling/test_retro_inputs.py --tb=short`:
**19 passed in 43.28s**. `sh .harness/scripts/gate.sh --self-test`: exit 0; 14 paired tests (6.35s),
10 actual collector cases (29.55s), 59 adapter/checkpoint tests (22.58s).
Generation self-test вызывает только эти узкие actual collector cases, без рекурсии. Ruff, actionable checker и diff check прошли.
Root прочитал узкий static review `/private/tmp/r14-retro-collector-repair-review.md`:
0 P0/P1; это не generic checkpoint red-team и не свидетельство выполнения тестов.

## Итог

`change`: ремонт `.harness/scripts/retro-inputs.sh`, actual producer regressions
в `tests/tooling/test_retro_inputs.py` и подключение их в существующий generation
self-test. Адрес класса тот же, новая агентная норма и replay ratio не вводятся.
Полнота означает успешное исполнение обязательных источников, не атомарный снимок
репозитория и не семантическую валидацию всех inventory данных. R14 остаётся OPEN.
Ретро закончится только коммитом.
