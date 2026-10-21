---
date: 2026-10-21
# trigger: cadence | incident
trigger: cadence
# outcome: change | skip
outcome: change
# id правила, если outcome: change
rule: pyproject.toml
# почему нет, если outcome: skip
reason:
---

## Что перечитано

При заведении записи `new-retro.sh` встраивает сюда точный stdout штатного сборщика.
Числа остаются рядом с командами, которыми получены; переписывать их по памяти нельзя
(`AGENTS.md` §6).

```text
входы ретро — с последней ретро (95d19fb)

1. КОПИЛКА  .harness/observations.md
   записей: 51   (grep -c '^- 2[0-9][0-9][0-9]-' .harness/observations.md || [ $? -eq 1 ])
   - 2026-10-18 | fixture drift and post-open ownership at integration closure | Период 516ec00..7b92ea2 отделил устаревшие digest/bounds и cause expectations от реальных H1 trust-boundary и post-open cleanup дефектов. Product RED: 16/33 для delivery, 11/23 для preparation, 4 открытых FD после отказа; выбранные GREEN 72, 34 и 18 tests. Legacy response/cancellation nodeids всё ещё доказывают только preflight/no-IPC. Это родственно incidental-refusal/fixture-asymmetry наблюдениям, но не faithful agent old/new replay; новая норма не вводится, невозможность replay не заявляется. | .harness/retro/2026-10-18-fixture-and-resource-closure.md; tests/composition/test_h1_delivery_evidence_contracts.py; tests/composition/test_h1_completion_preparation_session.py; tests/composition/test_h1_evidence_mount_enrollment.py
   - 2026-10-16 | nominal provenance misses evaluated control and effect multiplicity | Actual-source RED в anchor proof принимали внутренний путь другого домена, descriptor в exception cause, journal в diagnostic, object hook, чужой state и повтор разрешённого commit. eab840c добавил конечные guard/data/receiver checks; старый consumer отдельно поймал смешение forward и SQL roles, его ожидание сохранено. Root и checkpoint дали по31 passed. Это продуктовая серия под §6/§11, не проверенная эффективность нового agent-rule; paired agent replay не выполнялся, невозможность не заявляется. | tests/verification/test_authority_descriptor_closure.py; .harness/retro/2026-10-16-r14-protected-anchor-cadence.md
   - 2026-10-16 | incidental refusal masks semantic mutation | В anchor controls foreign() давал GREEN из-за сдвига SQL occurrence labels; constructor-domain RED был лишь несовпавшим regex при правильном отказе. Call-free self._path и return None реально принимались:2 failed/2 passed3.30s; после repair final34 passed. Product tests теперь различают этот стык; нового провала агентной инструкции отдельной тройкой не установлено, новая норма не вводится. Дополнение 2026-10-19: прежний accounting unit возвращал None до R16 на неполном stub; новый downstream control дал 0 вместо 1, typed doubles довели до ровно одного R16 read под held gate (df5bcc0), suffix probes сохранили два seal intents и foreign exclusion (87037fa). Это product-test repair под §6, не agent efficacy; .harness/retro/2026-10-19-bound-json-and-unit-cadence.md. | tests/verification/test_authority_descriptor_closure.py; /private/tmp/r14-protected-anchor-control-precise-red.log; .harness/retro/2026-10-16-r14-protected-anchor-cadence.md
   - 2026-10-16 | repeated history projection and unresolved writer staleness | Frozen c22afb0 свежий normal-clock two-seal завершился1failed3044.08s: второй preview73.275s, затем accept15.507s writer STALE. Diagnostic valid/no faults;271 history projections включают268 вне public phases,2444.577s inclusive с перекрытием. Точная stale-граница и безопасно устранимые повторы не установлены; credentials не replay authority, текущий HEAD этим не проверен. Root сохранил45файлов1274120788bytes с before/after/copy equality и повторной проверкой frozen hashes/modes. | design-docs/R14-VERIFICATION-STATUS.md; handoff31/34; /private/tmp/r14-runtime-phase-v2-c22afb0-observed/failure-files.json
   - 2026-10-16 | nominal receiver proof misses nonlocal binding forms | В25e719d..3099ff6 connected SQL evidence chain ещё пропускала container self aliases, def/class/import вместо Name(Store), positional constructor reorders, method/decorator/import shadows и implicit callback defaults. Root прочитал delegated implementation, получил точные RED (включая3 container,6 non-Name и5 mount-default cases), затем patched verifier и148 focused tests passed. Shared _binds и source/argument/escape checks исправляют product proof; отдельного old/new agent replay нет, невозможность построения не заявляется. Существующие AGENTS§6/§11 уже охватывают плохой вход и текущий контракт, новая норма не вводится. | product3099ff6; tests/verification/test_planning_descriptor_chain.py; .harness/retro/2026-10-16-r14-planning-descriptor-chain.md
   - 2026-10-16 | connected proof helpers lack startup consumer | identifier_sql_families→sql_expression_inventory→planning_descriptor_chain — реальная source evidence цепочка; последний builder пока потребляется verifier tests/candidate export, а _open_runtime всё ещё создаёт protected components без mandatory global version admission. Три owned public startup RED сохранены без xfail; fragment/corpus require_closed отказывают. Это текущие prerequisite progress и integration risk, не доказательство waste или эффективности новой нормы; helper PASS не закрывает публичный исход. Перед admission claim нужен полный proof consumer до effects; handoff54/58 уже держат работу. | design-docs/R14-VERIFICATION-STATUS.md; tests/composition/test_r14_startup_version_admission.py; новая норма не вводится
   - 2026-10-16 | handoff navigation without authority mutation | В сессии несколько полных handoff reads обрезаны tool output cap; это сессионное свидетельство, не факт встроенного снимка. Additive handoff.sh --summary сохраняет все parsed fields, ID/порядок/статус и timestamp по одной строке, без ack/close или записи файлов. Три CLI случая дали unknown argument/exit2 до правки; затем summary3 + identifier8 дали11 passed. Выигрыш — навигация, не bounded tokens: текущий журнал19411bytes/149lines, summary18566bytes/27lines (subprocess обоих CLI). Нового agent-rule или эффективности инструкции не заявляется. | tooling change .harness/scripts/handoff.py/.sh; tests/tooling/test_handoff_summary.py; .harness/retro/2026-10-16-r14-sql-families-and-observer.md
   - 2026-10-16 | source accounting and binding stronger than syntactic evidence | В период901aaaa..51f963e actual-source mutants нашли неполный src boundary, empty/orphan accounting, shadow/alias/nested-frame/leaf-export и manifest-parameter bindings; generated assembly baseline также не фиксировал независимые origins. Product RED regressions привели к finite refusal и literal expected edges; пять import/def/class/global/pattern mutants дали DID NOT RAISE до shared binding repair. Corpus/constructor/assembly require_closed всегда отказывают, три public startup admission RED остаются открыты. Это повторяющийся продуктовый класс под AGENTS sections6/11, не измеренная эффективность нового agent rule: faithful old/new agent replay не запускался, невозможность построения не заявляется. | продуктовые исправления506a59a/f072a99/2e7083d/51f963e; .harness/retro/2026-10-16-r14-sql-admission-cadence.md; global admission OPEN
   - 2026-10-16 | spawned diagnostic runner imports | Временный R14 continuation runner имел main guard, но импортировал тестовый модуль до него. Spawn повторно загрузил deployment_trust policy-модули в других owners; настоящий `AuthorityBrokerRuntime.attest()` остановил startup с identity mismatch до readiness. Минимальный `/private/tmp/r14_spawn_attest_bad.py` воспроизвёл отказ; перенос всех project imports внутрь main в `/private/tmp/r14_saved_cross_seal_continue.py --attest-smoke` дал 5 непустых owner attestations и exit 0. Проверка одного `runpy(..., run_name='__mp_main__')` не проверяла owner module closure. Действующий продуктовый гейт удержал границу; новая норма не вводится, независимого agent replay нет. Сохранённые head 28 и transfer count 1 перепроверены; неизменность всех startup sidecars не заявляется Дополнение 2026-10-19: новый временный manifest-debug runner повторил top-level project imports → attestation mismatch; после main guard неканонический /var path отдельно отказал в custody. Оба setup failures до target; canonical path дал actual mounted GREEN. Самостоятельного agent replay нет, невозможность не заявлена; .harness/retro/2026-10-19-bound-json-and-unit-cadence.md. | нет agent replay; единичная ошибка diagnostic fixture
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
   - 2026-10-16 | debugger and review target execution lapses | В текущей сессии attach отклонил statement/expectedContent breakpoint, но root успел продолжить процесс до проверки ответа; поздний plain-line breakpoint не дал наблюдения и не объявлен доказательством. Другой bounded query ошибочно прочитал dict как attribute, затем исправлен без изменения переменных. Cold reviewer сначала прочитал frozen candidate вместо main, после уточнения перечитал actual diff. Первоначальный gate-test использовал неканонический journal JSON; setup исправлен до настоящего RED. Это промахи под существующими AGENTS §1/§4/§6, а не установленный дефект debugger/харнесса; самостоятельный agent replay этих эпизодов не выполнялся, невозможность его построить не заявляется. Дополнение 2026-10-19: wait_for_stop отсутствовал в actual private MCP inventory, controller отказал и finally закрыл owned session/server. Несколько угаданных source paths дали no-such-file и были исправлены actual rg inventory. Это повтор под существующими требованиями чтения интерфейса/результата, не новая норма; .harness/retro/2026-10-19-bound-json-and-unit-cadence.md. | сессионные наблюдения; `.harness/retro/2026-10-16-r14-pending-prefix-reopen.md`; новой нормы нет
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

2. ДИФФЫ  95d19fb..HEAD
   25bda6f docs: reconcile active scope and session debts with no-legacy policy
   a070867 test: type startup trust fixtures without changing admission criteria
   89ea279 refactor: remove obsolete R13 H1 recovery guard and type assembly tests
   979a670 refactor: retire H1 delivery bridge and refresh current test fixtures
   34ef6cb refactor: remove obsolete H1 physical delivery grant persistence
   a72c85e test: retire references to removed H1 and SQLite runtime adapters
   385140a refactor: decouple workspace reads from obsolete H1 snapshot issuance
   5d68f53 test: remove retired recovery profiles while retaining shared contracts
   aa1873f refactor: remove obsolete physical recovery profiles and owner registrations
   46fd8b6 test: delete unused legacy runtime support builders
   c60dced refactor: remove orphan SQLite publication and installed H1 components
   51c6bdf test: remove legacy adapters while retaining current contract coverage
   956a482 test: remove obsolete SQLite publication and H1 inventory tests
   59f68ed test: remove obsolete H1 and SQLite dispatch runtime tests
   8bb19d2 test: remove obsolete SQLite R14 accounting and recovery tests
   d119e22 test: remove obsolete H1 wire and history implementation tests
   6bac003 refactor: remove legacy SQLite dispatch and ingress runtimes
   5437c2c refactor: remove legacy R14 execution runtime and recovery publications
   8d53f2e refactor: remove legacy H1 recovery and SQLite accounting sources
   83eb179 refactor: remove legacy CommonCli kernel and H1 implementation
   4071bf8 refactor: remove obsolete scoped broker wire contracts
   ac16950 refactor: remove obsolete H1 scoped readers and delivery authorities
   f9bb32c refactor: remove installed H1 runtime entrypoint and mount implementation
   fd45362 test: remove installed H1 owner and recovery mount consumers
   e68b278 refactor: delete unreachable scoped H1 mount configuration
   e5d9532 refactor: remove versioned installed H1 factory profiles
   bc06d7e refactor: remove legacy R16 dispatch factory entrypoints
   e2c5563 refactor: remove obsolete scheduler policies and compatibility archives
   014cf58 refactor: remove legacy SQLite scheduler runtime
   a22b1d1 test: remove legacy H1 authorization proof suites
   42cd3e5 refactor: delete legacy SQLite sealed accounting writer
   5a70486 refactor: remove legacy chat and SQLite migration surfaces
   объём: 561 files changed, 363 insertions(+), 192630 deletions(-)   (git diff --shortstat 95d19fb..HEAD | sed 's/^ *//')
   незакоммиченного сейчас (в период НЕ входит): 3   (git status --porcelain | wc -l | tr -d ' ')
   коммитов периода: 32   (git rev-list --count 95d19fb..HEAD)
   коммитов с R8 catalog: 0   (git rev-list --count 95d19fb..HEAD -- src/chiplog/inert_shared/r8-implementation-v1.json)

3. ХВОСТЫ  .harness/handoff.md
   файла нет — текущий журнал handoff отсутствует
   DURABLE DEFECTS  .harness/deferred
   записей нет

4. КЕЙСЫ  .harness/reproducers/*/case.md
   кейсов: 0   (find .harness/reproducers -name case.md | wc -l | tr -d ' ')
   регрессия вакуумна — прогонять нечего, отметь это, а не «держит»

5. ИНВАРИАНТЫ И ДОЛГИ
   правил-файлов: 2   (python3 .harness/scripts/checks/rules_have_reproducers.py --status | sed -n 's/^rules_total=//p')
   разделов AGENTS.md: 12   (grep -c '^## [0-9]' AGENTS.md || [ $? -eq 1 ])
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

Официальный снимок содержит 0 agent cases: регрессия вакуумна, прогонять
нечего. Faithful old/new agent replay не выполнялся; эффективность нового
агентного правила не заявляется и новое правило не создаётся.

## Пять линз роя

Рой прочитал официальный снимок, исходный scaffold и актуальные adapters;
root перечитал изменяемую конфигурацию и фактические результаты. Согласие
моделируемых экспертов не является внешней независимой проверкой.

| Линза | Вывод |
|---|---|
| Что сохранить | Удаление по актуальным потребителям, сохранение общих функциональных контрактов, реальные отрицательные/producer→consumer проверки. |
| Что не получилось | Унаследованный technical gate продолжал задавать работу после изменения продуктовой нормы: `a070867` типизировал fixtures, а следующим предлагался ещё один такой срез. |
| Что работает | AGENTS §6/§11/§12 уже требуют наблюдаемый исход и текущий контракт; дополнительная инструкция не нужна. Реальные ошибочные doubles из наблюдений показывают пользу runtime-проверок. |
| Где трение | Strict-аннотации всех тестов стали самостоятельной задачей. Избежать этого пути через область default mypy, сохранив pytest/Ruff и strict production typing. |
| Что исправить | `[tool.mypy].files = ["src"]` в `pyproject.toml`; реальный отрицательный production-вход должен по-прежнему отвергаться. |

## Фрикшены

| Фрикшен | Выбор | Адрес |
|---|---|---|
| Продолжение тестового typing ради самого гейта | Не встретиться: default mypy проверяет production, явный `mypy src tests` остаётся доступен | `pyproject.toml`, `[tool.mypy]` |
| Старые обязательства пережили удаление механизмов | Сгладить: сверка отменённых рецептов и сохранённых требований уже выполнена | `design-docs/R14-VERIFICATION-STATUS.md`, session-debt reconciliation, `25bda6f` |
| Полный вывод pytest скрывал ранние ошибки в хвосте журнала | Сгладить: вывод E27 разобран целиком; не заявлять green по tail | `/private/tmp/chiplog-e27-failures.json`; итоговая полная регрессия ещё обязательна |

## Классы, а не случаи

Класс — техническая проверка сохраняет исходную область после смены продуктовой
границы, а исполнитель принимает её за самостоятельное требование. Конкретный
механизм виден в исходном `cfefc16`: strict mypy с `files = ["src", "tests"]`
появился в scaffold, не как функциональный контракт. Текущая норма §12 говорит,
что технический гейт сам по себе не обосновывает продуктовую работу.

Свежие результаты на `25bda6f`:

- `.venv/bin/mypy`: exit 1, 217 ошибок в 54 файлах, проверено 892 файла;
  `/private/tmp/chiplog-e36-mypy-before.log`. Подсчёт строк `: error:` по префиксу
  пути дал 217 `tests/`, 0 `src/`.
- `.venv/bin/mypy src`: exit 0, 439 файлов;
  `/private/tmp/chiplog-e36-mypy-production-before.log`.
- `git log --all -S 'files = ["src", "tests"]' --oneline -- pyproject.toml`
  и `git show cfefc16:pyproject.toml` устанавливают scaffold-происхождение.

Среди ошибок тестов есть `arg-type`, `union-attr`, `return-value`; они не объявлены
косметическими или исправленными. Предлагаемая правка сознательно убирает
обязательный статический анализ тестов. Она не доказывает безопасность тестовых
дублей; их реальные сценарии/отрицательные случаи продолжают исполняться.
Новых `Any`, ignores, casts, xfail, пропусков и изменений pytest-селекции не нужно.

## Кандидаты в правила

Холодный `$swarm-red-team` прочитал сам черновик и реальные adapters: P0/P1
не подтверждены. Артефакт: `/private/tmp/chiplog-e36-retro-cold-review.md`.
Механизм `gate → lint → uv run mypy` проверен чтением; это не аудит всего
интеграционного diff. После находок перечитан `retro/references/actions.md`:
итоговая классификация — ремонт дорогого пути инструмента, не новое правило
и не удаление вызова проверки из gate.

| Находка | Класс | Адрес/предложение |
|---|---|---|
| Default strict-маршрут всех тестов превратился в дорогую отдельную работу | Ремонт инструмента, путь верный, область дорога | `pyproject.toml`, обязательный strict только для `src`; ремонт `0e1e5af`, pytest/Ruff остаются |
| Повтор указания «гейт требует» после §12 | Наблюдение, faithful agent replay отсутствует | `.harness/observations.md`, запись gate-driven scope after product-policy change; отдельное новое agent-rule не обосновано |
| Хорошо сработало удаление по потребителям | Существующие §11/§12 достаточны | Новую норму за человека не вводить |

## Инварианты модели и долги

| # | Проверка и граница вывода |
|---|---|
| 1 | Два rule-файла прочитаны; `rules_have_reproducers.py` проверяет наличие входа/нормы. Это не доказательство достаточности правил. |
| 2 | Все 12 разделов AGENTS перечитаны; у каждого есть названный носитель либо `[без гейта]`. |
| 3 | `UV_CACHE_DIR=/private/tmp/chiplog-uv-cache sh .harness/scripts/gate.sh --self-test` завершился exit 0: базовые пары, 14 layout, 10 generation и 59 dispatcher/strict-R8/checkpoint/fast тестов. Лог `/private/tmp/chiplog-e36-gate-self-test.log`; это не полный pytest. |
| 4 | Метки/владельцы проверяются `messages_are_actionable.py`; содержательность остаётся суждением. |
| 5 | `git diff 95d19fb..HEAD -- .harness/scripts/gate.sh` пуст; вызовы проверок за период не удалены. |
| 6 | `thresholds.sh` и объявления `HC_*`, `RETRO_EVERY`, `MAX_CONSECUTIVE_SKIPS` перечитаны: новых копий/правок порогов в периоде нет. Общая математическая полнота поиска не заявляется. |

`python3 .harness/scripts/checks/retro_due.py --status`: 32 коммита с последней
ретро, 3 skip при пределе 4. Эта ретро должна закончиться содержательным ремонтом,
а не ещё одним skip. В модели открытых долгов нет; новых не вводим. Optional
продуктовый backlog сохраняется в функциональных scope-документах.

## Уставки и пропускная способность

Durable счётчика частоты hard-отказов за весь период нет; пороги не меняем.
Сверка handoff убрала повторный пересказ отменённых рецептов, сохранив текущие
критерии. Официальный длинный снимок сохранён дословно; сверх него выводы здесь
ограничены конкретным ремонтом, без новой диагностической инфраструктуры.

## Наблюдаемый ремонт

Root изменил только `files = ["src", "tests"]` на `files = ["src"]`;
`strict = true`, Ruff и pytest не изменены. Коммит ремонта: `0e1e5af`.

Реальные команды из `/private/tmp/chiplog-e38-quality-probes.py` выполнены из
корня candidate; receipts `/private/tmp/chiplog-e38-quality-probes.json`:

| Вход | Команда | Исход |
|---|---|---|
| Чистый production | `uv run mypy` | exit 0, 439 файлов |
| Покрываемый `src/chiplog/_product_quality_scope_probe.py`, неверный return type | `uv run mypy` | exit 1, ровно `[return-value]` в probe, 440 файлов |
| Неимпортируемый из production `tests/_product_quality_scope_probe.py` с той же ошибкой | `uv run mypy` | exit 0, 439 файлов |
| Тот же test-only probe явно | `uv run mypy tests/_product_quality_scope_probe.py` | exit 1, `[return-value]` |
| После удаления обоих временных файлов | `uv run mypy` | exit 0, 439 файлов |

`rg --files src -g '*.py' | wc -l` также дал 439; это весь текущий Python src,
не выбранный вручную список. Test-only probe не имел импортов из production;
оба временных файла удалены. После этого
`UV_CACHE_DIR=/private/tmp/chiplog-uv-cache sh .harness/scripts/lint.sh` дал
exit 0: Ruff check, 1043 файла уже отформатированы, strict production mypy
прошёл. Лог: `/private/tmp/chiplog-e38-lint.log`.

Checkpoint ремонта выполнил неизменённый
`tests/tooling/test_harness_preflight.py`: 34 passed in 9.50s, включая
положительные и отрицательные исходы adapters. Это не полная функциональная
регрессия. Отдельный Markdown-коммит завершает эту ретро после собственного
коммита ремонта; разрешение пользователя на промежуточные коммиты уже дано.

## Итог

`outcome: change`, адрес `pyproject.toml`: ремонт выполнен отдельным коммитом,
отрицательные пробы подтвердили сохранение production typecheck. Тестовые
статические ошибки не исправлены и их безвредность не установлена. Это не финальная интеграция:
source/catalog reconciliation, полный gate/pytest с подтверждённым stage0 и
холодный блокирующий аудит всего diff остаются за root.
