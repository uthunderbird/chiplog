---
date: 2026-09-20
trigger: cadence
outcome: change
rule: .harness/scripts/retro-inputs.sh
reason: Устранить воспроизведённый пропуск отдельного deferred ledger при сборе входов ретро.
---

## Что перечитано

Ниже исходный вывод new-retro.sh / retro-inputs.sh до исправления, без пересчёта
исторических чисел. Его фраза об отсутствии отложенных работ оказалась слишком
сильной; раздел «Результат» отделяет фактическое состояние от дефекта сборщика.

```text
входы ретро — с последней ретро (c324672)

1. КОПИЛКА  .harness/observations.md
   записей: 9   (grep -c '^- 2[0-9][0-9][0-9]-' .harness/observations.md)
   - 2026-09-12 | R8 verification snapshot | Во время широкого прогона менялся source, привязанный к boundary evidence; лог /tmp/chiplog-r8-final-pytest.log содержит renewed boundary evidence и 10 failed, 483 passed. Не все отказы доказанно одной причины; исходная агентская тройка не сохранена. Gather-context уточнён, отдельное agent rule не создаётся | нет
   - 2026-09-12 | R8 scope baseline | Финализация подошла к пределу файлов; durable trace отказа guard отсутствует. Раньше учитывать inherited dirty files и артефакты завершения можно в gather-context; оснований повышать порог или объявлять измеренные десять минут потерь нет | нет
   - 2026-08-20 | внешнее состояние | принудительное завершение tool-session убило tunnel launcher без shell trap и оставило временное DigitalOcean SSH rule; первый ownership marker жил в нестабильном process-specific `$TMPDIR`, поэтому cleanup не находил его; marker перенесён в `~/.chiplog/tunnel.lock` и добавлена явная команда `tunnel cleanup`, но автоматического TTL у правила всё ещё нет | да
   - 2026-08-21 | внешняя идентичность публикации | Старый GitHub redirect потребовал точной внешней проверки: `gh repo view uthunderbird/chiplog` разрешил его в `uthunderbird/chiplog-legacy`, поэтому удобный read-only путь не отличил redirect от существующего exact repository; точный API и последующая create-проверка потребовались отдельно | нет
   - 2026-08-21 | перенос итогового артефакта | Копирование финальных документов изменило относительные имена ссылок: публичные копии сохранили ссылки на прежние `document.md` и `planning-journal-split.md`; post-copy поиск старых путей обнаружил drift до коммита | нет
   - 2026-08-21 | human-owned commit gate | Явное разрешение на коммит потребовало ожидания пользователя и автоматических continuation-turns; автоматический `handoff --ack` недопустим, потому что отменил бы authority boundary, а отдельного механизма ожидания без повторных turns в локальном харнессе нет | нет
   - 2026-08-27 | hook ownership/scope | Repo-wide lint при коммите R0 корректно остановился на заранее существовавшем untracked `.tmp_plan17_materialize.py`; чтобы отличить ожидаемый verdict от дефекта lifecycle/cleanup, будущий integration-input должен раздельно варьировать «файл существовал до запуска» и «файл создан проверкой», проверять residue, повторный запуск и сохранность bytes | нет
   - 2026-08-27 | worktree rematerialization | Старый R1 worktree пришлось заменить чистым после Git-local environment contamination; код и интеграционный тест подтверждают достижимость класса, но исходная тройка «запрос — состояние мира — харнесс» и durable trace конкретной порчи не сохранены, поэтому новое правило из этого рассказа не рождается | нет
   - 2026-09-04 | highlight observability | Ретро не может отличить молчащий жёсткий highlight от действительно редкого: durable-журнал срабатываний отсутствует, поэтому уставка порога остаётся непроверяемой; вход для правила пока не построен | нет
   вопрос: что здесь ломается ПОСТОЯННО, а не что сломалось

2. ДИФФЫ  c324672..HEAD
   6f34daf test: cache boundary fixture and avoid duplicate stage0 cases
   7d95361 feat(r13): implement verified hermetic production loop
   f8f0c39 feat(r12): compose and verify Stage1 workspace reads
   786e209 chore(repo): finalize deferred tools and clean up local setup
   db21a4e feat(r9-r11): add workspace, evidence journal and calendar reads
   f0a8d42 rule(tests-layout): organize tests by entrypoint and preserve current evidence
   объём: 205 files changed, 20516 insertions(+), 1525 deletions(-)   (git diff --shortstat c324672..HEAD | sed 's/^ *//' | grep . || echo 'нет изменений')
   незакоммиченного сейчас (в период НЕ входит): 35   (git status --porcelain | wc -l | tr -d ' ')

3. ХВОСТЫ  .harness/handoff.md
   файла нет — за человека ничего не решено и ничего не отложено

4. КЕЙСЫ  .harness/reproducers/*/case.md
   кейсов: 0   (find .harness/reproducers -name case.md 2>/dev/null | wc -l | tr -d ' ')
   регрессия вакуумна — прогонять нечего, отметь это, а не «держит»

5. ИНВАРИАНТЫ И ДОЛГИ
   правил-файлов: 2   (python3 .harness/scripts/checks/rules_have_reproducers.py --status | sed -n 's/^rules_total=//p')
   разделов AGENTS.md: 10   (grep -c '^## [0-9]' AGENTS.md)
     это левая половина инварианта 2: у каждого раздела должен быть
     гейт или пометка [без гейта] — правую половину читай глазами
   строк run в gate.sh: 11   (grep -c '^run ' .harness/scripts/gate.sh)
   из них адаптеров: 2   (grep -c '^run .*/\(lint\|test\)\.sh' .harness/scripts/gate.sh)
   самотест здесь не запускается; он проверяет harness-пары и ограниченные
   тесты fast/контрактов адаптеров. Досрочно: sh .harness/scripts/gate.sh --self-test
   долги:
   · нет открытых   (grep '^| D' .harness/HARNESS_MODEL.md)

Вставь этот вывод в раздел «Что перечитано» записи ретро.
```

Сверх вывода прочитаны диффы `git diff c324672..HEAD -- .harness/scripts/gate.sh
.harness/scripts/test.sh .harness/scripts/thresholds.sh`, коммит `6f34daf`, текущие
`tests/support/stage0_delegation.py`, `.harness/deferred/README.md`, `deferred.py`,
AGENTS.md, HARNESS_MODEL.md и пороги. Staged transcript changes не входят в этот
committed период: их холодная проверка и evaluation tests — отдельные свидетельства.

Handoff отсутствует после явного разрешения пользователя «Коммить, пушь»:
шесть ранее зафиксированных решений пересказаны и закрыты `handoff.sh --ack`.
Это не означает, что решений в сессии не было. Они касались единого Markdown,
сохранения exact formatter response, canonical message IDs, NOT_RUNNABLE для
неподключённых bindings и границы R18 для T01. Незакрытых handoff-tail не переносили.

## Рой по пяти линзам

1. **Что работает.** Cold review, typed source-bound evidence и разделение compile
   от runtime удерживают границу заявления. Это уже покрыто существующими AGENTS
   §1/§6/§7 и тестами; новую норму из успеха не создаём.
2. **Что не получилось.** Реестр deferred-defect добавлен в периоде, но входы
   ретро по-прежнему читали только handoff. Отсутствие одного источника ошибочно
   трактовалось как отсутствие всей категории работ. Воспроизведение ниже даёт
   конкретный предикат; это дефект мета-инструмента, не реконструированная ошибка агента.
3. **Ранее внедрённое.** В gate не убыло run; добавлен tests-layout с парными
   проверками. Stage0 delegation требует свежих точных дочерних отчётов; экономия
   повторного pytest не сама по себе evidence потери проверок. Список долгов модели
   пуст; старые наблюдения об external cleanup и highlight telemetry этим не закрыты.
4. **Фрикшен.** Двойной учёт tail-источников лучше сгладить в сборщике, чем каждый
   раз помнить второй каталог вручную. Обязательная cadence-проверка не отменяется
   из-за накопленного диффа. Текущий ошибочный запуск multiprocessing из stdin
   устранён штатным `python -m chiplog.verification.transcript_suite`; дополнительная
   норма для единичного выбора не требуется.
5. **Адрес исправления.** `.harness/scripts/retro-inputs.sh`: независимо вывести
   handoff и `.harness/deferred/D-*.md`, ID/status/path, различая отсутствующий каталог
   и пустой. Парные временные fixtures в `tests/tooling/test_retro_inputs.py`.
   Admission deferred и статусы этим сборщиком не переопределяются.

## Регрессия кейсов

Скрипт входов обнаружил ноль agent-case.md: regression агентских кейсов вакуумна,
доли не выдуманы. Shell/checker self-tests реально выполнены:
`sh .harness/scripts/gate.sh --self-test` завершился с кодом 0; лог
`/tmp/chiplog-transcripts-selftest.log`. В нём 14 tests-layout и 26 preflight тестов;
это не полный pytest. Проверка действующего reproducer и полный набор идут в commit gate.

## Воспроизводимый дефект сборщика

`python3 /tmp/chiplog-retro-valid-deferred-probe.py` создал временный Git-репозиторий,
зафиксировал target/finding/evidence/ratification, провёл реальный `defer-defect.sh`
с текущей observed revision и удалённым handoff. Результат исходного сборщика:
exit 0, корректный open ID отсутствует в выводе, присутствует ложная фраза
«за человека ничего не решено и ничего не отложено». Лог:
`/tmp/chiplog-retro-valid-deferred-probe.log`. Постоянный regression должен проверять
тот же исход и исправленный сборщик, а не подставлять вердикт из expected.

В настоящем дереве `python3 -c "from pathlib import Path;
print(list(Path('.harness/deferred').glob('D-*.md')))"` дал `[]`.
Скрытого существующего долга не обнаружено; доказана достижимая неполнота сборщика.

## Фрикшены

| Фрикшен | Выбор | Адрес |
|---|---|---|
| Ретро вручную ищет второй ledger, старый вывод обещает отсутствие всех tail | Сгладить: один сборщик, оба источника | `.harness/scripts/retro-inputs.sh` |
| Проверки формата зависят от мигрируемого авторского сценария | Сгладить: самостоятельная format fixture | Уже текущий `tests/evaluation/test_transcript_compiler.py`; product test, не новое правило |
| Непроверенный stdin entrypoint не совместим со spawn | Не встретиться: использовать уже документированную CLI-команду | `design-docs/TRANSCRIPTS.md`; норма/инструмент уже есть, нового правила нет |

## Классы, а не случаи

Подтверждённый класс: неполный агрегатор входов после появления отдельного
канонического источника. Плохой вход имеет корректную запись во втором источнике,
а первый отсутствует. Исправление включает обе независимые категории; не зависит
от конкретного ID или от необходимости прямо сейчас закрыть долг.
Не объединяем с ним непроверенные рассказы о telemetry или source drift.

## Кандидаты в правила

Маршрут HARNESS_MODEL «С чего начать», пункт 1: дефект относится к самому харнессу,
поэтому оформляется этой ретро и правкой инструмента. Rule-file и agent-case не
создаются; admission-правила deferred не меняются. После отдельного холодного
контура прочитан `retro/references/actions.md`; это последний содержательный выбор:

| Находка | Класс / маршрут | Адрес результата |
|---|---|---|
| Новый deferred ledger отсутствует во входах ретро | Рассинхрон мета-инструмента, HARNESS_MODEL шаг 1; воспроизводимый вход | `.harness/scripts/retro-inputs.sh` и `tests/tooling/test_retro_inputs.py` |
| Ручное восстановление второго источника стоит лишних действий | Сгладить существующий сборщик | Та же правка; нового процесса/нормы нет |
| Cold review и evidence-bound результаты полезны | Уже существующие controls; новая норма не предлагается | AGENTS §1/§6/§7 остаются без изменения |
| Исторические telemetry/source-drift наблюдения без agent triple | Существующие наблюдения, новый случай не изобретается | `.harness/observations.md`, прежние записи не объявлены закрытыми |
| Текущий stdin launch и format-fixture coupling | Локальные invocation/product-test исправления, не новая meta-норма | Штатная CLI в TRANSCRIPTS.md и независимая V2 fixture в evaluation tests |

Порог, модель, permission flow и gate admission не изменяются.

## Инварианты модели и долги

| # | Основание вердикта | Исход |
|---|---|---|
| 1 | `rules_have_reproducers.py`, входы выше | Не вакуумный: два rule-file; окончательный gate проверяет их |
| 2 | Перечитаны все десять разделов AGENTS.md | У каждого есть названный gate либо честная пометка без gate |
| 3 | `gate.sh --self-test`, лог выше | Парные harness/adapter checks прошли; не доказательство семантики всего продукта |
| 4 | `messages_are_actionable.py` в gate; просмотр новых сообщений | Проверяемый охват сохранён; за пределами checker нет универсального доказательства |
| 5 | `git diff c324672..HEAD -- .harness/scripts/gate.sh` | Строки run не удалены; tests-layout добавлен |
| 6 | `thresholds.sh`, `checks/retro_due.py`, `new-case.sh` | В просмотренных уставках новых дубликатов не найдено; пороги не меняем |

Открытых D-строк в HARNESS_MODEL нет по команде исходного сборщика. Отдельный
канонический deferred ledger также пуст по проверке выше. Исторические observations
не являются автоматически ни закрытыми задачами, ни валидно admitted deferred defects.

## Уставки хайлайтов и пропускная способность

Нет durable журнала срабатываний guard за период; нельзя вывести из этого, что
порог слишком высок или мёртв. `HC_FILES_HARD` остаётся 40, каданс остаётся 5.
Дословный исторический ввод сохранён по процедуре; новые инструкции/запреты ради
одного багфикса не добавляются. Долг о telemetry не объявляется закрытым.

## Холодный контур

Отдельный агент без родительского контекста проверил этот файл, исходный collector,
public admission fixture и доказательство. Подтверждённых P0/P1 нет; отчёт
`/tmp/chiplog-transcripts-retro-cold.md`. Этот вердикт относится к плану ремонта;
не заменяет тесты исправления и staged review. Потолок общей модели сохраняется.

## Итог

Реализован outcome change: сборщик перечисляет второй реестр и не выводит
глобальное отсутствие отложенных работ из отсутствия handoff. Статусы читаются
только из первого frontmatter, включая неизвестные и дубликаты; сборщик не выдаёт
admission-вердикт и не меняет records.

До исправления `uv run pytest -q /tmp/test_retro_inputs.py` дал 8 failed
(`/tmp/chiplog-retro-inputs-before.log`). После переноса того же набора и исправления:
`uv run pytest -q tests/tooling/test_retro_inputs.py` — 8 passed; затем добавлена
пара handoff present/absent, итог той же команды — 9 passed. Валидная open запись
создаётся публичным defer-defect, terminal запись проходит настоящий retired transition;
неизвестные/дублированные status проверяются как явно повреждённые входы inventory.
Ruff и `git diff --check` прошли. Новый полный сбор входов сохранён отдельно в
`/tmp/chiplog-transcripts-retro-after.log`; первоначальный блок выше не заменён.

Остался обычный предкоммитный порядок: холодный staged review, полный gate и
разрешённый пользователем коммит. До успешного коммита ретро не завершена.
