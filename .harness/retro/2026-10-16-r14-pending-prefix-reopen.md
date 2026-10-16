---
date: 2026-10-16
# trigger: cadence | incident
trigger: cadence
# outcome: change | skip
outcome: skip
# id правила, если outcome: change
rule:
# почему нет, если outcome: skip
reason: "Исправленные prefix/identity стыки продуктовые и уже охвачены AGENTS §6/§11; debugger/review промахи нарушают существующие §1/§4/§6, новой нормы не обосновывают. Подтверждённой поломки механизма харнесса или уставки за период не найдено; наблюдение сохранено, mounted R14 остаётся OPEN."
---

## Что перечитано

При заведении записи `new-retro.sh` встраивает сюда точный stdout штатного сборщика.
Числа остаются рядом с командами, которыми получены; переписывать их по памяти нельзя
(`AGENTS.md` §6).

```text
входы ретро — с последней ретро (0846538)

1. КОПИЛКА  .harness/observations.md
   записей: 32   (grep -c '^- 2[0-9][0-9][0-9]-' .harness/observations.md || [ $? -eq 1 ])
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

2. ДИФФЫ  0846538..HEAD
   0bcb6f7 fix(r14): verify readiness prefix before exact historical reads
   6632606 test(r14): preserve authentic legacy genesis before version admission
   136bde8 fix(r14): authenticate pending readiness during outcome reconstruction
   9f9352c fix(r14): preserve original decision aliases and pending prefix reads
   3b4084b fix(r14): authenticate historical intervals across later pending publication
   объём: 14 files changed, 984 insertions(+), 76 deletions(-)   (git diff --shortstat 0846538..HEAD | sed 's/^ *//')
   незакоммиченного сейчас (в период НЕ входит): 18   (git status --porcelain | wc -l | tr -d ' ')
   коммитов периода: 5   (git rev-list --count 0846538..HEAD)
   коммитов с R8 catalog: 0   (git rev-list --count 0846538..HEAD -- src/chiplog/inert_shared/r8-implementation-v1.json)

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
   кейсов: 0   (find .harness/reproducers -name case.md | wc -l | tr -d ' ')
   регрессия вакуумна — прогонять нечего, отметь это, а не «держит»

5. ИНВАРИАНТЫ И ДОЛГИ
   правил-файлов: 2   (python3 .harness/scripts/checks/rules_have_reproducers.py --status | sed -n 's/^rules_total=//p')
   разделов AGENTS.md: 11   (grep -c '^## [0-9]' AGENTS.md || [ $? -eq 1 ])
     это левая половина инварианта 2: у каждого раздела должен быть
     гейт или пометка [без гейта] — правую половину читай глазами
   строк run в gate.sh: 12   (grep -c '^run ' .harness/scripts/gate.sh || [ $? -eq 1 ])
   из них адаптеров: 2   (grep -c '^run .*/\(lint\|test\)\.sh' .harness/scripts/gate.sh || [ $? -eq 1 ])
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

Встроенный снимок получен штатным `new-retro.sh --id r14-pending-prefix-reopen`:
exit 0, пять секций и `RETRO_INPUTS_COMPLETE=1`. Его байты сохранены без ручной
пересборки. Период — пять коммитов `0846538..0bcb6f7`; прочитаны действительные
source/test/doc диффы, а не только shortstat. Снимок dirty-count относится к
моменту сбора, включая тогдашние временные файлы сборщика, не к inherited baseline.

Регрессия agent-кейсов вакуумна — прогонять нечего. Продуктовые RED/GREEN не
являются agent replay; доля эффективности агентного правила не установлена.

## Фрикшены

| Фрикшен | Не встретиться или сгладить | Адрес / результат |
|---|---|---|
| После разрешения pending readiness в OPEN fold следующий accepted-outcome callback, затем exact-history gate продолжали отказывать | Сгладить: проверять каждый настоящий consumer отдельно | `test_r14_recovery_pending_prefix.py`; узкие продуктовые исправления `9f9352c`, `136bde8`, `0bcb6f7`; полный native reopen ещё OPEN |
| Первый новый gate-test отверг неканонический служебный journal JSON | Не встретиться: сначала канонический wire, потом интерпретация RED | В тесте canonical JSON исправлен; настоящий RED — 2 failed/2 passed, setup-сбой в него не включён |
| Attach отверг statement/expectedContent, root продолжил до проверки ответа | Не встретиться: исполнить уже действующий §4/§6 | Перевооружён plain line после чтения источника; промежуточный breakpoint не дал наблюдения, его результат не заявлен |
| Bounded MCP query ожидал attribute у dict | Сгладить: исправленный запрос к известным скалярам | Переменные не изменялись; единичная ошибка выражения не доказывает дефект инструмента |
| Cold reviewer сначала читал frozen candidate вместо main diff | Не встретиться: явно привязать target | Root уточнил абсолютный main path/HEAD, агент перечитал фактический diff; первоначальный обзор не принят за доказательство |
| Независимый tracked legacy-context мешал точному staged-срезу | Сгладить сохранением custody | Точные байты/SHA сохранены вне repo, после `136bde8` восстановлены; отдельный `6632606` сохранил fixture и context |

## Классы, а не случаи

Наблюдаемый продуктовый класс — одна публикация пересекает несколько разных
исторических reader-gates. Точный структурный prefix proof в одном callback не
разрешает другой callback и не заменяет его semantic/full-cut proof. Native
saved-bundle прогоны показали каждый отдельный отказ; реальные SQL routing tests
проверяют ABSENT/COMPLETE, повреждение commitment, отсутствие fallback и повторное
чтение без cached success. Фактические source constructors/anchor projections
проверили полный selected-decision reference, не только удобный subject alias.

Удачные решения сохранены исполнимо: структурный interval bridge вместо удаления
adjacency; закрытый schema/owner alias; существующий exact-prefix verifier вместо
общего allowlist; аутентифицированный V1 fixture до изменения initializer. Fixture
не является работающим version gate. Последний целевой набор:
`.venv/bin/pytest -q tests/composition/test_r14_recovery_pending_prefix.py
 tests/composition/test_r14_historical_suffix.py
 tests/composition/test_r14_native_selected_accounting_fast_path.py --tb=short` —
202 passed in 5.37s. Checkpoint `0bcb6f7` отдельно дал 124 passed in 4.55s.
Это ограниченные проверки; sentinel/stub не доказывает полную native authority.

Последний actual MCP exact-history gate показал `(authenticated, defer_open_pending,
exact_historical_sources, operation, pending_count, actual_eq_anchor)` =
`(True, False, True, readiness, 1, False)` в ожидаемом startup/OPEN/current-cut/history
стеке. Процесс 73210 на момент черновика ещё работает. Наблюдение gate не означает
успешного startup, следующего Turn, второго seal, production-clock workflow или R14.
Все живые probes обслуживались тем же handle до terminal; candidate source
не менялся в ходе соответствующего acquisition. Новых SEND runner не запрашивает.

Серия сохранена в `.harness/observations.md`. Прежняя серия producer/consumer
representation-skew родственна по границе, но её старое неудачное восстановление
agent-input не доказывает попытку replay для нынешних debugger/review эпизодов.

## Кандидаты в правила

Новая норма не вводится. Уже существующие §6/§11 требуют действительную границу,
полный наблюдаемый исход и узкое исправление; §1/§4/§6 охватывают target обзора,
обработку отказа tool и запрет считать недостигнутый breakpoint доказательством.
Самостоятельный agent replay новых эпизодов не выполнялся; успешность будущего
правила и невозможность построить вход не заявляются. Продуктовые regression inputs
не подменяют faithful agent world. Нового подтверждённого failing harness predicate
нет; дублирование текста существующего правила не является механизмом исправления.

Финальная классификация после чтения `retro/references/actions.md`:

| Находка | Класс | Адрес / действие |
|---|---|---|
| Повторные физические prefix barriers в разных consumers | Продуктовый класс; новой агентной дыры не доказано | `.harness/observations.md`, запись readiness prefix crosses distinct historical consumers; продуктовые тесты уже в трёх перечисленных коммитах |
| Debugger response, неверный review target и неканонический test setup | Исполнительские промахи под действующими §1/§4/§6, нового replay нет | `.harness/observations.md`, запись debugger and review target execution lapses; норму не дублировать |
| Exact bridge/aliases и authentic legacy fixture сработали | Исполнимый продуктовый результат, не предложение новой нормы | Существующие regression tests и fixture в `3b4084b`/`9f9352c`/`6632606`; утверждение об эффективности agent-rule не производится |
| Частота highlights и стоимость handoff | Уставка не установлена по отсутствию измерения | Существующая запись highlight observability в `.harness/observations.md`; пороги оставить, новой частоты/срока не выдумывать |

Холодный file-based `$swarm-red-team` прочитал этот файл, actual observation diff,
реальные commits и consumer/helper/test границы. Отчёт
`/private/tmp/r14-pending-prefix-retro-cold-review.md`: P0=0, P1=0, без runtime rerun.
Root прочитал отчёт; findings P0/P1 для исправления нет. Новые нормы, удаления правил
и изменения уставок не предлагаются. Это последний содержательный шаг ретро.

## Инварианты модели и долги

| # | Проверка / доказательство | Вердикт в охвате |
|---|---|---|
| 1 | `python3 .harness/scripts/checks/rules_have_reproducers.py`, exit 0; status: два правила, одна норма, ревизия через 56 дней | Проверка пройдена, не вакуумна |
| 2 | Прочитаны все разделы AGENTS §1–11 и их gate/partial gate/честные `[без гейта]` | Носитель назван у каждого; действенность soft-правил отдельно не доказана |
| 3 | Прочитан self-test dispatcher; harness/scripts не менялись (`git diff 0846538..HEAD -- .harness AGENTS.md` пуст до этой записи) | Нового запуска self-test здесь нет; предшествующее доказательство остаётся в ретро `2026-10-16-r14-json-readiness.md`, не новый PASS |
| 4 | `python3 .harness/scripts/checks/messages_are_actionable.py`, exit 0 | Пройден статический охват; не доказательство всех устных сообщений |
| 5 | Пустой diff `gate.sh` за период; список run и пары прочитаны | Удаления run за период нет |
| 6 | Прочитаны thresholds, потребители HC и выделенные cadence/skip/revision constants | Нового дублирования в scoped review нет; глобальная теорема о всех числах не заявлена |

`checks_are_wired.py` также exit 0. HARNESS_MODEL «Текущие долги» — пустая таблица:
0 было, 0 стало, 0 закрыто, 0 заведено. Это не закрывает 24 продуктовых handoff-хвоста;
ретро их переносит без изменения сроков или заявлений о выполнении.

## Уставки хайлайтов

Durable frequency-телеметрии hard/soft срабатываний за период нет. Править или
удалять порог по отсутствию журнала нельзя. `guard-edit.sh` перед правкой вернул 0,
но это одно наблюдение и не частота. Пороги не меняются; inherited untracked bytes
не удаляются ради счётчика. Связь с известным observation highlight observability
сохраняется, новой нормы на основании отсутствия измерения нет.

## Пропускная способность

Новый нечитаемый файл харнесса не установлен. Автоматический input snapshot длинный,
но его точный вывод не сжимается вручную. Отдельные продуктовые диагностические
документы и рост authenticated history не закрываются косметическим сокращением
харнесса. Сокращение продуктового повторного чтения требует измерений и сохранения
authority, как указано в handoff №31.

## Итог

`outcome: skip`: продуктовые defects исправлены в своих коммитах и обеспечены
ограниченными тестами. Новое наблюдение сохраняет класс; оно не считается ремонтом
харнесса. Agent ошибки уже покрыты действующими инструкциями, отдельного replay
новой нормы нет, дефект исполняемого harness-tool/порога не установлен. Native
startup и полный R14 остаются OPEN. Холодный file-based red-team и финальная
классификация actions.md выполнены; завершение — отдельный checkpoint-коммит
Markdown-среза по ранее данному разрешению. AGENTS §7 исключает generic full-diff
precommit red-team и полную регрессию для промежуточного коммита; требуемый cold
контур самого файла ретро уже выполнен.
