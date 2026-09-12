# Отложенные дефекты

Канонический реестр доказанных дефектов, которые не блокируют текущий результат.
Это не общий backlog проекта и не замена task tracker.

## Граница с соседними носителями

- подтверждённый blocker текущего результата исправляется сейчас и сюда не попадает;
- сигнал без воспроизводимого входа или concrete trace живёт в
  `.harness/observations.md`;
- новая отсрочка текущей сессии сначала пересказывается человеку через
  `.harness/handoff.md`;
- доказанный и ратифицированный как non-blocking дефект живёт здесь.

Один дефект — один файл `<id>.md`. ID имеет вид
`D-YYYYMMDDTHHMMSSZ-<8 lowercase hex>`: время задаёт удобный порядок, digest не даёт
обычным параллельным веткам молча выбрать одно имя. Коллизия ID — отказ; существующий
файл не перезаписывается.

Файл `.lock` — заранее отслеживаемый process lock, а не запись реестра; его содержимое
не является состоянием.

## Admission contract

Запись допустима только при одновременном выполнении условий:

1. исходный finding имеет стабильный идентификатор и locator отчёта;
2. указаны существующий Git object проверенной ревизии и digest проверенного target;
3. evidence имеет стабильный locator, digest и ожидаемый наблюдаемый verdict;
4. отдельный ratifier зафиксировал disposition `non-blocking`;
5. описано, почему finding не нарушает текущую приёмку;
6. promotion и invalidation заданы проверяемыми условиями с владельцем и следующим
   действием.

`ratification_locator` указывает на файл в `observed_revision`, чей digest совпадает с
`ratification_sha256` и который содержит отдельными строками точные bindings:

```text
finding_id: <finding_id>
disposition: non-blocking
ratified_by: <ratified_by>
```

Ratification-файл содержит ровно эти три непустые строки: дубликаты, дополнительные
поля и противоречивые dispositions отклоняются. Fragment `finding_locator` обязан быть
равен `finding_id`, а finding-файл — содержать точную строку
`finding_id: <finding_id>`.

Отсутствующее, изменённое, `blocking` или `unknown` решение ратификатора отклоняет
admission. Сырой P0/P1/P2 не авторизует отсрочку: разные red-team процессы используют
severity по-разному.

При создании `observed_revision` обязана быть текущим `HEAD`, а bytes target, finding,
ratification и evidence в worktree — совпадать с этой ревизией. Уже существующая запись
сохраняет историческую observed revision; это не делает её новой stale admission.

## Формат записи

```markdown
---
id: D-20260903T120000Z-deadbeef
status: open
finding_id: RT-7
finding_locator: quality/reviews/example.md#RT-7
finding_sha256: <64 lowercase hex>
original_severity: P1
ratified_disposition: non-blocking
ratified_by: human-or-primary-agent-identity
ratified_at: 2026-09-03T12:00:00Z
ratification_locator: quality/reviews/example-ratification.md
ratification_sha256: <64 lowercase hex>
observed_revision: <full Git object id>
target: path/to/file
target_sha256: <64 lowercase hex>
evidence_locator: path/to/reproducer-or-report
evidence_sha256: <64 lowercase hex>
expected_verdict: <observable failing result>
created_at: 2026-09-03T12:00:00Z
admission_sha256: <digest of immutable fields and admission sections>
---

## Finding

Краткий наблюдаемый дефект.

## Why non-blocking now

Какой текущий критерий проверен и почему finding его не нарушает.

## Why deferred

Почему ремонт расширяет текущий scope или сейчас дороже принятого риска.

## Promotion condition

- predicate: проверяемое условие, переводящее дефект в обязательную работу
- owner: кто обязан проверить условие
- next_action: точное действие при `true`
- unknown: `stop` — неизвестный результат нельзя считать `false`

## Invalidation condition

- predicate: проверяемое условие, при котором evidence больше не относится к target
- owner: кто обязан проверить условие
- next_action: `retire` либо повторная ратификация
- unknown: `stop` — неизвестный результат нельзя считать `false`

## History

- 2026-09-03T12:00:00Z | E-<16 lowercase hex> | create | <actor> | <revision> | <evidence locator>
```

Все поля frontmatter, кроме `status`, неизменяемы. Изменения состояния меняют только
`status` и добавляют строку в `History`; старые строки не редактируются и запись
физически не удаляется.

`admission_sha256` связывает поля исходной admission-заявки и пять исходных секций
(`Finding`, обе причины и оба условия). Служебные `id`, `status`, `created_at` и сам
`admission_sha256` в fingerprint не входят. Transition отклоняет запись, если
fingerprint не совпадает.

Неизменяемость здесь — контракт публичных команд: они сохраняют исходные поля
и дописывают историю. Локальные checksums проверяют согласованность записи, но не
защищают от намеренной перезаписи файла. Внешнего доверенного журнала нет;
валидатор не доказывает, что из файла не удалили ранее сохранённое событие.

## Состояния и переходы

Закрытый enum: `open`, `active`, `resolved`, `retired`, `moved`.

- `open -> active`: человек или новая задача явно взяли ремонт в scope;
- `open|active -> resolved`: verifier подтверждает устранение;
- `open|active -> retired`: evidence инвалидировано либо риск явно принят;
- `open|active -> moved`: указан устойчивый ID настоящего task tracker.

Terminal-состояния `resolved`, `retired`, `moved` переходов не имеют. Повтор той же
операции с тем же payload идемпотентен; другой payload после применённого перехода —
конфликт и отказ.

`take-defect` реализует только `open -> active`. `close-defect` реализует только
`open|active -> resolved|retired|moved`. Для `resolved` и `retired` evidence —
repo-relative locator, существующий в revision перехода; для `moved` — стабильная
ссылка вида `tracker:https://...` либо ticket ID вида `tracker:PROJECT-123`.

Идемпотентность определяется `event_id`, вычисленным из ID дефекта, целевого состояния,
actor, точной revision и evidence. Автоматический timestamp в сравнение не входит.
Actor и transition evidence — однострочные scalars без `|` и управляющих символов, чтобы
публичная команда не могла повредить сериализацию `History`.

## Публичная поверхность

Публичная реализация принадлежит трём командам:

```text
./.harness/scripts/defer-defect.sh <admission-file>
./.harness/scripts/take-defect.sh <id> <actor> <revision>
./.harness/scripts/close-defect.sh <id> resolved|retired|moved <actor> <revision> <evidence>
```

Открытые записи сами по себе commit не блокируют; confirmed blocker не может быть
превращён в deferred entry.

Session handoff — отдельная authority boundary. В обычной работе сначала вызови
`defer.sh --next "deferred:<finding_id>" "почему не сейчас"`, затем создай durable entry.
Существующий handoff gate требует пересказа и human ack. Ledger намеренно не дублирует
этот lifecycle и не утверждает, что способен доказать provenance вызова `defer.sh` или
получение человеческого разрешения.
