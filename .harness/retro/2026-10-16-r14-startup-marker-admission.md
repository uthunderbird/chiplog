---
date: 2026-10-16
# trigger: cadence | incident
trigger: cadence
# outcome: change | skip
outcome: skip
# id правила, если outcome: change
rule:
# почему нет, если outcome: skip
reason: "Реальные marker/parser стыки исправлены продуктовым срезом; AGENTS §6/§11 уже требуют наблюдаемую границу. Отдельного faithful agent replay нового правила нет, его невозможность не заявляется. Механические пары харнесса проходят; данных для изменения уставок нет. Два startup admission RED и свежий полный two-seal результат остаются OPEN."
---

## Что перечитано

При заведении записи `new-retro.sh` встраивает сюда точный stdout штатного сборщика.
Числа остаются рядом с командами, которыми получены; переписывать их по памяти нельзя
(`AGENTS.md` §6).

```text
входы ретро — с последней ретро (7c85226)

1. КОПИЛКА  .harness/observations.md
   записей: 34   (grep -c '^- 2[0-9][0-9][0-9]-' .harness/observations.md || [ $? -eq 1 ])
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
   - 2026-10-16 | readiness prefix crosses distinct historical consumers | После local OPEN-effects route в `9f9352c` настоящий saved-bundle startup head29 отказал в accepted-outcome effects callback V3; MCP показал present callback=False при historical cut18, затем accepted_outcome=None. `136bde8` восстановил точный prefix route и actual MCP получил outcome для того же call/cut; следующий exact-history gate отдельно отказал как unverified pending. `0bcb6f7` добавил точный verifier туда; real-SQL ABSENT/COMPLETE RED дал 2 failed/2 passed, targeted GREEN 202 passed in 5.37s, actual gate стал authenticated=True/defer=False. Все три маршрута сохраняют full selected/semantic validation; unit sentinels не доказывают native startup. Полный reopen на момент записи ещё идёт. AGENTS §6/§11 уже охватывают реальные consumer seams; нового agent replay или эффективности новой нормы не установлено. | продуктовый класс, native R14 OPEN; `.harness/retro/2026-10-16-r14-pending-prefix-reopen.md`
   - 2026-10-16 | debugger and review target execution lapses | В текущей сессии attach отклонил statement/expectedContent breakpoint, но root успел продолжить процесс до проверки ответа; поздний plain-line breakpoint не дал наблюдения и не объявлен доказательством. Другой bounded query ошибочно прочитал dict как attribute, затем исправлен без изменения переменных. Cold reviewer сначала прочитал frozen candidate вместо main, после уточнения перечитал actual diff. Первоначальный gate-test использовал неканонический journal JSON; setup исправлен до настоящего RED. Это промахи под существующими AGENTS §1/§4/§6, а не установленный дефект debugger/харнесса; самостоятельный agent replay этих эпизодов не выполнялся, невозможность его построить не заявляется. | сессионные наблюдения; `.harness/retro/2026-10-16-r14-pending-prefix-reopen.md`; новой нормы нет
   вопрос: что здесь ломается ПОСТОЯННО, а не что сломалось

2. ДИФФЫ  7c85226..HEAD
   2e7c27f feat(r14): acquire existing authority locks without provisioning
   4ceaf14 fix(r14): separate startup reduction marker from current anchor proof
   096c8c6 perf(r14): reuse Run snapshot across pending readiness effects reads
   9faeccf fix(r14): verify historical startup across exact pending readiness
   9e18a33 perf(r14): reuse acquired history when rebuilding historical OPEN cuts
   объём: 13 files changed, 970 insertions(+), 38 deletions(-)   (git diff --shortstat 7c85226..HEAD | sed 's/^ *//')
   незакоммиченного сейчас (в период НЕ входит): 19   (git status --porcelain | wc -l | tr -d ' ')
   коммитов периода: 5   (git rev-list --count 7c85226..HEAD)
   коммитов с R8 catalog: 0   (git rev-list --count 7c85226..HEAD -- src/chiplog/inert_shared/r8-implementation-v1.json)

3. ХВОСТЫ  .harness/handoff.md
   записей: 25   (python3 .harness/scripts/checks/handoff_pending.py --status | sed -n 's/^handoff_total=//p')
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
   · Закрыть два RED actual-R7 startup version-admission теста в tests/composition/test_r14_startup_version_admission.py: authentic V1 typed no-write и missing-lock no-repair; затем остальные current/version/entrypoint recovery controls
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

Agent-кейсов нет по встроенной команде сборщика: регрессия вакуумна — прогонять нечего.
Продуктовые RED не превращены в agent replay, частота ошибок агента не измерена.
Самостоятельный захват входа текущих эпизодов не выполнялся; невозможность захвата
не установлена. До предложения нового agent-правила нужен отдельный faithful вход
«запрос — мир — харнесс», затем old/new replay по §3.

## Фрикшены

| Фрикшен | Развилка | Адрес / наблюдаемый результат |
|---|---|---|
| Startup routing был зелёным с подменённым общим suffix verifier | Не встретиться: проверить реальный decisive consumer | `test_r14_readiness_startup_suffix.py`; настоящее MCP исключение привело к `4ceaf14`, не к ослаблению generic helper |
| Первый marker fixture подменял также `_pending` | Не встретиться: читать зависимости проверяемого helper | Cold plan потребовал настоящий parser; тест теперь отвергает orphan/duplicate/unknown markers и изменённое после selection решение |
| Новый fixture импортировал несуществующий `R13PlanningRuntime` | Сгладить: сверить фактический symbol | Реальный класс `R13Runtime`; setup отказ отдельно от RED 2 failed/2 passed, наблюдение ниже |
| Пропущенный forwarding повторял полный historical fold | Сгладить: count-check конкретного участка | `9e18a33`, `096c8c6`; счётчики caller/builder и effects SQL, без native wall-time обещания |
| No-write startup пока вызывает mutating constructors | Не встретиться: admission до constructors | Два actual R7 RED, handoff54; `2e7c27f` даёт только existing-lock primitive |
| Native проверка длительная | Сгладить наблюдение без повторного запуска | Один frozen process5896 и MCP57b73aa6; pause показал Prepare reread, процесс продолжен и завершился exit0; таймаут не превращён в terminal |
| Сохранённый handoff дорог для пересказа | Сгладить учётом без потери ID/владельца | Все 25 записей сохранены; эта стоимость не обосновывает изменение порога без частотной телеметрии |

## Классы, а не случаи

Подтверждён продуктовый класс: stub вырезает именно тот consumer predicate,
который ограничивает реальную историю. Предыдущие SUSP command/representation
наблюдения и текущая reduction дают родственные стыки, но не независимую частоту
агентских сбоев. В настоящем MCP policy-only diagnosis общий suffix handler поймал
`OwnerJournalIntegrityError`: reduction физически COMPLETE и MATERIALIZED, current
C29 не равен anchor C28. Stub materialized возвращал bool без этого predicate.
Политика startup уже проверяла pending readiness, но до неё выполнение не доходило.
В `4ceaf14` только startup читает точный journal marker, затем сохраняет physical,
interval, prefix и catalog проверки; writer/recovery generic helper остаётся строгим.

Теперь тестовый путь использует настоящие R13 `_pending`/exact lookup и R14 physical
helper. RED 2 failed/2 passed исполнил именно преждевременный anchor guard. После
ремонта 48 startup cases прошли, включая downstream unauthenticated readiness
rejection до recovery и прямое сохранение строгого generic helper. Resolution,
witness, resume authority и persistence authentication в этом fixture контролируются:
он сам не доказывает весь native startup. После черновика реальный process5896
завершился exit0: ordinary startup и явная полная selected-readiness проверка прошли.
Отдельный `/private/tmp/r14-after-marker-repair-probe-report.json` сохраняет SQL
head29/commitment/custody transfers; pending стал пустым и anchor перешёл C28→C29.
Journal, anchor, broker/read-ledger и sidecars менялись законным recovery/startup;
неизменность всех файлов не заявляется. Fresh two-seal и version admission OPEN.
Policy-only diagnostic bypass был временным наблюдательным
контролем и не принимался за ordinary startup evidence.

Отдельный actual public R7 opener с authentic V1 fixture допускает legacy и пишет
broker/read-ledger файлы; при отсутствующем lock создаёт его. Команда
`.venv/bin/pytest -q tests/composition/test_r14_startup_version_admission.py --tb=short`
дала 2 failed in 2.72s. Этот файл — явный untracked WIP вне checkpoint primitive,
без xfail, сохранён в handoff54. Полный CURRENT/version admission не смонтирован.
Зелёный `hold_existing` не является доказательством отказа неподдерживаемой версии.

Успешные приёмы имеют существующий адрес: §6 — реальные критерии, bad input и
наблюдаемый результат; §11 — локальное исправление установленного механизма;
§1 — холодный контур с потолком общей модели. Новая норма из успеха без человека
не вводится. Повторное объяснение не считается новым исполнимым принуждением.

## Кандидаты в правила

| Кандидат | Проверка основания | Решение / адрес |
|---|---|---|
| Новое правило «проверять настоящий helper» | Уже §6/§11; product RED не проверяет incremental agent-rule effect | Не дублировать норму; точное наблюдение в `.harness/observations.md` |
| Дополировать debugger/fixture instructions из wrong import | Малый setup lapse; старое требование прочитать actual code уже применимо | Наблюдение, без универсального guard и без заявления невозможности replay |
| Изменить highlight/handoff пороги | Нет durable частоты/полугодового trace | Порог не менять; прежнее highlight-observability наблюдение остаётся открытым |
| Считать tests primitive закрытием admission | Наблюдаемый actual startup этому противоречит | Продуктовый handoff54 и `R14-DURABLE-VERSION-ADMISSION.md`, не правило харнесса |

Холодный рой `/private/tmp/r14-marker-retro-swarm.md` прошёл шесть итераций и все
пять линз. Его вывод — основание для проверки владельцем, не внешняя верификация.
Холодный контур прочитал фактический файл и проверил native delta: P0=0, P1=0,
`/private/tmp/r14-marker-retro-cold-review.md`. Root перечитал отчёт и источники.

Последний содержательный шаг — классификация по
`.agents/skills/retro/references/actions.md` после холодного контура:

| Находка | Класс | Адрес / действие |
|---|---|---|
| Реальный materialized predicate и делегированный pending-parser скрывались stub | Наблюдение о product seam; faithful agent replay не выполнен | `.harness/observations.md`, real materialized and pending-parser omitted by seam controls; продуктовая правка `4ceaf14` уже отдельным коммитом |
| Неверный импорт класса до критериального RED | Несозревшее наблюдение как есть | `.harness/observations.md`, setup symbol guessed before fixture; новую норму не вводить |
| Unsupported legacy admission действительно пишет до version refusal | Продуктовое обязательство | Handoff54, actual RED WIP и `design-docs/R14-DURABLE-VERSION-ADMISSION.md`; primitive не закрывает его |
| Narrow marker fix, счётчики, source freeze и настоящий preserved fixture сработали | Применение действующих §6/§11, не новая норма | Результаты тестов и native receipt в этой записи; предложения новой нормы нет |
| Нагрузка handoff/highlight не измерена во времени | Уставка не обоснована | Прежняя highlight-observability запись `.harness/observations.md`; пороги не менять |
| Механические checks/self-tests прошли | Существующие проверки, нового дефекта не выявлено | Приведённые команды/логи; gate/правила не изменять |

Все находки получили адрес; новых P0/P1 к ремонту нет. Дальше только сверка точного
snapshot, механические проверки и Markdown checkpoint. Дополнительный generic
full-diff red-team для checkpoint не запускается по явному AGENTS §7; отдельный
холодный контур самого retro-файла выполнен.

## Инварианты модели и долги

| # | Проверка | Вердикт в охвате |
|---|---|---|
| 1 | `python3 .harness/scripts/checks/rules_have_reproducers.py` exit0; `--status`: 2 правила, 1 норма, 56 дней до ревизии | Структурный гейт прошёл, не вакуумен; эффективность agent-правила этим не доказана |
| 2 | AGENTS §1–11 прочитаны | Каждый раздел называет gate/partial либо честный `[без гейта]` |
| 3 | `sh .harness/scripts/gate.sh --self-test`, exec8495 exit0, `/private/tmp/r14-marker-retro-selftest.out` | Bad/good пары прошли; layout14 passed, collector10 passed/9 deselected, tooling59 passed; полный продуктовый набор не запускался |
| 4 | `python3 .harness/scripts/checks/messages_are_actionable.py` exit0 | Статический охват сообщений checker, не всех ответов внешних инструментов |
| 5 | `git diff 7c85226..2e7c27f -- .harness/scripts/gate.sh` пуст | Ни одной строки run за период не удалено |
| 6 | Прочитаны thresholds и потребители healthcheck/guard/new-case; cadence/skip/revision constants в своих checks | Нового дублирования в проверенных потребителях нет; глобальная теорема не заявлена |

`python3 .harness/scripts/checks/checks_are_wired.py` также exit0. Формальных долгов
модели было 0, осталось 0, закрыто 0, заведено 0 — таблица `.harness/HARNESS_MODEL.md`
пуста. Отдельно product handoff вырос 24→25: добавлен конкретный admission RED,
ничего не закрыто. Новых agent cases нет; причин считать продуктовые обязательства
долгами модели или закрытыми нет.

## Уставки хайлайтов

Прочитаны `.harness/scripts/thresholds.sh`, `healthcheck.sh`, `guard-edit.sh`,
`new-case.sh`, cadence/skip/revision consumers. Их исполняемые источники за период
не менялись (`git diff 7c85226..2e7c27f -- .harness AGENTS.md` пуст).
Нет durable истории hard-level срабатываний и частоты highlights, поэтому самый
частый и полугодовое молчание не установлены. Уставки не изменены; это отсутствие
измерения, а не подтверждение оптимальности порогов. Существующая запись highlight
observability остаётся адресом неизвестности. До этой записи `retro_due --status`
дал since5/every5/skips1/max4; proposed skip находится в разрешённом лимите, предел
самостоятельно не повышается.

## Пропускная способность

AGENTS и модель прочитаны; непригодного для чтения файла харнесса в этом охвате не
обнаружено. Большой обязательный input snapshot оставлен точным, а не переписан по
памяти. Его code fence побайтно сверен с stdout единственного `new-retro.sh` запуска
`/private/tmp/r14-startup-marker-retro-inputs.out`. Снимок фиксирует момент сборки,
включая временные collector files; не следует выдавать его dirty count за текущий.

## Итог

`outcome: skip`: конкретный класс записан, product repairs и RED
сохранены, но независимого agent replay, обосновывающего новую норму, нет. Нового
дефекта механического харнесса пары не показали. Это не закрывает R14, версионную
admission, все startup контроли или ни один product handoff. Классификация выполнена,
наблюдения добавлены; остаётся механическая сверка и отдельный Markdown checkpoint.
