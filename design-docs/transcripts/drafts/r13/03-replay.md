# Повтор точной команды

**Статус: design · format v2 · stateful profile v2.** Ожидаемый сценарий, не запись прогона.
[Основной формат](../../../TRANSCRIPTS.md) · [Stateful-профиль](STATEFUL-PROTOCOL.md) · [Общие данные](common.md).

**Связь с текущим кодом:** `planning.execute` и observer paths — неподключённые draft bindings. Capture полного результата должен быть сопоставлен с PlanningCommittedResult; проектируемый путь `intention_id` пока не соответствует production-полю `intention_line_id`.

> **User story.** Не получить две одинаковые записи, если одна и та же команда пришла повторно.

**Сценарий, prefix и transport inputs:**

```yaml transcript
scenario:
  format_version: 2
  protocol_version: 2
  id: r13-command-replay
  version: 4
  status: design
  arrange:
    use: common.md#/scenario/arrange
  initial_context:
    use: common.md#/scenario/initial_context
  policy:
    use: common.md#/scenario/policy
  fixtures: []
  requirements:
    authority_still_valid: true
    prefix_stops_before_next_model_call: true
    conflict_fault_must_be_reached: true
  variants:
    identical:
      prefix:
        source: 01-intention-from-history.md
        variant: quarterly
        until: accept.commit
        capture_namespace: prefix
        inherit_current: true
        inherit_context: true
        fresh_run: true
      steps:
      - replay
      allowed_outcome: same_result_without_new_write
      parameters:
        history_fixture: common.md#/scenario/fixtures/history-quarterly
    changed-payload:
      prefix:
        source: 01-intention-from-history.md
        variant: quarterly
        until: accept.commit
        capture_namespace: prefix
        inherit_current: true
        inherit_context: true
        fresh_run: true
      steps:
      - conflict
      allowed_outcome: conflict_without_overwrite
      parameters:
        history_fixture: common.md#/scenario/fixtures/history-quarterly
```

Prefix [01](01-intention-from-history.md) останавливается сразу после `accept.commit`: команда C и результат R захвачены из реального owner boundary. Финальный ответ модели ещё не исполнялся.

## Идентичный повтор

```yaml transcript
step:
  id: replay
  input:
    kind: owner_command_replay
    envelope: $prefix.accepted_command
    bytes: identical
  end: replay.result
```

**За кадром, replay:** Повторно доставляет ровно те же байты принятого envelope C. Это не новая пользовательская реплика.

**Ожидаемые вызовы и результаты:**

```yaml transcript
expect:
  step: replay
  calls:
  - id: replay.execute
    boundary: owner
    operation: planning.execute
    envelope:
      eq: $prefix.accepted_command
    count: 1
    result:
      source: real_boundary
      fields:
        disposition: REPLAY
        result: $prefix.result
```

<details>
<summary>Точные события, связи и контекст этого шага</summary>

```yaml transcript
expect:
  step: replay
  events:
  - id: replay.result
    operation: planning.result
    count: 1
    same_call_as: replay.execute
    fields:
      disposition: REPLAY
      result: $prefix.result
  context:
    planning_identity:
      eq: $prefix.result.intention_id
    new_render_revision_allowed: true
```

</details>

**Ожидаемый результат владельца:** `REPLAY`, тот же полный R. Новый ответ Chiplog для этой проверки не требуется.

**Ожидаемое состояние к концу шага:**

```yaml transcript
expect:
  step: replay
  baseline: step.start
  state:
    planning.results:
      delta: 0
    planning.intentions:
      delta: 0
    planning.revisions:
      delta: 0
    calendar.execution_intents:
      delta: 0
    provider.effects:
      delta: 0
    planning.result:
      eq: $prefix.result
```

## Тот же ID, другой payload

```yaml transcript
step:
  id: conflict
  input:
    kind: owner_command_fault
    envelope: $prefix.accepted_command
    replace:
      path: command.purpose
      value: Другое намерение
    preserve:
    - command.id
    - authority_act
    - owner_allocated_ids
  end: conflict.result
```

**За кадром, conflict:** В копии C меняет только purpose на «Другое намерение», сохраняя command ID.

**Ожидаемые вызовы и результаты:**

```yaml transcript
expect:
  step: conflict
  calls:
  - id: conflict.execute
    boundary: owner
    operation: planning.execute
    envelope:
      eq_input: true
    count: 1
    result:
      source: real_boundary
      fields:
        disposition: CONFLICT
        result: null
```

<details>
<summary>Точные события, связи и контекст этого шага</summary>

```yaml transcript
expect:
  step: conflict
  events:
  - id: conflict.fault
    operation: fault.reached
    count: 1
    fields:
      fault: conflict.input
  - id: conflict.result
    operation: planning.result
    count: 1
    same_call_as: conflict.execute
    after:
    - conflict.fault
    fields:
      disposition: CONFLICT
      result: null
```

</details>

**Ожидаемый результат владельца:** `CONFLICT`; ранее созданный R сохранён.

**Ожидаемое состояние к концу шага:**

```yaml transcript
expect:
  step: conflict
  baseline: step.start
  state:
    planning.results:
      delta: 0
    planning.intentions:
      delta: 0
    planning.revisions:
      delta: 0
    calendar.execution_intents:
      delta: 0
    provider.effects:
      delta: 0
    planning.result:
      eq: $prefix.result
    planning.purpose:
      eq: Подготовить квартальный отчёт
```

**Запреты:**

```yaml transcript
forbid:
  use: common.md#/forbid
  predicates:
  - new_command_id_for_replay
  - overwrite_prior_committed_result
  - infer_deduplication_from_call_count_only
  - treat_fault_as_authorized_adoption_change
```

Сравнение ведётся с baseline после первого коммита и по owner publications. Служебные trace/rejection rows допустимы. Новый snapshot допустим, новое намерение — нет. Этот сценарий не сертифицирует recovery неизвестного provider/model outcome или повторную доставку ответа.
