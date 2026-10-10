---
date: 2026-10-10
# trigger: cadence | incident
trigger: cadence
# outcome: change | skip
outcome: change
# id правила, если outcome: change
rule: .harness/scripts/new-retro.sh
# почему нет, если outcome: skip
reason:
---

## Что перечитано

Входной снимок получен одним запуском `new-retro.sh`, который вызвал
`retro-inputs.sh`; ниже сохранён его точный stdout. На момент этого запуска генератор ещё
оставил ручную вставку в заготовку. Числа сохранены вместе с командами, которыми получены
(`AGENTS.md` §6).

```text
входы ретро — с последней ретро (3f579a8)

1. КОПИЛКА  .harness/observations.md
   записей: 22   (grep -c '^- 2[0-9][0-9][0-9]-' .harness/observations.md)
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
   - 2026-10-10 | manual retro-input insertion | `new-retro.sh` напечатал пять входов, после чего пришлось вручную переносить их в созданную запись `.harness/retro/2026-10-10.md`. В этой сессии перенос проверен сравнением с точным выводом `retro-inputs.sh`; это один зафиксированный случай без входа для воспроизведения пропуска/усечения. Пока оставить наблюдением, а при повторе проверить генератор записи как адрес сглаживания | нет
   вопрос: что здесь ломается ПОСТОЯННО, а не что сломалось

2. ДИФФЫ  3f579a8..HEAD
   2902752 feat(r14): publish accepted recovery source cut
   7196999 fix(r16): preflight outcome authority before refresh
   75f118b feat(r14): collect accepted recovery source subset
   d8c8313 feat(r14): retain selected dispatch source chain
   462700e feat(r14): validate recovered continuation readiness
   объём: 12 files changed, 861 insertions(+), 68 deletions(-)   (git diff --shortstat 3f579a8..HEAD | sed 's/^ *//' | grep . || echo 'нет изменений')
   незакоммиченного сейчас (в период НЕ входит): 8   (git status --porcelain | wc -l | tr -d ' ')
   коммитов периода: 5   (git rev-list --count 3f579a8..HEAD)
   коммитов с R8 catalog: 0   (git rev-list --count 3f579a8..HEAD -- src/chiplog/inert_shared/r8-implementation-v1.json)

3. ХВОСТЫ  .harness/handoff.md
   записей: 11   (python3 .harness/scripts/checks/handoff_pending.py --status | sed -n 's/^handoff_total=//p')
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

Вставь этот вывод в раздел «Что перечитано» записи ретро.

```

Дополнительно прочитаны сами изменения и журнал за период:

- `git diff --stat 3f579a8..HEAD` — 12 файлов, 861 вставка, 68 удалений.
- `git log --format='%h %s' 3f579a8..HEAD` — пять последовательных тематических коммитов R14/R16; R8-каталог не менялся.
- `git diff --check 3f579a8..HEAD` — ошибок пробелов нет.
- `git diff 3f579a8..HEAD -- .harness/scripts/gate.sh .harness/scripts/thresholds.sh AGENTS.md` — процессные гейты и правила периода не менялись.
- Прочитаны `.harness/handoff.md` записи 15–18 и последняя ретро `.harness/retro/2026-10-10-r14-next-turn-owner.md`.

Открытый пользовательский срез: selected accepted recovery source subset повторно открывается без SEND; successor после reopen ещё не публикуется. Это R14.3 продуктовый хвост, а не дефект проверки.
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
| | | | |

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
| Повторный ручной перенос одинакового 14K stdout в новую запись ретро; это уже было отмечено в предыдущей cadence-записи, и повторилось сейчас | Сгладить: сохранять точный stdout один раз и встраивать автоматически. При ошибке сбора запись не публикуется; существующая запись не перезаписывается. Реализовано и проверено самотестом | `.harness/scripts/new-retro.sh` |
| Первая попытка checkpoint-коммита с pathspec привела к временному индексу, который не соответствует снимку, ожидаемому commit hook; пришлось восстановить рабочий набор и выполнить тематический коммит последовательной подготовкой | Избегать: staged только точный срез, затем `git commit` без pathspec. Один случай, дополнительное правило не обосновано | `.harness/observations.md` |
| Mounted R14-проверка заняла 470.80 с вручную и 490.55 с в hook; R16-срез — около 102 с | Сгладить после проходного пользовательского сценария: локализовать повторное чтение журнала и сузить долгую проверку, сохранив mounted producer→consumer seam. Не считать git-интервалы оценкой трудозатрат | Handoff #17–18; продуктовая работа, не харнесс |

## Классы, а не случаи

Повторяемый класс этого периода — лишний ручной перенос штатно сгенерированного снимка
входов ретро в его же заготовку. Предыдущая ретро уже записала этот перенос как
наблюдение с условием проверить генератор при повторе; текущий `new-retro.sh` снова
создал placeholder и отдельно напечатал те же входы. Причина локализована в tool seam:
генератор владеет и файлом, и выводом, но не соединяет их.

Другие заметные трудности не образуют подтверждённого класса харнесса: дорогая проверка
R14 — продуктовый путь; pathspec-проблема была единичной и обходится стандартным staging.
По R14 тесту доказан selected source subset после reopen без повторного SEND, но не
successor publication. Для регрессии за период кейсов нет: `0` — вакуум, прогонять нечего.

## Кандидаты в правила

| Находка | Класс | Что появится в диффе | Куда смотреть |
|---|---|---|---|
| Повторный ручной перенос пяти входов в новую cadence-запись | Повторяющийся инструментальный фрикшен; не ошибка поведения агента | `new-retro.sh` сохраняет stdout `retro-inputs.sh` один раз и встраивает его в созданную запись атомарно; ошибки и конфликт пути остаются видимыми, существующая запись не меняется. Самотест прошёл. | `.harness/scripts/new-retro.sh` |
| Pathspec commit hook с временным индексом | Единичная шероховатость git commit | Новая норма не обоснована; случай записан в копилку. Использовать точное staged-содержимое и коммит без pathspec | `.harness/observations.md` |
| Медленный mounted late-evidence путь | Локализуемая продуктовая стоимость, ещё не измерен один участок | Оставить в R14.3 хвосте до прохождения рабочего сценария; без нагрузочных и широких тестов | `.harness/handoff.md` #17–18 |
| Нет полного successor после reopen | Незавершённое требование R14.3, не найденный сбой harness | Продолжить R14: смонтированная публикация successor из authenticated recovery cut без SEND и rival HOLD на stale evidence | `.harness/handoff.md` #18 |

Новых правил поведения агента нет. Путь «С чего начать» в `.harness/HARNESS_MODEL.md`
проверен: это повторяемая инструментальная friction, поэтому меняется скрипт, а не
AGENTS.md/правило без исполняемого плохого входа.

## Инварианты модели и долги

| # | Инвариант | Как проверить | Держится? |
|---|---|---|---|
| 1 | у правила-файла есть вход либо непросроченная норма | `python3 .harness/scripts/checks/rules_have_reproducers.py --status` → 2 правила | Проверка сообщает 2 правила; поведение правил вне её охвата здесь не пересматривалось |
| 2 | у каждого раздела `AGENTS.md` есть гейт или `[без гейта]` | `grep -c '^## [0-9]' AGENTS.md` → 10; прочитаны все разделы | Да: у каждого раздела указан носитель либо `[без гейта]` |
| 3 | у каждой проверки `gate.sh` есть плохая и хорошая ветви `--self-test` | `grep -c '^run ' .harness/scripts/gate.sh` → 12; self-test не запускался в этом периоде | В этом периоде `gate.sh` не менялся; парные ветви заново не проверялись |
| 4 | каждое сообщение о провале несёт метку и владельца | `messages_are_actionable.py` подключён к гейту; этот гейт в периоде не менялся | Внутри охвата — без новой проверки; сообщения вне охвата не пересматривались |
| 5 | из `gate.sh` не убыло строк `run` без записи ретро | `git diff 3f579a8..HEAD -- .harness/scripts/gate.sh` пуст | Да для этого периода: `gate.sh` не менялся |
| 6 | ни один порог не в двух местах исполняемого кода | `.harness/scripts/thresholds.sh`, `checks/*.py`, `new-case.sh`; значения `HC_*` через общий файл | По чтению: пороги централизованы; полноту отдельным тестом не проверял |

**Долги.** В `.harness/HARNESS_MODEL.md` открытых долгов нет — вывод `retro-inputs.sh`.
Четыре оставленные продуктовые хвоста находятся в handoff; это не долги модели.
Крючок для таблицы долгов не сработал.

| Долгов было | Стало | Закрыто | Заведено |
|---|---|---|---|
| 0 | 0 | 0 | 0 |

## Уставки хайлайтов

Периодические входы не содержат durable-счётчика срабатываний `guard-edit.sh` или
частоты хайлайтов. По ним нельзя заявить, что порог сработал либо молчал; значение
порогов не менялось (`git diff 3f579a8..HEAD -- .harness/scripts/thresholds.sh` пуст).
Пересмотр уставок по этому периоду не обоснован.

## Пропускная способность

За период нет сообщения, что файл харнесса перестал читаться за один присест. Новая
запись ретро содержит 217 строк вместе со снимком пяти входов; это само по себе не
доказывает нечитаемость. Сжимать исходные свидетельства без такого сигнала не нужно.

## Итог

`outcome: change`; `rule: .harness/scripts/new-retro.sh`. Повторяемая ручная операция
уже была отмечена в предыдущей ретро и повторилась в текущем кадансе. Генератор захватывает
stdout ровно один раз, проверяет пять заголовков и completion-marker, встраивает байты в
запись и публикует её без перезаписи существующей цели. Самотест
`sh .harness/scripts/self-tests/new-retro-generation.sh` прошёл: точное multiline-встраивание,
ошибка с partial stdout/stderr, неполный снимок, существующая цель и гонка публикации.
Решения и выводы ретро по-прежнему остаются за человеком.
