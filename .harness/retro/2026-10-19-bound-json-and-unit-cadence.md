---
date: 2026-10-19
# trigger: cadence | incident
trigger: cadence
# outcome: change | skip
outcome: skip
# id правила, если outcome: change
rule:
# почему нет, если outcome: skip
reason: Критерии уже заданы AGENTS §6/§11 и seam-вопросом шаблона; конкретные исполнимые продуктовые проверки исправлены. Дополнительный общий запрет doubles или поиск sentinel по AST не различает достигнутую границу и ранний выход, а для смены порогов нет телеметрии. Повторы оставлены в копилке; исправление агентной привычки и невозможность replay не заявляются.
---

## Что перечитано

При заведении записи `new-retro.sh` встраивает сюда точный stdout штатного сборщика.
Числа остаются рядом с командами, которыми получены; переписывать их по памяти нельзя
(`AGENTS.md` §6).

```text
входы ретро — с последней ретро (92c86b4)

1. КОПИЛКА  .harness/observations.md
   записей: 51   (grep -c '^- 2[0-9][0-9][0-9]-' .harness/observations.md || [ $? -eq 1 ])
   - 2026-10-18 | fixture drift and post-open ownership at integration closure | Период 516ec00..7b92ea2 отделил устаревшие digest/bounds и cause expectations от реальных H1 trust-boundary и post-open cleanup дефектов. Product RED: 16/33 для delivery, 11/23 для preparation, 4 открытых FD после отказа; выбранные GREEN 72, 34 и 18 tests. Legacy response/cancellation nodeids всё ещё доказывают только preflight/no-IPC. Это родственно incidental-refusal/fixture-asymmetry наблюдениям, но не faithful agent old/new replay; новая норма не вводится, невозможность replay не заявляется. | .harness/retro/2026-10-18-fixture-and-resource-closure.md; tests/composition/test_h1_delivery_evidence_contracts.py; tests/composition/test_h1_completion_preparation_session.py; tests/composition/test_h1_evidence_mount_enrollment.py
   - 2026-10-16 | nominal provenance misses evaluated control and effect multiplicity | Actual-source RED в anchor proof принимали внутренний путь другого домена, descriptor в exception cause, journal в diagnostic, object hook, чужой state и повтор разрешённого commit. eab840c добавил конечные guard/data/receiver checks; старый consumer отдельно поймал смешение forward и SQL roles, его ожидание сохранено. Root и checkpoint дали по31 passed. Это продуктовая серия под §6/§11, не проверенная эффективность нового agent-rule; paired agent replay не выполнялся, невозможность не заявляется. | tests/verification/test_authority_descriptor_closure.py; .harness/retro/2026-10-16-r14-protected-anchor-cadence.md
   - 2026-10-16 | incidental refusal masks semantic mutation | В anchor controls foreign() давал GREEN из-за сдвига SQL occurrence labels; constructor-domain RED был лишь несовпавшим regex при правильном отказе. Call-free self._path и return None реально принимались:2 failed/2 passed3.30s; после repair final34 passed. Product tests теперь различают этот стык; нового провала агентной инструкции отдельной тройкой не установлено, новая норма не вводится. | tests/verification/test_authority_descriptor_closure.py; /private/tmp/r14-protected-anchor-control-precise-red.log; .harness/retro/2026-10-16-r14-protected-anchor-cadence.md
   - 2026-10-16 | repeated history projection and unresolved writer staleness | Frozen c22afb0 свежий normal-clock two-seal завершился1failed3044.08s: второй preview73.275s, затем accept15.507s writer STALE. Diagnostic valid/no faults;271 history projections включают268 вне public phases,2444.577s inclusive с перекрытием. Точная stale-граница и безопасно устранимые повторы не установлены; credentials не replay authority, текущий HEAD этим не проверен. Root сохранил45файлов1274120788bytes с before/after/copy equality и повторной проверкой frozen hashes/modes. | design-docs/R14-VERIFICATION-STATUS.md; handoff31/34; /private/tmp/r14-runtime-phase-v2-c22afb0-observed/failure-files.json
   - 2026-10-16 | nominal receiver proof misses nonlocal binding forms | В25e719d..3099ff6 connected SQL evidence chain ещё пропускала container self aliases, def/class/import вместо Name(Store), positional constructor reorders, method/decorator/import shadows и implicit callback defaults. Root прочитал delegated implementation, получил точные RED (включая3 container,6 non-Name и5 mount-default cases), затем patched verifier и148 focused tests passed. Shared _binds и source/argument/escape checks исправляют product proof; отдельного old/new agent replay нет, невозможность построения не заявляется. Существующие AGENTS§6/§11 уже охватывают плохой вход и текущий контракт, новая норма не вводится. | product3099ff6; tests/verification/test_planning_descriptor_chain.py; .harness/retro/2026-10-16-r14-planning-descriptor-chain.md
   - 2026-10-16 | connected proof helpers lack startup consumer | identifier_sql_families→sql_expression_inventory→planning_descriptor_chain — реальная source evidence цепочка; последний builder пока потребляется verifier tests/candidate export, а _open_runtime всё ещё создаёт protected components без mandatory global version admission. Три owned public startup RED сохранены без xfail; fragment/corpus require_closed отказывают. Это текущие prerequisite progress и integration risk, не доказательство waste или эффективности новой нормы; helper PASS не закрывает публичный исход. Перед admission claim нужен полный proof consumer до effects; handoff54/58 уже держат работу. | design-docs/R14-VERIFICATION-STATUS.md; tests/composition/test_r14_startup_version_admission.py; новая норма не вводится
   - 2026-10-16 | handoff navigation without authority mutation | В сессии несколько полных handoff reads обрезаны tool output cap; это сессионное свидетельство, не факт встроенного снимка. Additive handoff.sh --summary сохраняет все parsed fields, ID/порядок/статус и timestamp по одной строке, без ack/close или записи файлов. Три CLI случая дали unknown argument/exit2 до правки; затем summary3 + identifier8 дали11 passed. Выигрыш — навигация, не bounded tokens: текущий журнал19411bytes/149lines, summary18566bytes/27lines (subprocess обоих CLI). Нового agent-rule или эффективности инструкции не заявляется. | tooling change .harness/scripts/handoff.py/.sh; tests/tooling/test_handoff_summary.py; .harness/retro/2026-10-16-r14-sql-families-and-observer.md
   - 2026-10-16 | source accounting and binding stronger than syntactic evidence | В период901aaaa..51f963e actual-source mutants нашли неполный src boundary, empty/orphan accounting, shadow/alias/nested-frame/leaf-export и manifest-parameter bindings; generated assembly baseline также не фиксировал независимые origins. Product RED regressions привели к finite refusal и literal expected edges; пять import/def/class/global/pattern mutants дали DID NOT RAISE до shared binding repair. Corpus/constructor/assembly require_closed всегда отказывают, три public startup admission RED остаются открыты. Это повторяющийся продуктовый класс под AGENTS sections6/11, не измеренная эффективность нового agent rule: faithful old/new agent replay не запускался, невозможность построения не заявляется. | продуктовые исправления506a59a/f072a99/2e7083d/51f963e; .harness/retro/2026-10-16-r14-sql-admission-cadence.md; global admission OPEN
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
   - 2026-10-16 | real materialized and pending-parser omitted by seam controls | Обычный saved-bundle startup после локальных routing positives отказал на policy. MCP policy-only диагноз поймал generic suffix handler → reduction startup → R14 `_loop_decision_materialized`: COMPLETE/MATERIALIZED при C29/C28 отвергался до readiness branch. Старый fixture возвращал bool вместо этого predicate. Cold plan также нашёл, что exact marker lookup делегирует integrity в `_pending`, оставленный stub; fixture переведён на реальные parser/lookup/physical helpers. Настоящий RED 2 failed/2 passed, локальный startup-only ремонт `4ceaf14`, 48 startup cases passed; strict generic helper сохранён. Отдельный обычный frozen startup5896 затем завершился exit0 с full selected-readiness validation, SQLhead/commitment/custody transfers сохранены, legitimate recovery journal/anchor/sidecars менялись. Это product-seam класс под действующими §6/§11, не agent replay и не full R14 verdict. | новое agent-правило не обосновано; `.harness/retro/2026-10-16-r14-startup-marker-admission.md`
   - 2026-10-16 | setup symbol guessed before fixture | Первый upgraded marker fixture импортировал выдуманный `R13PlanningRuntime`; фактический класс в прочитанном source — `R13Runtime`. Этот ImportError исправлен и не назван RED критерия; следующий запуск исполнил нужный anchor predicate и дал 2 failed/2 passed. Это отдельный setup lapse под действующими требованиями чтения/проверки результата, а не доказанная дыра инструмента. Faithful agent input и old/new replay для него не выполнялись; невозможность построения не утверждается. | наблюдение без новой нормы; `.harness/retro/2026-10-16-r14-startup-marker-admission.md`
   - 2026-10-16 | recovered OPEN lost in next-Turn context | Full frozen native6529 terminated exit1 in4554.71s: original resolution/reduction/resume and next-Turn publication passed, but context projection required direct-terminal for RECOVERY_REQUIRED. Exact retained bytes reproduce the error;4acbc4f adds the original continuation proof join and preserves original terminal plus recovered outcome.13 captured-input cases and75 neighboring owner checks pass. New full-native55741 remains RUNNING; helper evidence does not prove second seal or version admission. Related typed-provenance/consumer-seam class is product evidence under AGENTS sections6/11; no faithful old/new agent replay constructed for this episode, impossibility not claimed. | product repair4acbc4f; full R14 OPEN; .harness/retro/2026-10-16-resolved-open-context.md
   - 2026-10-17 | local durable inbox before PostgreSQL identity admission | `329b644` создавал/pin-ил `LocalCliDeliveryProvider` до PG principal/session authentication; `4866099` перенёс `_context` перед provider. Actual subprocess negative в `tests/cli/test_native_chat.py` меняет principal/session, требует отказ, пустой stdout и отсутствие inbox. Root сообщил исторический RED1failed1.68s и GREEN3passed5.76s/CLI10passed4.83s; исходный RED log/handle после compaction не сохранён, независимо он здесь не перечитан. Класс продуктовый: durable side effect до полной authority admission; entitlement proof не заменяет identity. Faithful old/new agent replay не выполнялся, невозможность построения не заявляется; новая норма не вводится. | product repair4866099; .harness/retro/2026-10-17-r15-r17-functional-cadence.md
   - 2026-10-17 | isolated dependency sequencing and lint lapses | Root сообщил R16 worktree NameError из-за cherry-pick без prerequisite9c471e8; MAIN не повреждён. Preflight Ruff list-concat был исправлен в4866099. Исходные execution handles этих двух эпизодов ретро не сохранила и самостоятельно RED не повторяла; это сессионные промахи, без доказанного общего класса и faithful agent replay. Новая норма/автоматизация из них не выводится. | .harness/retro/2026-10-17-r15-r17-functional-cadence.md
   - 2026-10-18 | dependent shell failure hidden by later command status | Formatting assertion и недоступный python не остановили последующие зависимые действия; первый порядок — свидетельство сессии, retained commit log отдельно показывает только checkpoint. Existing developer instructions уже требуют последовательности и exits; faithful old/new agent replay не выполнен, невозможность не заявляется. Fail-fast correction применена, новая норма не вводится. | .harness/retro/2026-10-18-runtime-closure.md; /private/tmp/chiplog-merge-process-incident.md
   - 2026-10-18 | nested instruction and checkpoint scope misses | Author/cold review пропустили явный tests/AGENTS2 запрет test-module import; до коммита тело теста без изменения перенесено к local builder. Дорогие mounted modules ошибочно попали в checkpoint и были остановлены; focused срез прошёл, full collection не менялась. Это пропуски existing instructions, не доказанный agent-rule gap; точное различение и nodeid mapping в ретро, replay не выполнен. | .harness/retro/2026-10-18-runtime-closure.md; tests/AGENTS.md §2; AGENTS.md §7
   - 2026-10-18 | cold reviewer exposed to ancestor summaries | Whole-diff reviewer вызвал list_agents и увидел исторические сводки после cold start. Сам раскрыл предел; его результат оставлен supporting, mandatory final review требует свежего reviewer без agent metadata/author reports. Existing AGENTS6 уже требует isolation; отдельного faithful replay нет, новая норма не установлена. | .harness/retro/2026-10-18-runtime-closure.md; /private/tmp/chiplog-final-whole-integration-swarm.md
   - 2026-10-18 | repeated dependent shell status after ambiguous edit anchor | Authored root session note: /private/tmp/chiplog-edit-historical-types.py отказал на неуникальном import json anchor после source-части, но внешний shell без set -e продолжил Ruff и вернул успех последнего шага. Root сообщил частичное состояние, исправил anchor и порядок исполнения, отдельно проверил diff/AST/test outcomes до 3bfec2f. Родственный эпизод уже есть в 2026-10-18-runtime-closure. Это повтор наблюдаемого механизма, не faithful agent old/new replay: исходный полный ввод не сохранён, невозможность построить его не установлена; existing instructions уже требуют exits/dependency order. Новая норма не вводится. Дополнение периода 9cfc495..af85aa8: exact-edit assertion отказал до записи platform import changes, но shell без set -e запустил тесты старого source; misnamed chiplog-dispatch-spawn-import-green.log дал 2 failed/4 passed143.75s. После явного раскрытия и точной правки с set -e actual-green дал6passed147.82s, checkpoint8passed182.69s. Это повтор того же агентного механизма, не новый agent replay; новая норма/эффективность не заявляется. | .harness/retro/2026-10-18-merge-typing-cadence.md; /private/tmp/chiplog-edit-historical-types.py; .harness/retro/2026-10-18-dispatch-and-spawn-closure.md
   вопрос: что здесь ломается ПОСТОЯННО, а не что сломалось

2. ДИФФЫ  92c86b4..HEAD
   87037fa test(r14): type the authenticated suffix forwarding probes
   df5bcc0 test(r14): prove accounting collection reaches the gated R16 read
   cef6826 fix(h1): recognize source-bound scoped runtime manifests
   019190a perf(r14): encode first-path fingerprints with native JSON
   d464203 test(h1): bind recovery handoff and cancellation to current contracts
   объём: 12 files changed, 718 insertions(+), 151 deletions(-)   (git diff --shortstat 92c86b4..HEAD | sed 's/^ *//')
   незакоммиченного сейчас (в период НЕ входит): 3   (git status --porcelain | wc -l | tr -d ' ')
   коммитов периода: 5   (git rev-list --count 92c86b4..HEAD)
   коммитов с R8 catalog: 0   (git rev-list --count 92c86b4..HEAD -- src/chiplog/inert_shared/r8-implementation-v1.json)

3. ХВОСТЫ  .harness/handoff.md
   записей: 28   (python3 .harness/scripts/checks/handoff_pending.py --status | sed -n 's/^handoff_total=//p')
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
   · Связать точный current-contract extension registry с зарегистрированным durable-version descriptor и authenticated CURRENT до финальной R14 интеграции
   · R14 global admission: воспроизвести owner pre-import ENV/file/native confinement на свежих sentinels, реализовать authenticated OS/image launch boundary и замкнуть конечный authority-origin/readgraph/schema/AMR criterion до финальной R14 интеграции
   · R15-R17 optional assurance B-R15-01/B-R16-01/B-R17-01/B-VERIFY-01 in design-docs/R15-R17-FUNCTIONAL-PLAN.md; add cross-restart host clock rollback protection to B-R15-01
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

## Период и границы доказательства

Пять коммитов `92c86b4..87037fa` разобраны по фактическому диффу; команды и
объём сохранены в снимке выше. После снимка новые commits в период не включались.
Root владеет исправлениями; рой — read-only supporting-анализ, шесть итераций,
все пять линз, `/private/tmp/chiplog-bound-json-unit-retro-swarm.md`.
Это общий модельный контур, не независимая внешняя сертификация.

| Изменение | Исполнимое наблюдение | Предел |
|---|---|---|
| d464203 | B handoff связан с recovery source/context/lease и четырьмя фактическими owner frames; checkpoint retry 1 passed, до него 12 неизменённых случаев прошли | Recovery не создаёт first-path cut. Cancellation seams проверяют task ownership, не реальный mint |
| 019190a | MCP RED std=1/native=0; GREEN для трёх доменов std=0/native=1, один preimage, byte_equal=True; 76 соседних тестов и 13 checkpoint | UInt64/Unicode и narrow native-error fallback проверены; codec не заменяет весь JSON проекта |
| cef6826 | Точный bound template равен настоящему manifest при несовпадении `is`; 3 mounted producer tests passed; V22 owner и routing 5 passed | Подмена broker identity и local profile отвергаются; не глобальная CURRENT/OS certification |
| df5bcc0 | Новый R16-held control дал RED 0 вместо 1; GREEN ровно один R16 вход после baseline/collect-one под held gate; module 97 passed | Typed autospec — unit forwarding, не authenticated producer |
| 87037fa | Пять suffix verdicts, два включённых intent и исключённый foreign intent сохранены; module 97 passed | Источник в unit не аутентифицируется; AST остальных 20 функций совпал |

Команды чтения retained evidence: `cat /private/tmp/chiplog-first-path-native-json-debug-{red,green}.json`,
`cat /private/tmp/chiplog-first-path-encode-benchmark.json`,
`tail -n 5 /private/tmp/chiplog-current-h1-producer-three-green.log`,
`tail -n 5 /private/tmp/chiplog-h1-scoped-profile-selection-green.log`,
`cat /private/tmp/chiplog-accounting-first-two-unit-red.log`,
`tail -n 5 /private/tmp/chiplog-accounting-prefix-unit-green.log`.
V22 mounted log предшествует уточнению только return annotation; body AST проверен
`/private/tmp/chiplog-h1-profile-routing-annotation-proof.json`.
Окончательные retained-wire V1/V2 routing controls дали 4 passed in 0.70s.

Чистое кодирование fixture-preimages ускорилось в 11.38/9.57/6.77 раза при
побайтовом равенстве. В измерение не входят model_dump, hash, SQL, IPC и E2E;
это не обещание такого ускорения R14. Полный mypy остался красным: 2393 → 2378 → 2369,
новых сообщений нет при Counter-сравнении без номеров строк; команды —
`.venv/bin/mypy src tests`, журналы `chiplog-h1-bound-profiles-typed-mypy.log`,
`chiplog-accounting-first-two-mypy.log`, `chiplog-accounting-prefix-unit-mypy.log` в `/private/tmp`.
Строгий source catalog не repin-ился. Full be93 baseline ещё не завершён;
он не проверяет текущий кандидат. После снимка два current scoped recovery tests
завершились 2 passed in 554.93s: canonical inner request, настоящий terminal owner,
девять записей и replay без owner calls; лог
`/private/tmp/chiplog-current-scoped-recovery-two-repro.log`.
`git diff --name-only cef6826..87037fa -- src` пуст: production bytes этих снимков одинаковы.
Restart/cancellation controls, полная регрессия и merge остаются OPEN.

## Регрессия кейсов

Снимок `find .harness/reproducers -name case.md` содержит ноль кейсов:
регрессия agent replay вакуумна — прогонять нечего. Долей old/new не выдумываем.
Product RED/GREEN выше не является agent replay или измерением эффективности инструкции.
Новый faithful agent input и paired replay не исполнялись; их невозможность не утверждается.

## Что получилось и что не с первого раза

Успешный повторяемый приём — назвать наблюдаемый стык до проверки: точные байты
preimage, identity bound manifest, конкретное downstream событие. Это уже дом
AGENTS §6/§11; закрепление здесь — реальные regression assertions, а не новая норма.

Три различимых механизма:

1. **Ранний выход до заявленного стыка.** Старый accounting stub не имел selected_seal;
   пойманный AttributeError давал None до R16. Прежний collect count проходил.
   Только добавленный R16-held control показал настоящий RED. Это родственно
   incidental-refusal и producer/stub-asymmetry наблюдениям; исправлен конкретный тест.
2. **Сравнение nominal template после binding.** Opener создаёт manifest с текущим
   broker version, а `is` отвергал его и выбирал V1 вместо V22 V2. Exact recomputed
   equality и отрицательные mounted controls исправляют продуктовый стык, не расширяют admission.
3. **Setup до target.** Временный debugger runner импортировал project modules вне main,
   затем использовал неканонический macOS путь. Отказы attestation/custody предшествуют target,
   поэтому не считаются его RED. Недоступный wait_for_stop отклонён controller inventory;
   finally закрыл собственную сессию/сервер. Чтения выдуманных путей также были setup misses,
   исправленные чтением actual inventory; существующая инструкция не была новой дырой нормы.

B fixture сначала требовал all-clearance history; checkpoint отказал, потому что
это не durable history. Проверка заменена фактическими четырьмя owner frames.
Не утверждается, что runtime доказанно переиспользовал id, и allocator не изменялся.
Новый V2 unit fixture сначала расплющил strict dataclass через model_dump;
нормальная валидация исходных typed fields исправила setup без model_construct/ignore.

## Фрикшены

| Фрикшен | Развилка | Адрес и исход |
|---|---|---|
| Неполный stub даёт подходящий None раньше R16 | Не встретиться | tests/composition/test_r14_native_selected_accounting_fast_path.py; R16-held exactly once, AGENTS §6 уже действует |
| Static identity против bound manifest | Не встретиться | h1_producer_sources.py и h1_completion_preparation_session.py; exact bind equality, scoped/local и broker negatives |
| Дорогой mounted цикл | Сгладить | AGENTS §7; быстрые routing/byte probes плюс настоящий mounted результат, без нового scheduler |
| Spawn imports и /var path в runner | Не встретиться | Временный runner исправлен по реальному opener/custody contract; обновлена существующая копилка |
| Unsupported debugger API, угаданные source paths | Не встретиться | Actual tool/file inventory; сохранены границы отказа, новых launcher/framework правил нет |
| Пересказ 28 handoff entries | Сгладить | Существующий handoff.sh --summary; номера/статусы не менялись, хвосты этим ретро не закрыты |

## Классы, не новые нормы

Структура doubles и наличие assertion сами по себе не показывают, какое событие
должно быть достигнуто. Текущие tests намеренно используют doubles для forwarding;
запрет на них отверг бы допустимый unit test и не доказал бы реальный authority seam.
Это конкретный предел предложенного AST/style guard, а не доказательство невозможности
любой будущей автоматизации. Имеющийся шаблон ретро уже спрашивает про actual producer
bytes, consumer и одностороннюю подмену. Новое текстовое напоминание дублирует вопрос.
Повтор агентного setup miss остаётся наблюдением, не объявляется исправленной привычкой.

## Кандидаты в харнесс

| Кандидат | Адрес | Решение и проверяемая причина |
|---|---|---|
| Ещё одно требование sentinel/реального стыка | AGENTS.md §6, retro/TEMPLATE.md | Не добавлять: оба уже требуют наблюдаемый критерий и producer→consumer; реальный R16 predicate теперь исполняется в тесте |
| Общий запрет doubles / AST поиск sentinel | .harness/scripts/checks/ | Не добавлять: форма не различает meaningful reach и ранний выход; конкретный failing input закрыт product assertion |
| Перенастройка шумных highlights | thresholds.sh | Не менять: нет durable частоты/активаций, большой handoff — реальная незакрытая работа |
| Изменение модели/новая норма | HARNESS_MODEL.md | Не менять: в этом периоде нет нового расхождения кода и модели, сравнительный эффект нового intervention не установлен |
| Удержать серию наблюдений | observations.md | Дополнить related incidental-refusal, spawn diagnostic и debugger setup строки; не считать это outcome:change |

## Инварианты модели и долги

| # | Наблюдение и команда | Вердикт |
|---|---|---|
| 1 | python3 .harness/scripts/checks/rules_have_reproducers.py; --status | Exit0, 2 rules/1 norm; структурный verdict, не agent efficacy |
| 2 | Снимок grep AGENTS sections и чтение AGENTS.md | Все 11 разделов имеют gate либо [без гейта] |
| 3 | sh .harness/scripts/gate.sh --self-test | Exit0; actual bad/good пары, generation checks, 14/10/59 targeted tests; не full product suite |
| 4 | python3 .harness/scripts/checks/messages_are_actionable.py | Exit0 в собственном охвате; речь вне охвата оценена отдельно |
| 5 | git diff --name-only 92c86b4..87037fa -- .harness/scripts/gate.sh | Пусто; ни один run не исчез |
| 6 | thresholds.sh и consumers healthcheck/guard-edit/new-case; diff периода | Новых дубликатов порогов не введено; skip/revision limits принадлежат своим checks, глобальный semantic-equivalence proof не заявляется |

`checks_are_wired.py` также завершился Exit0. Retained self-test log:
`/private/tmp/chiplog-bound-json-unit-retro-selftest.log`.
Формальных model debt rows было 0, осталось 0; handoff — отдельные 28 записей.
Команды подсчёта — в точном snapshot. Ни один handoff этой ретро не закрыт,
новых решений/NOW/отсрочек нет. Root сообщил о сохранении 23 исходных user untracked
entries при FF, но исторические before/after captures не сохранены отдельным manifest.
Ретро и холодный reviewer не перепроверяли этот исторический результат независимо;
это атрибутированный сессионный отчёт, не установленная здесь гарантия данных.
Текущий `/private/tmp/chiplog-ff-integrated.py` читает список через
`git ls-files --others --exclude-standard -z`, сравнивает bytes обычных файлов,
symlink targets и dev/inode только директорий, держа captures в памяти.
Просмотр helper или сегодняшнего inventory не доказывает прошлую сохранность;
число 23 не относится к snapshot строке про три незакоммиченных артефакта.
Формальные долги этим сообщением не меняются.

## Уставки

Durable telemetry срабатываний hard guard и шестимесячного молчания не установлена.
HC_FILES/HC_HANDOFF/HC_AGENTS/HC_COOLDOWN и case bounds сохранены. Наличие 28 хвостов
не оправдывает поднятие handoff threshold. До этой записи retro_skips=2,
retro_max_skips=4 (`retro_due.py --status`); текущий skip станет третьим в пределах нормы.

## Холодный контур

Отдельный no-parent swarm-red-team прочитал файл и исходные evidence:
`/private/tmp/chiplog-bound-json-unit-retro-cold-rt.md`. P0=0/P1=0; одна material P2:
в тексте не было evidence address для user-data preservation. Root уточнил утверждение
как сессионный отчёт без независимо проверенного исторического manifest, указал
helper и точные пределы его проверок. Целевой cold recheck закрыл P2:
новых P0/P1/material P2 нет, исходный отчёт дополнен reviewer.
Это общий модельный cold контур с потолком независимости, не внешняя сертификация.
Final whole-diff red-team для merge этим не заменяется.

После закрытия recheck root прочитал `.agents/skills/retro/references/actions.md`.
Следующая таблица — последний содержательный шаг ретро; далее только проверка/коммит.

## Финальная классификация находок

| Находка | Класс actions.md | Адрес и результат |
|---|---|---|
| R16 критерий проходил до downstream read | Продуктовая ошибка; агентный механизм без paired replay остаётся наблюдением | df5bcc0/87037fa tests/composition/test_r14_native_selected_accounting_fast_path.py; обновлена observations.md строка incidental refusal |
| Bound manifest отвергался или выбирал V1 | Продуктовый representation mismatch, не новый agent-rule | cef6826 h1_producer_sources.py/h1_completion_preparation_session.py; actual mounted и substitution controls в tests/composition |
| Повтор spawn setup до target | Провал наблюдался, faithful agent input не прогонялся | observations.md строка spawned diagnostic runner imports дополнена фактическим повтором и пределом |
| Unsupported debugger operation и угаданные пути | Наблюдение под существующей инструкцией; эффективность её ремонта не измерена | observations.md строка debugger and review target execution lapses дополнена; невозможность replay не заявлена |
| Codec bytes/счётчики и точные frames оказались полезны | Успех, новой нормы не предложено | 019190a/d464203 и этот отчёт; AGENTS §6 уже действует, норменную ветку за человека не выбираем |
| Предложение общего doubles/sentinel guard | Кандидат отклонён по различающей способности | Этот файл, «Кандидаты в харнесс»; сохраняются executable product predicates вместо нового style blocker |
| Дорогие runs, handoff и thresholds | Фрикшен сгладить; уставка без telemetry не обоснована | Этот файл, «Фрикшены»/«Уставки»; AGENTS §7 и handoff.sh --summary оставлены, долг не объявлен закрытым |
| Формальные долги отсутствуют, agent cases отсутствуют | Вакуумный verdict, без выдуманной регрессии | Встроенный snapshot и этот файл; строки debt не удалялись, долей replay нет |
| P2 untracked preservation без manifest | Рассинхрон силы утверждения и доступного evidence | Этот файл, user-data paragraph: сессионная атрибуция и точные пределы helper; P2 закрыт cold recheck |


## Итог

`outcome: skip`: конкретные продуктовые ошибки получили исполнимые различающие
проверки; предложенное дублирование инструкций/проверка формы doubles не добавляет
установленного контроля над их семантикой. Телеметрии для смены порогов нет.
Копилка удерживает повтор, agent habit не объявляется исправленным. Мерж не выполнен.
