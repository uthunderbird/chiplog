---
date: 2026-10-12
# trigger: cadence | incident
trigger: cadence
# outcome: change | skip
outcome: skip
# id правила, если outcome: change
rule:
# почему нет, если outcome: skip
reason: "За период нет воспроизводимого agent-входа, показывающего пробел инструкции: снимок сообщает 0 case.md и 2 правила. Все пять коммитов меняют продуктовые R14-контракты/тесты. Новое правило или изменение гейта сейчас было бы безвходовым; вернуться к гипотезе при появлении replay."
---

## Что перечитано

При заведении записи `new-retro.sh` встраивает сюда точный stdout штатного сборщика.
Числа остаются рядом с командами, которыми получены; переписывать их по памяти нельзя
(`AGENTS.md` §6).

```text
входы ретро — с последней ретро (5243267)

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

2. ДИФФЫ  5243267..HEAD
   6cafc14 fix(r14): bind successor to selected CLI input
   89d3ae9 fix(r14): bind successor identity to predecessor
   dafb0c0 fix(r14): validate successor inheritance inputs
   41849f5 fix(r14): classify same-run resume frontier
   b04537c fix(r14): bind frozen refs to frontier families
   объём: 13 files changed, 735 insertions(+), 27 deletions(-)   (git diff --shortstat 5243267..HEAD | sed 's/^ *//' | grep . || echo 'нет изменений')
   незакоммиченного сейчас (в период НЕ входит): 15   (git status --porcelain | wc -l | tr -d ' ')
   коммитов периода: 5   (git rev-list --count 5243267..HEAD)
   коммитов с R8 catalog: 0   (git rev-list --count 5243267..HEAD -- src/chiplog/inert_shared/r8-implementation-v1.json)

3. ХВОСТЫ  .harness/handoff.md
   записей: 17   (python3 .harness/scripts/checks/handoff_pending.py --status | sed -n 's/^handoff_total=//p')
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
   · Reduce the original-resolution mounted witness to bounded, focused fixtures that still prove closure, selected outcome, reduction and reopen; rerun end-to-end only after the fixture no longer expands journals beyond the execution budget
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
| Нет: `find .harness/reproducers -name case.md` нашёл 0 кейсов | — | — | Регрессия вакуумна — прогонять нечего. |

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
| Successor contracts последовательно связывали identity, inheritance и новые CLI-поля несколькими локальными проверками. | Сгладить: объединить producer→consumer проверки одним смонтированным successor/reopen тестом с односторонней подменой admitted ingress; локальные проверки оставить для точных отказов. | Текущий R14 successor/reopen handoff; продуктовый путь, не harness. |

## Классы, а не случаи

Назови повторяющуюся серию и проверь, образует ли она один класс. Например, три
«дочиненных» теста в разных файлах могут быть одним классом — но только если назван
общий механизм.

Для межкомпонентной гарантии проверь, проходил ли тест через байты реального
producer до consumer, и отвергал ли consumer одностороннюю подмену выбранного
поля или идентичности. Если нет, запиши конкретный пропущенный стык и реши,
нужен ли совместный плохой вход; отдельные успешные тесты сторон его не заменяют.

Класс периода — полнота межконтрактных joins у R14 successor: пять коммитов закрепили
принадлежность frozen refs к frontier families (`b04537c`), повторную классификацию
same-Run resume внутри транзакции (`41849f5`), наследование identity и obligations
(`dafb0c0`, `89d3ae9`), и связь CLI-предложения с admitted input (`6cafc14`). Холодные
проверки нашли prompt-output mismatch и contour mismatch и исправили их. Это подтверждённое
качество продуктового контракта, но снимок не содержит agent replay, связывающего сбой с
пробелом инструкции; правило не выводится.

## Кандидаты в правила

Ворота: есть ли прогоняемый вход. Есть и падает — правило рождается, сколько бы раз
провал ни случался. Нет — наблюдение ждёт родню в копилке; признак созревания не число
повторов, а формулировка: класс назван так, что под него подходят все накопленные записи.

У сбоя либо есть одна устранимая причина, либо так шумит сама система. Шуму системы
не отвечают правилом — ему отвечают системой.

Форму нового правила сверить с `.harness/HARNESS_MODEL.md`, маршрут «С чего начать»: он решает,
это раздел `AGENTS.md` или правило-файл, и какие поля обязательны.

Новых harness-кандидатов нет: все пять коммитов затрагивают продуктовые R14
контракты/тесты, а воспроизводителей agent-ошибки в снимке ноль. Наблюдение о
nested delegation ownership остаётся в копилке до нового воспроизводимого входа.

## Инварианты модели и долги

`.harness/HARNESS_MODEL.md` задаёт владельца вердикта для каждого инварианта. Ретро сводит
результаты всех шести: 1, 3 и 4 устанавливают проверки, а 2, 5 и 6 — сама ретро.

| # | Инвариант | Как проверить | Держится? |
|---|---|---|---|
| 1 | у правила-файла есть вход либо непросроченная норма | `python3 .harness/scripts/checks/rules_have_reproducers.py --status` | Да в проверяемой части: 2 правила, 1 норма; exit 0. Не доказывает продуктовые тесты. |
| 2 | у каждого раздела `AGENTS.md` есть гейт или пометка `[без гейта]` | `grep -nE '^## [0-9]|\[без гейта\]|Гейт:|Хайлайт:' AGENTS.md` | Да: 10 разделов; глазами найден носитель в каждом. |
| 3 | у каждого файла проверки есть парный блок в `--self-test` | `python3 .harness/scripts/checks/checks_are_wired.py`; `UV_CACHE_DIR=/private/tmp/chiplog-retro-uv-cache sh .harness/scripts/gate.sh --self-test`; `grep -c '^run ' .harness/scripts/gate.sh`; `grep -c '^run .*/\(lint\|test\)\.sh' .harness/scripts/gate.sh` | Да в покрытии self-test: парные проверки и test-модули прошли (14 + 58 passed); структурный checker exit 0, 12 run, 2 адаптера. |
| 4 | каждое сообщение о провале несёт метку и владельца | `python3 .harness/scripts/checks/messages_are_actionable.py` | Да в охвате гейта, exit 0; вне его остаётся ручная проверка. |
| 5 | из `gate.sh` не убыло строк `run` без записи ретро | `git -c core.fsmonitor=false diff --name-only 5243267..HEAD -- .harness/scripts/gate.sh` | Да для периода: `gate.sh` не менялся. |
| 6 | ни один порог не в двух местах исполняемого кода | `grep -R -nE 'HC_(FILES_SOFT|FILES_HARD|DIRTY_HOURS|HANDOFF_ENTRIES|AGENTS_WORDS|COOLDOWN_TURNS|CASE_MAX_FILES|CASE_MAX_BYTES)=' .harness/scripts/checks .harness/scripts/new-case.sh .harness/scripts/thresholds.sh` | Да для проверенных highlight-порогов: определения только в `thresholds.sh`; cadence/skip/revision — отдельные предикаты. |

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

Данных о частоте срабатывания highlight/guard нет: durable telemetry не найдено,
поэтому текущую настройку нельзя оценить по фактической частоте. В периоде файл
порогов не менялся; вывод — неизвестно, пересмотр сейчас не обоснован.

## Пропускная способность

Есть ли файл харнесса, который перестал читаться за один присест? Он идёт под нож:
сжать, слить, вынести в скрипт.

В этом периоде сигналов о нечитаемом harness-файле не было; сокращение не предлагается.

## Итог

Одна из двух веток, обе легальны, обе оставляют след:
- `outcome: change` — урок доведён до правки харнесса, указан `rule`;
- `outcome: skip` — правки нет, указан `reason`.

Ретро заканчивается коммитом.

Рой рекомендует `skip`: открытый R14 стык и долгий mounted-путь относятся к
продуктовой работе. Нет воспроизводимого agent-входа, который оправдал бы правку
правила или гейта. Регрессия по reproducers вакуумна (0 кейсов), это не считается
подтверждением того, что правила держат.
