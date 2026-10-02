---
date: 2026-10-02
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

Сюда вставляется вывод `./.harness/scripts/retro-inputs.sh` целиком — он печатается
при заведении записи. Числа приходят вместе с командами, которыми получены; переписывать
их по памяти нельзя (`AGENTS.md` §6).

```text
входы ретро — с последней ретро (1f16e44)

1. КОПИЛКА  .harness/observations.md
   записей: 17   (grep -c '^- 2[0-9][0-9][0-9]-' .harness/observations.md)
   - 2026-10-02 | R8 pin maintenance cost | `git log -20 --format='%H'` с `git diff-tree --no-commit-id --name-only -r` и точным поиском `src/chiplog/inert_shared/r8-implementation-v1.json` дал 14 из последних 20 коммитов с правкой каталога; `tests/architecture/test_r8_current_surfaces.py` после repin прошёл за 0.66 с. Это измеряет частоту ручного перепина и дешёвизну текущей проверки, но не доказывает безопасное удаление 491-file identity: динамические импорты и отрицательный вход для более узкого каталога ещё не закрыты. Пересмотреть охват после такого доказательства, не по числу коммитов | нет
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
   вопрос: что здесь ломается ПОСТОЯННО, а не что сломалось

2. ДИФФЫ  1f16e44..HEAD
   f722066 fix(r15): isolate historical scheduler source closure
   9a031d1 feat(r14): publish complete native recovery cut
   319ccf5 feat(r14): compact native cancellation trust cut
   b7c6d01 feat(r15): authenticate signed lease execution
   5119717 feat(r17): bind native one-call context to prepare
   объём: 71 files changed, 12429 insertions(+), 84 deletions(-)   (git diff --shortstat 1f16e44..HEAD | sed 's/^ *//' | grep . || echo 'нет изменений')
   незакоммиченного сейчас (в период НЕ входит): 4   (git status --porcelain | wc -l | tr -d ' ')

3. ХВОСТЫ  .harness/handoff.md
   записей: 5   (python3 .harness/scripts/checks/handoff_pending.py --status | sed -n 's/^handoff_total=//p')
   · H1 V2 pre-seal owner-negative inventory, selected full first-path cut and terminal completion; then remaining J1-J7/Phase D R14-R17
   · Смонтированный отрицательный тест замены grant между двумя настоящими SCOPED_V3 B историями, включая restart и актуальную ingress admission
   · H1 SCOPED_V3 restart после шестой записи в новом runtime generation с аутентифицированным successor current worker
   · Явный durable successor claim или recovery после истечения 15-минутной аренды g1
   · Измерить и сократить повторную JSON-сериализацию полного selected V4 при смонтированных H1 recheck
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
   строк run в gate.sh: 11   (grep -c '^run ' .harness/scripts/gate.sh)
   из них адаптеров: 2   (grep -c '^run .*/\(lint\|test\)\.sh' .harness/scripts/gate.sh)
   самотест здесь не запускается; он проверяет harness-пары и ограниченные
   тесты fast/контрактов адаптеров. Досрочно: sh .harness/scripts/gate.sh --self-test
   долги:
   · нет открытых   (grep '^| D' .harness/HARNESS_MODEL.md)

Вставь этот вывод в раздел «Что перечитано» записи ретро.
```

Диффы и треды за период читаются сверх этого: скрипт даёт список коммитов и объём,
но не содержание.

## Регрессия кейсов

`find .harness/reproducers -name case.md 2>/dev/null | wc -l` даёт 0: agent cases отсутствуют, replay-доля вакуумна. `tests/composition/test_r15_lease_v2b_history.py` отвергает мутацию runtime зависимости и проверяет сохранённый V2c fixture валидатором; он не проверяет type-only mutation или mounted restart. Отдельный смонтированный restart `/private/tmp/test_actual_v2c_restart_v2d.py` на сохранённой V2c базе прошёл 1/1. Совместный R15/R14 mounted прогон после V2d прошёл 8/8 (команда в `/private/tmp/chiplog-r15-v2d-repair-handoff.md`).

## Фрикшены

| Фрикшен | Развилка | Адрес |
|---|---|---|
| Зелёные отдельные producer/consumer тесты скрывали неверные R14 attempt/Turn и C→C′ связи. | Не встретиться: реальный выбранный поток и односторонняя подмена раньше отдельных позитивных фикстур. | Уже покрыто `AGENTS.md` §6 и mounted negatives в `9a031d1`; новой нормы нет. |
| R15 V2b historical closure втянула R17 durability, затем R14 workspace через импорты только для аннотаций; оба изменения остановили startup до lease. | Сгладить: pin исполняемый граф, сохранить прежний профиль/историю. | Продуктовый repair `f722066`; в харнессе измерить частоту pin изменений по каждому ретро-периоду. |
| Все пять коммитов периода меняли R8 каталог: `git log 1f16e44..HEAD --format='%h %s' -- src/chiplog/inert_shared/r8-implementation-v1.json`. | Сгладить: измерять цену, прежде чем менять охват. | Наблюдение 14/20 в `.harness/observations.md`; безопасное удаление R8 пока не доказано. |

## Классы, а не случаи

Подтверждённый класс: **исторический SHA профиль включает неисполняемую транзитивную зависимость**. Один вход дал `historical V2b evaluator dependency changed: chiplog.platform.r7_trust_durability` после законного R17 изменения; второй — тот же отказ для `chiplog.platform.workspace_snapshot` после R14. Оба пришли через импорты для отложенных аннотаций. `f722066` перенёс `SelectedOwnerDecision` под `TYPE_CHECKING`, сохранил V2c history через V2d и проверил recursive runtime graph. Два разных файла, один механизм.

Другой повторяющийся класс — пропущенный точный межвладельческий join; его уже держит `AGENTS.md` §6 и реальные отрицательные входы `9a031d1`. Успешный приём: версионированный current профиль и настоящий старый выбранный fixture; старый V2c manifest не переписан.

## Кандидаты в правила

**Кандидат: измерение в `.harness/scripts/retro-inputs.sh`.** Выводить за тот же Git-период число всех коммитов и число коммитов, тронувших R8 source catalog, вместе с исполняемыми командами. Это делает частоту pin работы видимой в каждой ретро без ручного выборочного подсчёта. Текущая проверяемая пара — 5 коммитов и 5 правок R8 после `1f16e44` (`git rev-list --count 1f16e44..HEAD` и тот же вызов с `-- src/chiplog/inert_shared/r8-implementation-v1.json`). Пустая/первая история должна печатать явную область подсчёта, не выдумывать долю. Скрипт уже является носителем пяти входов ретро; это изменение его измерения, не новая агентная норма.

Идея нового пункта `AGENTS.md` §6 отвергнута на холодном контуре: два отказа были в продукте, а падающего agent input против действующего §6 нет. Проверка type-only mutation тоже ещё не существует. Эти требования остаются продуктовым тестовым долгом, не выдаются за пройденный harness reproducer. Полное удаление R8 SHA не обосновано: нет плохого входа для динамического импорта и сохранения identity.

Классификация по `.harness/skills/retro/references/actions.md` после холодных находок:

| Находка | Класс | Адрес |
|---|---|---|
| Pin частота и повторная ручная диагностика closure делают правильный путь дорогим | фрикшен: сгладить инструмент | `.harness/scripts/retro-inputs.sh` — измерение каждого периода |
| Продуктовый type-only import дефект пока не дал падающего agent input для новой нормы | наблюдение без agent reproducer | `.harness/observations.md` — точная граница доказательства |
| Завышенные слова о тестах и четвёртом skip в черновике | ошибка отчёта/полномочий, уже держат наблюдаемый исход §6 и `retro_due.py` | исправленные разделы этой записи; новых правил нет |

## Инварианты модели и долги

| # | Проверка | Вердикт |
|---|---|---|
| 1 | `rules_have_reproducers.py --status`: 2 правила, 1 норма, ревизия через 70 дней | держится в охвате гейта |
| 2 | `grep -c '^## [0-9]' AGENTS.md`: 10; ручной разбор всех 10 нашёл гейт либо `[без гейта]` | держится |
| 3 | `UV_CACHE_DIR=/private/tmp/chiplog-uv-cache sh .harness/scripts/gate.sh --self-test`: парные входы и 14+32 теста прошли | держится; первый запуск без writable uv cache остановила среда |
| 4 | `python3 .harness/scripts/checks/messages_are_actionable.py`: exit 0 | держится в охвате проверки |
| 5 | `git -c core.fsmonitor=false log -p 1f16e44..HEAD -- .harness/scripts/gate.sh`: пусто; `grep -c '^run '`: 11 | держится в периоде |
| 6 | `grep` всех `HC_*` в `.harness/scripts`: определения в `thresholds.sh`, consumers используют переменные | видимых дублей нет |

Долгов D нет (`grep '^| D' .harness/HARNESS_MODEL.md`): было 0, стало 0, закрыто 0, заведено 0. Пять хвостов handoff не являются долгами модели.

## Уставки хайлайтов

Долговременной статистики срабатываний `guard-edit.sh` и частоты хайлайтов нет; объявлять пороги удачными нельзя. `thresholds.sh` задаёт 10/40 файлов, 4 часа, handoff 5, AGENTS 2000 слов, cooldown 100 ходов. Нет доказательства, что эти пороги вызвали R15 source closure сбой. Уставки не меняются.

## Пропускная способность

`wc -w AGENTS.md` даёт 902 слова при `HC_AGENTS_WORDS=2000`; новый пункт туда не добавляется. Первоначальный `retro-inputs.sh` дал 60 строк между разделителями, они сохранены выше без сокращения. Новая строка наблюдения после этого подняла копилку с 17 до 18; первоначальный снимок не переписывался.

## Холодный контур черновика

- **P1, основание нормы:** два продуктовых startup отказа не являются падающим входом агента для новой нормы `AGENTS.md` §6. Исправлено: кандидат перенесён в измерение существующего `retro-inputs.sh`; продуктовый урок остаётся в `f722066`, новая агентная норма не заявляется.
- **P1, завышенные проверки:** committed тест не мутирует type-only зависимость и не открывает mounted базу через startup. Исправлено: раздел регрессии разделяет validator fixture, отсутствие type-only mutation и отдельный mounted V2c restart `1/1`.
- **Риск полномочий:** четвёртый `skip` нельзя выбрать ради закрытия записи. В итоге названы обе ветки, которыми владеет человек, если предложенная правка не выдержит проверки.
- **P1, первая ретро:** первый вариант `MEASURE_RANGE=root..HEAD` исключал корневой коммит при обещании «вся история»; в репозитории это давало 242 вместо 243. Исправлено на `HEAD`. Новый `tests/architecture/test_harness_retro_inputs.py` различает один корневой коммит (1/1) и период после закоммиченной ретро (2/1), включая печатаемые команды.

## Итог

Исход: `outcome: change`, адрес `.harness/scripts/retro-inputs.sh`. Продуктовый сбой уже закрыт `f722066`; ретро делает частоту pin работы видимой на каждом следующем кадансе. `sh .harness/scripts/retro-inputs.sh` напечатал 5 коммитов периода и 5 с R8 catalog с обеими исполняемыми командами. `uv run pytest -q tests/architecture/test_harness_retro_inputs.py`: 1 passed, проверены корневой период 1/1 и период после ретро 2/1; Ruff и `sh -n` прошли. Это метрика частоты, не измерение затраченного времени и не доказательство необходимости всего каталога. Итоговый cold P0/P1 повтор и коммит ещё впереди. `retro_due.py --status` даёт `retro_skips=3`, `retro_max_skips=3`; порог не повышается. Если правка не выдержит итоговую проверку, человек выбирает между обоснованным повышением `MAX_CONSECUTIVE_SKIPS` коммитом и признанием ретро неработающей с содержательной починкой механизма; агент не выбирает четвёртый skip.
