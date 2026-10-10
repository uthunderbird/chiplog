---
date: 2026-10-10
# trigger: cadence | incident
trigger: cadence
# outcome: change | skip
outcome: skip
# id правила, если outcome: change
rule:
# почему нет, если outcome: skip
reason: В периоде нет нового воспроизводимого сбоя правила или гейта. Отказ monkeypatch вызван подменой защищённого метода; mounted test не выдал наблюдаемого pytest-исхода. Отдельный R17-коммит во время незакрытого R14 записан как разовое наблюдение без agent replay; оснований вводить норму нет.
---

## Что перечитано

При заведении записи `new-retro.sh` встраивает сюда точный stdout штатного сборщика.
Числа остаются рядом с командами, которыми получены; переписывать их по памяти нельзя
(`AGENTS.md` §6).

```text
входы ретро — с последней ретро (c36626c)

1. КОПИЛКА  .harness/observations.md
   записей: 23   (grep -c '^- 2[0-9][0-9][0-9]-' .harness/observations.md)
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
   вопрос: что здесь ломается ПОСТОЯННО, а не что сломалось

2. ДИФФЫ  c36626c..HEAD
   3cf8a1c feat(r14): accept direct terminal for next turn
   8d89cb0 perf(r17): reuse historical admitted inbox read
   9dfb48e fix(r14): prepare next turn from direct terminal
   218a132 docs(r14): correct semantic reduction status
   a3707b4 fix(r14): bind next-turn actor to cut trust
   объём: 12 files changed, 945 insertions(+), 83 deletions(-)   (git diff --shortstat c36626c..HEAD | sed 's/^ *//' | grep . || echo 'нет изменений')
   незакоммиченного сейчас (в период НЕ входит): 15   (git status --porcelain | wc -l | tr -d ' ')
   коммитов периода: 5   (git rev-list --count c36626c..HEAD)
   коммитов с R8 catalog: 0   (git rev-list --count c36626c..HEAD -- src/chiplog/inert_shared/r8-implementation-v1.json)

3. ХВОСТЫ  .harness/handoff.md
   записей: 13   (python3 .harness/scripts/checks/handoff_pending.py --status | sed -n 's/^handoff_total=//p')
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

В снимке периода кейсов 0 (`find .harness/reproducers -name case.md`); регрессия вакуумна — прогонять нечего. Ни один новый agent replay в этом периоде не запускался.

## Фрикшены

Места, где работа шла тяжелее, чем должна: переспрашивание, переделка, долгий поиск,
спор о решённом. По каждому — развилка: **не встретиться** (был другой путь) или
**сгладить** (путь верный, проходится дорого).

| Фрикшен | Не встретиться или сгладить | Адрес правки |
|---|---|---|
| R14 direct-terminal mounted test: запуск под 180-секундным ограничением остановлен без pytest summary; pytest сообщил `no tests ran` через 201.73 секунд. Связанные поздние пути отражены в handoff #19 и #20. | сгладить: не повторять полный тяжёлый путь на checkpoint; ограниченной локализацией определить медленный этап или уменьшить реалистичную fixture, сохранив selected closed outcome → reopen → один next Turn | handoff #20; `.harness/HARNESS_MODEL.md` / AGENTS.md §7 |
| Коммит `8d89cb0 perf(r17)` попал в период незакрытого R14 после пользовательской инструкции работать по одной дорожной карте | не встретиться: удерживать следующий roadmap до закрытия R14; единичный случай занесён в observations, без нового правила | `.harness/observations.md` |

## Классы, а не случаи

**1. Что сработало и стоит сохранить.** Пять коммитов периода затронули 12 файлов (`c36626c..3cf8a1c`: 945 insertions, 83 deletions); каждый отдельный коммит — не более трёх файлов и укладывается в пользовательский лимит 40. В R14 коммиты добавляют узкие проверки селектора, trust continuity и direct-terminal preparation; R17 commit отдельно проверяет повторное чтение admitted inbox заглушкой, которая падает при втором чтении. Это полезные локальные тесты. Они не доказывают полный смонтированный direct-terminal путь.

**2. Что не получилось с первого раза.** Первая временная диагностика заменила защищённый dispatch-метод и получила `WorkspaceRejected("unregistered workspace class dispatch")` за 8.27 секунды. Это был отказ самой техники probe; он показывает, что регистрационная граница сработала, а не объясняет задержку цели. Следующий позитивный mounted тест за 201.73 секунды не сообщил ни pass, ни assertion failure (`no tests ran`). Его нельзя объявлять успешным или дефектом продукта. Отдельно, R17 commit оказался внутри незакрытого R14 focus. Причина исключения не записана; это одна наблюдаемая sequencing ошибка, но независимого agent replay пока нет.

**3. Что из ранее внедрённого работает.** `new-retro.sh` встраивает один точный stdout сборщика; проверка реального формата `base..HEAD` в `.harness/scripts/self-tests/new-retro-generation.sh` прошла. `sh .harness/scripts/gate.sh --self-test` с временным `UV_CACHE_DIR` завершился кодом 0: 58 passed, включая 14 тестов структуры снимков. В периоде не менялись `.harness` и `AGENTS.md`. Сейчас 13 handoff-записей, все `next`, `new=0`, `now=0`; они не держат коммит. В `.harness/deferred` нет durable defect, в таблице долгов модели — 0. Handoff threshold 5 пересечён, но это число carried-записей, не число блокеров.

**4. Фрикшены: путь или стоимость.** Несколько записей handoff фиксируют длинные R14/R16 mounted пути; последний прямой тест не дал результата за 180 секунд. Здесь стоит сгладить проверочный путь уменьшенной реалистичной fixture или ограниченной локализацией, сохраняя быстрые mounted компоненты, negative cases и восстановление. Полный путь оставить до рабочего прототипа/этапа, где он даст решающий критерий. Отдельный R17 commit во время фокуса R14 — путь, которого следовало избежать по пользовательской последовательности; сохранить существующую commit и исправить ход работы дальше, не добавляя неподтверждённый системный запрет.

**5. Предложения по харнессу.** Не добавлять правило и не менять gate. AGENTS.md §7 уже откладывает нагрузочные/стрессовые/широкие проверки и требует быстрые реальные mounted checks на промежуточном срезе; handoff #20 уже задаёт владельцу меньшую fixture или ограниченную локализацию. Ни один новый тест не демонстрирует, что это правило не держит. Первую ошибку диагностической подмены не превращать в норму: контроль защищённого метода сработал, а метод `sys.setprofile` ещё не запускался.

## Кандидаты в правила

Класс выбирается только при падающем воспроизводимом входе. Нового входа, который показывает пробел правила или gate, нет. Единственное отклонение от последовательного focus — коммит `8d89cb0 perf(r17)` посреди открытого R14; точные пользовательская инструкция и commit зафиксированы как наблюдение ниже, до независимого agent replay правило не рождается.

| Находка | Класс | Что появится в диффе | Куда смотреть |
|---|---|---|---|
| R17 commit во время явного R14-first focus | единичное наблюдение; replay нет | строка наблюдения; новых правил и гейтов нет | `.harness/observations.md` |

## Инварианты модели и долги

| # | Инвариант | Как проверить | Держится? |
|---|---|---|---|
| 1 | у правила-файла есть вход либо непросроченная норма | `rules_have_reproducers.py --status`; пара self-test | да, `rules_total=2`; самотест показал `ok` |
| 2 | у каждого раздела `AGENTS.md` есть гейт или `[без гейта]` | 10 заголовков + проверка каждого раздела на адрес | да, 10/10 |
| 3 | у каждого файла проверки есть парный блок в `--self-test` | `gate.sh --self-test` | да в охвате: 58 passed, включая парные снимки |
| 4 | каждое сообщение о провале несёт метку и владельца | `messages_are_actionable.py`; self-test pair | да, checker прошёл, самотест показал `ok` |
| 5 | из `gate.sh` не убыло строк `run` без записи ретро | `git diff c36626c..HEAD -- .harness/scripts/gate.sh` | да, diff пуст |
| 6 | ни один порог не в двух местах исполняемого кода | поиск присваиваний `HC_*` вне `thresholds.sh` в `.harness/scripts` | да, 0 duplicate assignments в 6 проверенных переменных |

| Долгов было | Стало | Закрыто | Заведено |
|---|---|---|---|
| 0 | 0 | 0 | 0 |

## Уставки хайлайтов

- `.harness/scripts/thresholds.sh`: `HC_HANDOFF_ENTRIES=5`, `HC_COOLDOWN_TURNS=100`; текущий счётчик 13, поэтому handoff highlight выше порога.
- Игнорируемый state healthcheck хранит последний handoff signal на turn 22, текущий turn — 63. Истории сигналов нет; частоту за период установить нельзя.
- Для guard-edit snapshot с `git status --porcelain` не равен входу `--untracked-files=all`; срабатывание hard threshold не подтверждено. Ни один порог не меняю.

## Пропускная способность

В периоде нет свидетельства, что файл харнесса перестал читаться за один присест.

## Итог

`outcome: skip`: нет нового падающего воспроизводителя, показывающего пробел правила или проверки. Зафиксированное нарушение R14-first focus остаётся наблюдением до независимого replay; отклонение не повторять. Длинный R14 mounted путь остаётся в handoff #20 и продолжится через меньшую fixture или bounded localization. Ретро заканчивается отдельным коммитом.
