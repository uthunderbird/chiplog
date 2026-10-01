---
date: 2026-10-01
trigger: cadence          # cadence | incident
outcome: skip
rule:
reason: R8 SHA-каталог перепиновался в 5/5 коммитов. Эксперимент дал candidate closure 255/466 и три ожидаемых вердикта, но не доказал fail-closed учёт вычисляемых dynamic imports и совместимость новой broker/manifest identity; handoff №4 остаётся до этих проверок. Нового воспроизводимого дефекта харнесса за период нет.
---

## Что перечитано

Сюда вставляется вывод `./.harness/scripts/retro-inputs.sh` целиком — он печатается
при заведении записи. Числа приходят вместе с командами, которыми получены; переписывать
их по памяти нельзя (`AGENTS.md` §6).

```text
входы ретро — с последней ретро (bf0ed28)

1. КОПИЛКА  .harness/observations.md
   записей: 14   (grep -c '^- 2[0-9][0-9][0-9]-' .harness/observations.md)
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

2. ДИФФЫ  bf0ed28..HEAD
   7bc92cd fix: reserve delivery work records at the physical writer
   2c4fca8 feat: define exact H1 delivery work subject and claim
   7444f89 fix: pair H1 custody inputs across ordinary admission
   314fbbc feat: select ordinary PlanEffect with atomic capacity claim
   b623f9d fix: bind H1 input to full R17 admission publication
   объём: 31 files changed, 1526 insertions(+), 116 deletions(-)   (git diff --shortstat bf0ed28..HEAD | sed 's/^ *//' | grep . || echo 'нет изменений')
   незакоммиченного сейчас (в период НЕ входит): 4   (git status --porcelain | wc -l | tr -d ' ')

3. ХВОСТЫ  .harness/handoff.md
   записей: 4   (python3 .harness/scripts/checks/handoff_pending.py --status | sed -n 's/^handoff_total=//p')
   · H1 V2 pre-seal owner-negative inventory, selected full first-path cut and terminal completion; then remaining J1-J7/Phase D R14-R17
   · Смонтированный отрицательный тест замены grant между двумя настоящими SCOPED_V3 B историями, включая restart и актуальную ingress admission
   · H1 SCOPED_V3 restart после шестой записи в новом runtime generation с аутентифицированным successor current worker
   · Измерить достижимый R8 импортный охват и три отрицательных входа для сужения SHA-каталога; при успехе исправить каталог и manifest с отдельным коммитом
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

Диффы периода просмотрены отдельно: `git diff --shortstat bf0ed28..HEAD` → 31 files changed, 1526 insertions(+), 116 deletions(-). `git log -p bf0ed28..HEAD -- .harness AGENTS.md .codex/hooks.json` пуст. Нынешние незакоммиченные файлы в период не входят.

## Регрессия кейсов

`find .harness/reproducers -name case.md 2>/dev/null | wc -l` → 0. Agent replay вакуумен; утверждения «правило держит» нет. Структурный гейт проверен self-test отдельно.

## Фрикшены

| Фрикшен | Не встретиться или сгладить | Адрес правки |
|---|---|---|
| R8 exact SHA-каталог менялся в 5/5 коммитов (`git log --format=%H bf0ed28..HEAD -- src/chiplog/inert_shared/r8-implementation-v1.json`); в предыдущей ретро было 4/5 | Сгладить после candidate closure и трёх проб: осталось закрыть вычисляемые dynamic imports и влияние новой broker/manifest identity | `src/chiplog/verification/r8_surface.py`, каталог; handoff №4 со сроком «следующим», правка ещё не безопасна |
| Новый R17 custody batch содержит несколько admission rows, а H1 ожидал singleton публикацию | Не встретиться: совместный consumer запускать на байтах нового producer до заявления совместимости; эта проверка уже поставлена в `.harness/retro/TEMPLATE.md` | `b623f9d` связал полную публикацию с одной исходной custody записью и добавил отрицательный тест усечённых IDs |
| После ordinary PlanEffect H1 считал все admission decisions custody decisions | Не встретиться: проверять смешанный хронологический поток после объединения owner routes | `7444f89` отделил custody decisions при сопоставлении H1 records, сохранив полный R17 validator |
| Pure delivery-work контракт не закрывал generic physical writer | Не встретиться: искать обход на writer cut до положительной публикации | `7bc92cd` добавил отказ для чужих operation/schema/ID и no-resolver; положительный путь остаётся отдельной работой |

## Классы, а не случаи

Повторился межкомпонентный класс: отдельно верные owner records и reader assumptions не дают гарантии их совместного выбранного и физического пути. При объединении веток H1 отвергал реальный R17 custody batch с admission rows; затем после ordinary PlanEffect H1 ошибочно сопоставлял custody records со всеми admission decisions. Смонтированный тест и холодный интеграционный обзор нашли эти два разных проявления, исправления проверяют полный пакет и отдельно извлекают исходный custody input. Полный V4 сценарий на дереве `7444f89` прошёл `uv run pytest -q tests/composition/test_h1_v4_selected_publication.py` → `1 passed in 1733.44s` (интерактивный вывод exec session 86215; отдельный log-файл не сохранялся); это один маршрут, а не закрытие R14–R17.

Удачный подход: закрывать новый физический namespace отрицательным writer тестом до положительного publisher. Он применён в `7bc92cd`, но закреплять его новой нормой агент не вправе; он уже следует общему критерию плохого входа `AGENTS.md` §6. Конкретный вопрос про producer→consumer bytes находится в шаблоне ретро, а не дословно в §6. Прежний холодный обзор пропустил singleton-предположение; обзор после объединения веток обнаружил ordinary/H1 несовместимость. Нулевые P0/P1 в одном обзоре не доказывают отсутствие других стыков.

## Кандидаты в правила

| Находка | Класс по `references/actions.md` | Адрес / исход |
|---|---|---|
| Межкомпонентная несовместимость H1/R17 | Продуктовый плохой вход; процессуальный критерий уже есть | Тесты `test_h1_selected_input_role.py`, `test_h1_historical_h0_r17.py`; отдельного agent-rule без исходной тройки нет |
| Generic writer принимал бы delivery-work без обязательного resolver | Продуктовая физическая дыра | `_sqlite.py` и `test_h1_publication_guard.py` изменены в `7bc92cd`; нового правила харнесса нет |
| 5/5 SHA repin | Дорогой правильный путь → правка инструмента после proof | `r8_surface.py` и каталог; handoff №4. Candidate closure и три пробы сделаны; нужен fail-closed учёт computed dynamic imports и проверка новой broker/manifest identity |

Новых `case.md` нет. Hook SHA-пины исполняемых скриптов в периоде не менялись (`git log bf0ed28..HEAD -- .codex/hooks.json` пуст); их не смешиваем с R8 source catalog. При расширении/сужении каталога меняется R8 manifest identity, поэтому эксперимент должен фиксировать прежнюю и новую идентичность, а не переписать хеши автоматически.

### Измерение R8 scope по handoff №4

`python3 /tmp/r8_scope_probe_oct1c.py` на копии `src/` от `7bc92cd` посчитал 466 Python файлов: candidate scope 255, вне его 211. Алгоритм обходит все AST imports, включая локальные и относительные, package `__init__`, а также каждый строковый literal `chiplog.*` в достижимых файлах от `chiplog.cli` и `chiplog.composition.r8`. На копии он дал: baseline `PASS`; комментарий в постороннем `composition/r14_execution_runtime.py` — `PASS`; комментарий в достижимом `domain_primitives/__init__.py` — `FAIL digest`; новое ребро импорта из `composition/r8.py` к изменённому постороннему модулю даже после обновления SHA импортёра — `FAIL inventory`. Это три различающих результата candidate verifier, **не** действующего `verify_implementation_identity()`.

Прототип `/tmp/r8_scope_probe_oct1c.py` не менял tracked files. Граница результата: сбор строковых targets покрывает сегодняшние просмотренные owner/manifest loaders, но не отвергает будущий вычисляемый module name или новый reflective loader. Production verifier должен явно распознавать разрешённые динамические загрузчики и падать на неразрешённом target. `architecture/r7_runtime.py` хеширует весь каталог в `broker_version`; наследующие manifest identities тоже требуют отдельной проверки. До этих двух входов сужение было бы недоказанным ослаблением.

## Инварианты модели и долги

| # | Инвариант | Как проверить | Держится? |
|---|---|---|---|
| 1 | У правила-файла есть вход или актуальная норма | `rules_have_reproducers.py --status` → 2; case.md → 0 | Структурный гейт проходит; agent replay вакуумен |
| 2 | У раздела `AGENTS.md` есть гейт или `[без гейта]` | `rg -n '^## [0-9]|\\[без гейта\\]|Гейт:' AGENTS.md` → 10 разделов просмотрены | Да |
| 3 | У проверок есть парные self-test | `sh .harness/scripts/gate.sh --self-test` | 14 базовых + 32 fast/adapter прошли с доступом к uv cache |
| 4 | Сообщение провала называет действие и владельца | `messages_are_actionable.py` внутри self-test | Механическая часть да; смысл оценён чтением |
| 5 | Строка `run` не удалена без ретро | `git log -p bf0ed28..HEAD -- .harness/scripts/gate.sh` пуст | Да |
| 6 | Порог не дублируется в исполняемом коде | `thresholds.sh`, `checks/*.py`, дифф периода | Новых дубликатов нет; глобальная уникальность не доказана |

Долгов модели было 0, стало 0 (`grep '^| D' .harness/HARNESS_MODEL.md`). Handoff: четыре carried записи; №4 относится к SHA эксперименту. В `.harness/deferred` записей нет. Критерий долга модели не сработал.

## Уставки хайлайтов

Durable-журнала срабатываний `guard-edit.sh` за период нет. Частоту и шестимесячное молчание установить нельзя; пороги не менялись.

## Пропускная способность

`AGENTS.md` остаётся 10 разделами; `retro-inputs.sh` читается одним прогоном. Подтверждённого файла харнесса, переставшего читаться за присест, нет.

## Итог

`outcome: skip`: фрикшен R8 вырос до 5/5 коммитов. Измерение 255/466 и три candidate-пробы выполнены сейчас, но оно не доказывает fail-closed computed dynamic imports и совместимость новой broker/manifest identity. Это конкретные оставшиеся части handoff №4, а не вывод «чинить нечего». Нового воспроизводимого дефекта харнесса в периоде нет; продуктовые P1 исправлены в отдельных коммитах. Следующий триггер — прототип verifier, который отвергает новый unresolved dynamic loader, и тесты обеих manifest identities; затем возможен отдельный change-коммит с плохими входами.

## Приложение: прототип R8 scope

Запуск из корня этого репозитория: сохранить блок как `/tmp/r8-scope-probe.py`, затем `python3 /tmp/r8-scope-probe.py`. Скрипт меняет только временную копию `src/`; это эксперимент, не production verifier.

```python
import ast, pathlib, importlib.util, json, hashlib, tempfile, shutil
ROOT=pathlib.Path.cwd()
def closure(root):
 r=root/'src'; files={'.'.join(p.relative_to(r).with_suffix('').parts).removesuffix('.__init__'):p for p in (r/'chiplog').rglob('*.py')}; graph={}
 for m,p in files.items():
  deps=set()
  for n in ast.walk(ast.parse(p.read_text())):
   if isinstance(n,ast.Import): deps.update(a.name for a in n.names)
   if isinstance(n,ast.ImportFrom):
    base=importlib.util.resolve_name('.'*n.level+(n.module or ''),m if p.name=='__init__.py' else m.rpartition('.')[0]) if n.level else n.module or ''
    deps.add(base);deps.update(base+'.'+a.name for a in n.names)
   if isinstance(n,ast.Constant) and isinstance(n.value,str) and n.value.startswith('chiplog.'): deps.add(n.value.partition(':')[0])
  deps.update('.'.join(m.split('.')[:i]) for i in range(1,len(m.split('.'))))
  graph[m]=deps & files.keys()
 seen=set();todo=['chiplog.cli','chiplog.composition.r8']
 while todo:
  m=todo.pop()
  if m in seen:continue
  seen.add(m);todo.extend(graph[m]-seen)
 return {files[m].relative_to(root).as_posix() for m in seen}, {p.relative_to(root).as_posix() for p in files.values()}
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def verify(root,cat):
 scope,_=closure(root)
 if scope != set(cat):return 'FAIL inventory new='+str(len(scope-set(cat)))+' sample='+str(sorted(scope-set(cat))[:3])
 changed=[p for p in scope if digest(root/p)!=cat[p]]
 return 'FAIL digest '+str(sorted(changed)) if changed else 'PASS'
with tempfile.TemporaryDirectory(prefix='r8-scope-') as temp:
 root=pathlib.Path(temp);shutil.copytree(ROOT/'src',root/'src')
 scope,allfiles=closure(root);cat={p:digest(root/p) for p in scope}
 print('total',len(allfiles),'in',len(scope),'out',len(allfiles-scope))
 print('baseline',verify(root,cat))
 unrelated='src/chiplog/composition/r14_execution_runtime.py';assert unrelated not in scope
 p=root/unrelated;p.write_text(p.read_text()+'\n# unrelated edit probe\n');print('unrelated',verify(root,cat))
 helper='src/chiplog/domain_primitives/__init__.py';assert helper in scope
 p=root/helper;orig=p.read_bytes();p.write_bytes(orig+b'\n# reachable helper probe\n');print('reachable',verify(root,cat));p.write_bytes(orig)
 p=root/'src/chiplog/composition/r8.py';p.write_text(p.read_text()+'\nimport chiplog.composition.r14_execution_runtime\n')
 cat['src/chiplog/composition/r8.py']=digest(p)
 print('edge with importer digest updated',verify(root,cat))
```
