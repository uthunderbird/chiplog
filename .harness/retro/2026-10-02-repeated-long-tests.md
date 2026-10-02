---
date: 2026-10-02
# trigger: cadence | incident
trigger: incident
# outcome: change | skip
outcome: skip
# id правила, если outcome: change
rule:
# почему нет, если outcome: skip
reason: Явный запрет повторного долгого прогона и требование наблюдаемого исхода уже были даны; agent replay вход не сохранён. Попытка найти существующий admission point для плохого входа не нашла перехвата прямого pytest; новый launcher можно обойти, пока его не подключили к фактическому пути. Инцидент записан в наблюдения, новое правило без проверяемого входа не введено.
---

## Что перечитано

Вывод `./.harness/scripts/retro-inputs.sh` целиком; он фиксирует период до новых правок этой ретро.

```text
входы ретро — с последней ретро (605b3e3)

1. КОПИЛКА  .harness/observations.md
   записей: 15   (grep -c '^- 2[0-9][0-9][0-9]-' .harness/observations.md)
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

2. ДИФФЫ  605b3e3..HEAD
   b551f9e feat(r14): project original native prepare
   2a31513 feat(h1): add scoped authorization command projection
   f85d191 feat: read selected H1 semantic registry source
   84e67ff feat: read selected H1 mandate exception roles
   объём: 11 files changed, 1481 insertions(+), 2 deletions(-)   (git diff --shortstat 605b3e3..HEAD | sed 's/^ *//' | grep . || echo 'нет изменений')
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

В периоде нет сохранённых `case.md`, поэтому долю replay измерить нельзя.

| Кейс | Было при захвате | Стало на сегодняшнем харнессе | Держит? |
|---|---|---|---|
| Нет `case.md`: `find .harness/reproducers -name case.md` дал 0 | — | — | Вакуумно; agent replay не заявлен |

## Фрикшены

Каждый фрикшен отнесён к выбору пути или к стоимости нужного пути.

| Фрикшен | Не встретиться или сгладить | Адрес правки |
|---|---|---|
| Пустой yield долгого pytest был принят за завершение; в двух worktree повторили запуск и тратили CPU параллельно | Сгладить: один наблюдаемый запуск с PID, логом и конечным exit/status; сначала воспроизвести отказ второй попытки и восстановление после остановки | Кандидат в launcher/test-run ledger; до проверки — `.harness/observations.md` |
| Коммит semantic reader запустил полный hook после отдельной успешной checkpoint-проверки | Не встретиться: передавать `CHIPLOG_COMMIT_MODE=checkpoint` и selectors именно процессу `git commit`; повторная попытка дала 6 passed | Уже описано в `AGENTS.md` §7 и `gate.sh`; нового адреса нет |
| Новый R14 cut упёрся в смешение template/rendered hash и семантику output scope | Не встретиться: прочитать фактический selected Prepare и scope contract до обещания accounting; сделать read-only источник первым | Продуктовые контракты/тесты R14, вне харнесса |

## Классы, а не случаи

Повторился один процессный симптом: после пустого tool-yield исполнители не удержали
идентичность уже запущенного долгого теста. В задании `h1_current_effects_terra`
дословно было «Avoid long V22 selector duplicate while trust process active»; это
задание сохранено в текущей сессии. Первый исполнитель сообщил о нескольких
дубликатах в `/private/tmp/chiplog-h1-current-trust-terra/design-docs/V22-H1-CURRENT-TRUST-READER-PLAN.md:102`;
точное их число независимо не доказано. Второй исполнитель записал два родительских
PID 13643/15834 и их остановку в `/private/tmp/chiplog-h1-current-effects-terra/IMPLEMENTATION_PLAN.md:38`;
родитель сохранил исходный trust-прогон 84730/84738. Второй исполнитель сначала
сообщил о якобы успешном mounted тесте после пустого 31-секундного tool yield, затем
письменно отозвал вывод, указав отсутствие `exit_code` и pytest summary; это отчёт
сессии, не независимый pytest-лог. Отказ доказательства успеха требует `AGENTS.md` §6.
Исходная тройка для независимого agent replay не сохранена. Наблюдение внесено в
`.harness/observations.md`; новое текстовое правило не обосновано.

Это не тот же механизм, что наблюдение R8 2026-09-12/10-02 о правке source во время
широкой проверки: здесь лишние процессы и преждевременный вывод, там дрейф проверяемого
снимка. Проверки текущих четырёх коммитов были узкими checkpoint; они не доказывают весь
V22 runtime. R14 template/rendered hash и H1 semantic operation — продуктовые стыки,
для которых нужны реальные producer→consumer байты и односторонние подмены, а не новое
правило по одному совпадению слов.

Сработавшее поведение: изолированные worktree позволили остановить лишние процессы,
сохранив исходный trust-прогон и общий каталог; холодная проверка результата удержала
неподтверждённый mounted pass. Эти границы уже названы в `AGENTS.md` §6 и в правилах
делегирования §9; новая норма без слова человека не устанавливается.

## Кандидаты в правила

Попытка построить плохой вход для действующего харнесса: `rg -n
'flock|lockfile|pytest.*session|long.test|test.run|RUNNING|session_id'
.harness/scripts .githooks tests/tooling` нашёл лишь локальный lock проверки
`post-tool-checks.py` и lock журнала `deferred.py`. Оба обслуживают свои файлы;
ни один не принимает или не перехватывает прямой `uv run pytest`. Поэтому вход
«вторая копия того же теста при живом первом» пока некуда подать как отказ
существующей проверки. Само параллельное выполнение двух процессов было бы
свойством оболочки, а не падающим входом харнесса. Это конкретный блокер пробы,
а не вывод, что защита невозможна.

Кандидат на техническую защиту: launcher или test-run ledger с атомарным захватом
реального общего ресурса, PID/start identity, логом, статусом RUNNING/terminal и
восстановлением устаревшей записи. Сначала нужен плохой вход «вторая попытка при живом
первом проходит» и хороший вход «после завершения первый ресурс освобождён», плюс проверка,
что пустой yield остаётся RUNNING. Ключ только по pytest selector может зря запретить
безопасные независимые worktree; общие CPU/DB и цена теста должны определить границу.
Необязательный launcher обходится прямым `uv run pytest`, поэтому будущий отрицательный
тест обязан идти через реальный путь запуска или защита не имеет заявленного охвата.
Без этих входов скрипт или правило создаст непроверяемый долг. Инцидент оставлен в
копилке; `kind: agent` case не создавался, потому что независимый replay не собран.

| Находка | Класс по `references/actions.md` | Артефакт в диффе |
|---|---|---|
| Повторные долгие запуски и ложный отчёт при пустом yield, без agent replay | Наблюдение: вход не собран | Новая строка в `.harness/observations.md` |
| Механический launcher пока не имеет проверенного direct-pytest admission и recovery входа | Наблюдение о несозревшем инструменте | Та же строка в `.harness/observations.md`; неподключённого скрипта нет |

## Инварианты модели и долги

Сверены шесть инвариантов `.harness/HARNESS_MODEL.md`; вывод ограничен указанными проверками.

| # | Инвариант | Как проверить | Держится? |
|---|---|---|---|
| 1 | у правила-файла есть вход либо непросроченная норма | `rules_have_reproducers.py --status`: 2 правила, 1 норма, ревизия через 70 дней; gate self-test проверил плохой/хороший вход | Да в охвате гейта; agent `case.md` по входам 0 |
| 2 | у каждого раздела `AGENTS.md` есть гейт или пометка `[без гейта]` | `rg -n '^## [0-9]|Гейт:|Гейт частичный:|без гейта' AGENTS.md`: 10 разделов, каждый имеет носитель | Да, визуальная сверка 10 разделов |
| 3 | у каждого файла проверки есть парный блок в `--self-test` | `UV_CACHE_DIR=/private/tmp/chiplog-uv-cache sh .harness/scripts/gate.sh --self-test`: 8 пар checks, 14 tests-layout, 32 fast/adapter | Да в заявленном охвате; адаптеры не являются отдельными парными checks |
| 4 | каждое сообщение о провале несёт метку и владельца | Тот же self-test прошёл пару `messages_are_actionable.py`; сообщения нового launcher отсутствуют | Да в проверенном охвате, вне его не утверждается |
| 5 | из `gate.sh` не убыло строк `run` без записи ретро | `git diff 605b3e3..HEAD -- .harness/scripts/gate.sh | wc -l` → 0 | Да за период |
| 6 | ни один порог не в двух местах исполняемого кода | `rg -n '^[A-Z_]+ *= *[0-9]+|-(ge|gt|le|lt) +[0-9]+' .harness/scripts --glob '*.py' --glob '*.sh'`; AST-поиск числовых `Compare` по всем `.harness/scripts/**/*.py`, затем визуальная сверка outcome/modality условий | Да в исполняемом наборе `.harness/scripts`: HC_* определены только в thresholds; `RETRO_EVERY=5`, `MAX_CONSECUTIVE_SKIPS=3`, `REVISION_DAYS=90`, `CHECK_TIMEOUT=1.5` по одному владельцу; найденные литералы в прочих Compare — размеры/формат/exit-code, не дубли порога |

**Долги.** Таблица «Текущие долги» в `.harness/HARNESS_MODEL.md` пуста; нечему
срабатывать и нечему стареть на этой ретро.

| Долгов было | Стало | Закрыто | Заведено |
|---|---|---|---|
| 0 | 0 | 0 | 0 — прочитана таблица «Текущие долги» в `.harness/HARNESS_MODEL.md` |

## Уставки хайлайтов

- Durable-журнала срабатываний `guard-edit.sh` и хайлайтов за период нет; нельзя
  объявить ни отсутствие срабатываний, ни самый частый уровень. Пороги не менялись.

## Пропускная способность

`wc -w AGENTS.md .harness/HARNESS_MODEL.md .harness/scripts/gate.sh` → 902, 1074,
1151 слов соответственно. В этой ретро файл, который нельзя прочитать за присест,
не выявлен; сжатие без конкретной потери чтения не начинаю.

## Итог

`outcome: skip`: явные инструкции уже запрещали дублировать долгий прогон и требовали
наблюдаемого результата; два исполнителя нарушили их. Новый агентский rule без
независимого replay добавил бы текст без проверенного воздействия. Попытка подать
плохой вход на текущий харнесс упёрлась в отсутствие точки приёма прямого pytest;
необязательный launcher не закрыл бы тот же путь. Техническая защита требует
проверенного плохого входа, корректного ключа общего ресурса и хорошего входа на
освобождение. Конкретный инцидент внесён в наблюдения. Ретро закрывается коммитом.
