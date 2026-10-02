---
date: 2026-10-02
# trigger: cadence | incident
trigger: incident
# outcome: change | skip
outcome: change
# id правила, если outcome: change
rule: .harness/scripts/checkpoint.py
# почему нет, если outcome: skip
reason:
---

## Что перечитано

Сюда вставляется вывод `./.harness/scripts/retro-inputs.sh` целиком — он печатается
при заведении записи. Числа приходят вместе с командами, которыми получены; переписывать
их по памяти нельзя (`AGENTS.md` §6).

```text
входы ретро — с последней ретро (f04b8c7)

1. КОПИЛКА  .harness/observations.md
   записей: 16   (grep -c '^- 2[0-9][0-9][0-9]-' .harness/observations.md)
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

2. ДИФФЫ  f04b8c7..HEAD
   312f9ac fix(r8): repin owner journal scan
   78a0715 fix(harness): keep selector lock outside git
   d8c442b fix(harness): make durable selector executable
   ae425a1 perf(platform): avoid duplicate owner journal prefix scan
   3974a26 feat(r14): prepare one-call native cancellation accounting
   объём: 17 files changed, 1223 insertions(+), 27 deletions(-)   (git diff --shortstat f04b8c7..HEAD | sed 's/^ *//' | grep . || echo 'нет изменений')
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

Гоняются не все, а задетые с прошлой ретро плюс помеченные хрупкими.
Требовать полного прогона всего — гарантированно получить приписки.

```sh
./.harness/scripts/replay.sh <id> --harness <ветка>
```

Результат — доля, а не флаг. Один прогон не значит ничего: агент мог провалиться
из-за сэмплирования, а не из-за дыры.

| Кейс | Было при захвате | Стало на сегодняшнем харнессе | Держит? |
|---|---|---|---|
| Нет затронутых agent-кейсов: `find .harness/reproducers -name case.md 2>/dev/null \| wc -l \| tr -d ' '` → 0 | — | — | Проверка вакуумна; нельзя писать «держит» |

`3/3 → 2/3` — правило не держит, что бы ни хотелось записать. Это же и есть
механическая проверка §3 «правило было мёртвым весом».

## Фрикшены

Места, где работа шла тяжелее, чем должна: переспрашивание, переделка, долгий поиск,
спор о решённом. По каждому — развилка: **не встретиться** (был другой путь) или
**сгладить** (путь верный, проходится дорого).

| Фрикшен | Не встретиться или сгладить | Адрес правки |
|---|---|---|
| Срез owner journal изменил pinned Python, но 50 выбранных platform тестов пропустили устаревший R8 SHA | Сгладить: дешёвая identity-проверка при изменении `src/chiplog/**/*.py` в checkpoint | `.harness/scripts/checkpoint.py` |
| Launcher был неисполняемым в установленном checkout, хотя фикстура ставила `chmod` | Не встретиться: тестировать исполняемый бит реального файла; исправлено коммитом `d8c442b` | `tests/tooling/test_run_selector_durable.py` и mode `.sh` |
| Lock в `.git` был недоступен агентской песочнице; переменный `TMPDIR` разбивал бы общий lock | Сгладить: стабильный `/tmp` namespace по UID и Git common dir, с проверкой владельца и mode; исправлено `78a0715` | `.harness/scripts/run-selector-durable.py` |

## Классы, а не случаи

Назови повторяющуюся серию и проверь, образует ли она один класс. Например, три
«дочиненных» теста в разных файлах могут быть одним классом — но только если назван
общий механизм.

Для межкомпонентной гарантии проверь, проходил ли тест через байты реального
producer до consumer, и отвергал ли consumer одностороннюю подмену выбранного
поля или идентичности. Если нет, запиши конкретный пропущенный стык и реши,
нужен ли совместный плохой вход; отдельные успешные тесты сторон его не заменяют.

Класс этого периода — **верный выборочный checkpoint без дешёвой проверки
вторичного identity-контракта**. `ae425a1` изменил
`platform/owner_decision_journal.py`; его 50 выбранных тестов прошли, но
`R8_IMPLEMENTATION_FILES` сохранил прежний SHA. Реальный durable запуск
`tests/architecture/test_r8_current_surfaces.py` затем оставил `failed` receipt
и точный путь расхождения. После узкого `312f9ac` тот же selector оставил
`verified` receipt и `1 passed`. Следовательно, R8 сам обнаруживает ошибку, но
checkpoint ставит эту быструю проверку слишком поздно. Лог не доказывает, что
SHA-каталог должен охватывать все 491 файла: сужение охвата по динамическим
импортам остаётся отдельным недоказанным решением.

Успех периода: установленный durable launcher теперь даёт проверяемый исход
после tool-session, а узкая оптимизация owner journal сохранила полный хвостовой
контроль и прошла 50 профильных тестов. Это не закрывает ни одну из 23 полных
строк `design-docs/R14-R17-COMPLETION.md`; V23 остаётся инертной подготовкой.

## Кандидаты в правила

Ворота: есть ли прогоняемый вход. Есть и падает — правило рождается, сколько бы раз
провал ни случался. Нет — наблюдение ждёт родню в копилке; признак созревания не число
повторов, а формулировка: класс назван так, что под него подходят все накопленные записи.

У сбоя либо есть одна устранимая причина, либо так шумит сама система. Шуму системы
не отвечают правилом — ему отвечают системой.

Форму нового правила сверить с `.harness/HARNESS_MODEL.md`, маршрут «С чего начать»: он решает,
это раздел `AGENTS.md` или правило-файл, и какие поля обязательны.

Нового текстового правила нет. `AGENTS.md` §6 уже требует наблюдаемого исхода;
§7 уже ограничивает checkpoint. Падающий вход принадлежит проверке: staged
изменение pinned Python без repin не должно проходить checkpoint.
`verify_implementation_identity()` теперь вызывается в `.harness/scripts/checkpoint.py` только
при staged Python или изменённом каталоге; untracked Python под `src/chiplog`
отклоняется отдельно, потому что verifier увидит его вне index. Автоматически переписывать SHA нельзя:
это превратило бы проверку в самоаттестацию. Положительный вход — согласованный
одноключевой repin; отрицательные — устаревший SHA и новый Python без ключа.
`UV_CACHE_DIR=/private/tmp/chiplog-uv-cache uv run pytest -q
tests/tooling/test_checkpoint_gate.py tests/verification/test_r8_surface.py`
дал `90 passed in 7.35s`: real-verifier fixture проверяет старый SHA,
согласованный repin, staged и untracked новый файл, изменение только каталога
и сломанную загрузку; на отказах выбранный pytest не стартует. Сам identity
в отдельном вызове под `python3` занял 0.072 с (измерение исполнителя), а не
время полного checkpoint.

| Находка | Класс из `references/actions.md` | Адрес и исход |
|---|---|---|
| Checkpoint пропустил устаревший SHA после staged Python-правки | Фрикшен: нужный путь дорог из-за поздней проверки → правка инструмента | `.harness/scripts/checkpoint.py`; bad/good реальным verifier проверены в `tests/tooling/test_checkpoint_gate.py` |
| Launcher не запускался из checkout и не мог писать lock в Git directory | Фрикшен инструмента | `.harness/scripts/run-selector-durable.sh` и `.harness/scripts/run-selector-durable.py`; исправлены отдельными коммитами `d8c442b`, `78a0715`, установленный R8 selector оставил verified receipt |
| R8 каталог часто перепинивается, но безопасный меньший охват ещё не доказан | Наблюдение без подтверждённой альтернативной проверки | `.harness/observations.md`, запись `R8 pin maintenance cost` от 2026-10-02 |
| Уже существующая R8 identity отвергла старый SHA | Работающий контроль; новой нормы человека нет | `src/chiplog/verification/r8_surface.py` сохранён, изменение ему не нужно |

## Инварианты модели и долги

`.harness/HARNESS_MODEL.md` задаёт владельца вердикта для каждого инварианта. Ретро сводит
результаты всех шести: 1, 3 и 4 устанавливают проверки, а 2, 5 и 6 — сама ретро.

| # | Инвариант | Как проверить | Держится? |
|---|---|---|---|
| 1 | у правила-файла есть вход либо непросроченная норма | `rules_have_reproducers.py --status` → 2; `gate.sh --self-test` | Действующие файлы не менялись, agent replay 0; self-test ниже |
| 2 | у каждого раздела `AGENTS.md` есть гейт или пометка `[без гейта]` | `grep -c '^## [0-9]' AGENTS.md` → 10; чтение разделов | Да в прочитанном охвате, 10 разделов |
| 3 | у каждого файла проверки есть парный блок в `--self-test` | `UV_CACHE_DIR=/private/tmp/chiplog-uv-cache sh .harness/scripts/gate.sh --self-test` | 14 пар + 32 adapter tests прошли; охват ровно тот, что объявляет self-test |
| 4 | каждое сообщение о провале несёт метку и владельца | `python3 .harness/scripts/checks/messages_are_actionable.py` и тот же self-test | Оба прошли; смысл сообщений вне синтаксического гейта остаётся на ревью |
| 5 | из `gate.sh` не убыло строк `run` без записи ретро | `git diff f04b8c7..HEAD -- .harness/scripts/gate.sh` → пусто | Да за период; строк `run` 11 |
| 6 | ни один порог не в двух местах исполняемого кода | `.harness/scripts/thresholds.sh`, `checks/*.py`, `new-case.sh`; diff периода | Новый порог не вводился; расхождения в просмотренном наборе не найдено |

**Долги.** Список — в `.harness/HARNESS_MODEL.md`, раздел «Текущие долги». Два вопроса, не один:

1. **Сработал ли крючок?** Сработал — исход на этой ретро. Отсрочка одна, с причиной в журнале
   решений; на второй решает человек.
2. **Сколько ретро долгу?** Три без закрытия — исход обязателен независимо от крючка.

| Долгов было | Стало | Закрыто | Заведено |
|---|---|---|---|
| 0 (`grep '^| D' .harness/HARNESS_MODEL.md`) | 0 | 0 | 0 |

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

Durable-журнала guard/highlight за пять коммитов нет: частоту и точность порогов
оценить нельзя. Полугодовое молчание из этого окна не следует; уставки не менялись.

## Пропускная способность

Есть ли файл харнесса, который перестал читаться за один присест? Он идёт под нож:
сжать, слить, вынести в скрипт.

Из прочитанных `AGENTS.md`, `.harness/HARNESS_MODEL.md` и `checkpoint.py`
нового файла, который нельзя прочитать за один присест, нет. Копилка содержит
16 записей по выводу `retro-inputs.sh`; удалять старые без отдельной проверки
их действующих адресов этот период не позволяет.

## Итог

Одна из двух веток, обе легальны, обе оставляют след:
- `outcome: change` — урок доведён до правки харнесса, указан `rule`;
- `outcome: skip` — правки нет, указан `reason`.

Ретро заканчивается коммитом.

Выбран `outcome: change`: R8 identity вызывается в checkpoint только когда
staged срез меняет `src/chiplog/**/*.py` или сам каталог; untracked Python
под исходниками отклоняется до проверки. Плохой вход —
изменённый pinned Python с прежним SHA — падает до выбранного pytest;
согласованный repin и неизменный срез проходят. Этот адрес не снимает вопрос
о будущем сужении каталога; оно требует доказать динамические импорты и
сохранить R8 отрицательный вход.
