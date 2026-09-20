# Календарный блок после подтверждения

**Статус: design · format v2 · stateful v2.** Ожидаемый сценарий, не запись исполнения.
[Формат](../TRANSCRIPTS.md) · [План](../R14-R17-TRANSCRIPT-PLAN.md) · [Bindings и наблюдения](drafts/r14-r17/common.md).

> **User story.** Выделить завтра час на отчёт и получить подтверждение создания события без дублей.

Все `cal.*` — проектируемые mappings, не готовые API. Без runtime bindings каждый вариант — `NOT_RUNNABLE`; compile PASS не доказывает поведение. Реплики Chiplog — примеры.

```yaml transcript
scenario:
  format_version: 2
  protocol_version: 2
  id: calendar-proposal-confirmation
  version: 3
  status: design
  arrange:
    use: drafts/r14-r17/common.md#/scenario/arrange
  initial_context:
    use: drafts/r14-r17/common.md#/scenario/initial_context
  policy:
    use: drafts/r14-r17/common.md#/scenario/policy
  fixtures:
  - id: calendar-read
    boundary: cal.read
    match:
      date: '2026-08-23'
      timezone: Asia/Almaty
    result:
      available_intervals:
      - - '2026-08-23T10:00:00+05:00'
        - '2026-08-23T12:00:00+05:00'
    steps:
    - time
  - id: calendar-confirm
    boundary: cal.provider
    match:
      effect: $effect
    result:
      kind: CONFIRM
      provider_event_id: cal-417
      requires_independent_authenticated_receipt: true
    steps:
    - send
  requirements:
    full_envelope_validation: true
    independent_provider_log: true
    no_oracle_in_application_context: true
    production_loop_integration: R18
    all_step_cutoffs_must_be_reached: true
  variants:
    confirmed:
      steps:
      - request
      - time
      - adopt
      - send
      - evidence
      - answer
      allowed_outcome: one_confirmed_calendar_event
      requires:
      - stateful_runtime
      - calendar_payload_mapping
      - R16_broker_dispatch
      - R18_calendar_loop
    duplicate-adoption:
      steps:
      - request
      - time
      - adopt
      - send
      - evidence
      - answer
      - replay
      allowed_outcome: duplicate-adoption
      requires:
      - stateful_runtime
      - calendar_payload_mapping
      - R16_broker_dispatch
      - R18_calendar_loop
    changed-replay:
      steps:
      - request
      - time
      - adopt
      - send
      - evidence
      - answer
      - conflict
      allowed_outcome: changed-replay
      requires:
      - stateful_runtime
      - calendar_payload_mapping
      - R16_broker_dispatch
      - R18_calendar_loop
    stale-display:
      steps:
      - request
      - time
      - stale
      allowed_outcome: redisplay_without_effect
      requires:
      - stateful_runtime
      - calendar_payload_mapping
      - R16_broker_dispatch
      - R18_calendar_loop
    preaccept-dispatch:
      steps:
      - request
      - time
      - premature
      allowed_outcome: rejected_before_provider
      requires:
      - stateful_runtime
      - calendar_payload_mapping
      - R16_broker_dispatch
      - R18_calendar_loop
    crash-before-publication:
      steps:
      - request
      - time
      - crash-before
      - recover-first
      allowed_outcome: one_complete_batch_after_recovery
      requires:
      - stateful_runtime
      - calendar_payload_mapping
      - R16_broker_dispatch
      - R18_calendar_loop
    crash-after-publication:
      steps:
      - request
      - time
      - crash-after
      - recover-replay
      allowed_outcome: original_batch_replayed
      requires:
      - stateful_runtime
      - calendar_payload_mapping
      - R16_broker_dispatch
      - R18_calendar_loop
    invalidation-before-send:
      steps:
      - request
      - time
      - adopt
      - invalidate-before
      allowed_outcome: no_send
      requires:
      - stateful_runtime
      - calendar_payload_mapping
      - R16_broker_dispatch
      - R18_calendar_loop
    send-before-invalidation:
      steps:
      - request
      - time
      - adopt
      - send
      - invalidate-after
      - evidence
      - answer
      allowed_outcome: original_attempt_preserved
      requires:
      - stateful_runtime
      - calendar_payload_mapping
      - R16_broker_dispatch
      - R18_calendar_loop
  faults:
  - id: changed-command
    boundary: broker.replay.before_compare
    count: 1
    require_reached: true
    change:
      field: payload.title
      keep: command_id
  - id: stale-head
    boundary: adoption.before_current_head_check
    count: 1
    require_reached: true
    after: time.done
    change:
      authoritative_head: successor
  - id: preaccept
    boundary: broker.dispatch.before_acceptance_guard
    count: 1
    require_reached: true
    after: time.done
    change:
      attempt: dispatch_unaccepted_command
  - id: publication-before
    boundary: broker.publication.before_commit
    count: 1
    require_reached: true
    change:
      process: crash
  - id: publication-after
    boundary: broker.publication.after_commit
    count: 1
    require_reached: true
    change:
      process: crash
  - id: invalidate-before
    boundary: dispatch.before_send_committed
    count: 1
    require_reached: true
    after: adopt.done
    change:
      authority_head: revoked_successor
  - id: invalidate-after
    boundary: dispatch.after_send_committed
    count: 1
    require_reached: true
    after: send.done
    change:
      authority_head: revoked_successor
```

## Уточнить время

**Пользователь · request:** Завтра надо час поработать над отчётом.

```yaml transcript
step:
  id: request
  input:
    kind: message
    message_ref: request
  end: request.done
```

**Chiplog · request-reply · пример:** Во сколько поставить этот час?

**Ожидаемые вызовы, результаты и состояние:**

```yaml transcript
expect:
  step: request
  baseline: step.start
  calls: []
  events:
  - id: request.clarification
    operation: cal.clarification.accepted
    count: 1
  - id: request.done
    operation: cal.step.closed
    count: 1
    after:
    - request.clarification
  state:
    cal.plan_revisions:
      delta: 0
    cal.effect_intents:
      delta: 0
    cal.provider_sends:
      delta: 0
    cal.claims_without_evidence:
      eq: 0
  response:
    semantics:
    - Уточнение времени; никаких заявлений о созданном событии.
```

## Проверить время и показать предложение

**Пользователь · time:** В 10 утра.

```yaml transcript
step:
  id: time
  input:
    kind: message
    message_ref: time
  end: time.done
```

**Chiplog · time-reply · пример:** Завтра, 23 августа, с 10:00 до 11:00 — «Работа над отчётом». Добавить в календарь?

**Ожидаемые вызовы, результаты и состояние:**

```yaml transcript
expect:
  step: time
  baseline: step.start
  calls:
  - id: time.read
    boundary: model_tool
    operation: cal.read
    count: 1
    arguments:
      date: '2026-08-23'
      timezone: Asia/Almaty
    result:
      fixture: calendar-read
  - id: time.display
    boundary: owner
    operation: cal.display
    count: 1
    arguments:
      title: Работа над отчётом
      starts_at: '2026-08-23T10:00:00+05:00'
      ends_at: '2026-08-23T11:00:00+05:00'
    result:
      source: real_boundary
      capture:
        display: display
        proposal: proposal
        command: command
    after:
    - time.read.result
  events:
  - id: time.done
    operation: cal.step.closed
    count: 1
    after:
    - time.read.result
    - time.display.result
  state:
    cal.plan_revisions:
      delta: 0
    cal.effect_intents:
      delta: 0
    cal.provider_sends:
      delta: 0
    cal.claims_without_evidence:
      eq: 0
  response:
    semantics:
    - Видны точные дата, время, длительность и название; это предложение, не выполненное действие.
```

## Принять подтверждение

**Пользователь · accept:** Да, добавь.

```yaml transcript
step:
  id: adopt
  input:
    kind: authenticated_adoption
    message_ref: accept
    principal: owner
    proposal: $proposal
    display_digest: $display.digest
  end: adopt.done
```

Cutoff prefix T03 — `adopt.commit`. Полный envelope и authority проверяет реальная граница. Здесь ещё нет transmission identity.

**Ожидаемые вызовы, результаты и состояние:**

```yaml transcript
expect:
  step: adopt
  baseline: step.start
  calls:
  - id: adopt.accept
    boundary: owner
    operation: cal.accept
    count: 1
    arguments:
      command: $command
      display: $display
    result:
      source: real_boundary
      fields:
        disposition: COMMITTED
      capture:
        accepted_command: command
        publication_result: result
        effect: effect
  events:
  - id: adopt.commit
    operation: cal.publication.committed
    count: 1
    after:
    - adopt.accept.result
    fields:
      complete: true
  - id: adopt.done
    operation: cal.step.closed
    count: 1
    after:
    - adopt.accept.result
    - adopt.commit
  state:
    cal.plan_revisions:
      delta: 1
    cal.effect_intents:
      delta: 1
    cal.provider_sends:
      delta: 0
    cal.atomic_batch:
      eq: COMPLETE
```

## Отправить принятую работу

Событие среды; пользователь не произносит техническую команду.

```yaml transcript
step:
  id: send
  input:
    kind: observe
  end: send.done
```

Fixture моделирует только внешний ответ. `send.committed` и передача наблюдаются независимо; binding dispatch обязан проверить полный issued ticket до фактических bytes.

**Ожидаемые вызовы, результаты и состояние:**

```yaml transcript
expect:
  step: send
  baseline: step.start
  calls:
  - id: send.dispatch
    boundary: provider
    operation: cal.dispatch
    after:
    - send.committed
    count: 1
    arguments:
      effect: $effect
    result:
      fixture: calendar-confirm
  events:
  - id: send.committed
    operation: cal.send_committed
    count: 1
    after:
    - send.input
    capture:
      transmission: transmission
  - id: send.transfer
    operation: cal.provider.transfer
    count: 1
    after:
    - send.committed
    - send.dispatch.result
    fields:
      outside_writer_transaction: true
      effect: $effect
  - id: send.done
    operation: cal.step.closed
    count: 1
    after:
    - send.dispatch.result
    - send.committed
    - send.transfer
  state:
    cal.transmissions:
      delta: 1
    cal.provider_sends:
      delta: 1
    cal.provider_events:
      delta: 1
    cal.effect:
      eq: $effect
```

## Зафиксировать подтверждение провайдера

Событие среды; пользователь не произносит техническую команду.

```yaml transcript
step:
  id: evidence
  input:
    kind: observe
  end: evidence.done
```

**Ожидаемые вызовы, результаты и состояние:**

```yaml transcript
expect:
  step: evidence
  baseline: step.start
  calls:
  - id: evidence.ingest
    boundary: owner
    operation: cal.evidence
    count: 1
    arguments:
      transmission: $transmission
      source: authenticated_fake_provider
    result:
      source: real_boundary
      fields:
        outcome: CONFIRMED
  events:
  - id: evidence.done
    operation: cal.step.closed
    count: 1
    after:
    - evidence.ingest.result
  state:
    cal.outcome:
      eq: CONFIRMED
    cal.provider_sends:
      delta: 0
    cal.claims_without_evidence:
      eq: 0
```

## Сообщить результат

Событие среды; пользователь не произносит техническую команду.

```yaml transcript
step:
  id: answer
  input:
    kind: observe
  end: answer.done
```

**Chiplog · answer-reply · пример:** Добавил «Работа над отчётом» на завтра, с 10:00 до 11:00.

**Ожидаемые вызовы, результаты и состояние:**

```yaml transcript
expect:
  step: answer
  baseline: step.start
  calls:
  - id: answer.render
    boundary: owner
    operation: cal.render
    count: 1
    arguments:
      effect: $effect
    result:
      source: real_boundary
      fields:
        assertion: PROVIDER_CONFIRMED
  events:
  - id: answer.done
    operation: cal.step.closed
    count: 1
    after:
    - answer.render.result
  state:
    cal.provider_sends:
      delta: 0
    cal.provider_events:
      eq: 1
    cal.claims_without_evidence:
      eq: 0
  response:
    semantics:
    - Успех основан на закрытом evidence исходного действия.
```

## Повтор команды

Событие среды; пользователь не произносит техническую команду.

```yaml transcript
step:
  id: replay
  input:
    kind: owner_command_replay
    envelope: $accepted_command
    bytes: identical
  end: replay.done
```

**Ожидаемые вызовы, результаты и состояние:**

```yaml transcript
expect:
  step: replay
  baseline: step.start
  calls:
  - id: replay.execute
    boundary: owner
    operation: cal.replay
    count: 1
    envelope:
      eq_input: true
    result:
      source: real_boundary
      fields:
        disposition: REPLAY
  events:
  - id: replay.done
    operation: cal.step.closed
    count: 1
    after:
    - replay.execute.result
  state:
    cal.plan_revisions:
      delta: 0
    cal.effect_intents:
      delta: 0
    cal.provider_sends:
      delta: 0
    cal.claims_without_evidence:
      eq: 0
    cal.transmissions:
      delta: 0
    cal.command_result:
      eq: $publication_result
    cal.effect:
      eq: $effect
    cal.changed_historical_results:
      eq: 0
```

## Изменённая команда с прежним ID

Событие среды; пользователь не произносит техническую команду.

```yaml transcript
step:
  id: conflict
  input:
    kind: owner_command_fault
    envelope: $accepted_command
    replace:
      path: payload.title
      value: Другой отчёт
    preserve:
    - command_id
    - authority
    - owner_allocated_ids
    fault: changed-command
  end: conflict.done
```

**Ожидаемые вызовы, результаты и состояние:**

```yaml transcript
expect:
  step: conflict
  baseline: step.start
  calls:
  - id: conflict.execute
    boundary: owner
    operation: cal.replay
    count: 1
    envelope:
      eq_input: true
    result:
      source: real_boundary
      fields:
        disposition: CONFLICT
    after:
    - conflict.fault
  events:
  - id: conflict.fault
    operation: fault.reached
    count: 1
    fields:
      fault: changed-command
  - id: conflict.done
    operation: cal.step.closed
    count: 1
    after:
    - conflict.execute.result
    - conflict.fault
  state:
    cal.plan_revisions:
      delta: 0
    cal.effect_intents:
      delta: 0
    cal.provider_sends:
      delta: 0
    cal.claims_without_evidence:
      eq: 0
    cal.transmissions:
      delta: 0
    cal.command_result:
      eq: $publication_result
    cal.effect:
      eq: $effect
    cal.changed_historical_results:
      eq: 0
```

## Подтверждение устаревшего предложения

**Пользователь · stale-accept:** Да, добавь.

```yaml transcript
step:
  id: stale
  input:
    kind: authenticated_adoption
    message_ref: stale-accept
    principal: owner
    proposal: $proposal
    display_digest: $display.digest
    fault_before_adoption: stale-head
  end: stale.done
```

Новый display должен отличаться от старого. Proposal может остаться тем же; эта ветка соответствует T04/R18.

**Chiplog · stale-reply · пример:** Планы изменились. Вот актуальное предложение — подтвердите его ещё раз.

**Ожидаемые вызовы, результаты и состояние:**

```yaml transcript
expect:
  step: stale
  baseline: step.start
  relations:
  - left: $fresh_display
    op: not_eq
    right: $display
  calls:
  - id: stale.accept
    boundary: owner
    operation: cal.accept
    count: 1
    arguments:
      command: $command
      display: $display
    result:
      source: real_boundary
      fields:
        disposition: STALE
    after:
    - stale.fault
  - id: stale.redisplay
    boundary: owner
    operation: cal.redisplay
    count: 1
    arguments:
      previous_display: $display
    result:
      source: real_boundary
      capture:
        fresh_display: display
    after:
    - stale.accept.result
  events:
  - id: stale.fault
    operation: fault.reached
    count: 1
    fields:
      fault: stale-head
  - id: stale.done
    operation: cal.step.closed
    count: 1
    after:
    - stale.accept.result
    - stale.redisplay.result
    - stale.fault
  state:
    cal.plan_revisions:
      delta: 0
    cal.effect_intents:
      delta: 0
    cal.provider_sends:
      delta: 0
    cal.claims_without_evidence:
      eq: 0
    cal.current_display:
      eq: $fresh_display
```

## Попытка отправить до принятия

Событие среды; пользователь не произносит техническую команду.

```yaml transcript
step:
  id: premature
  input:
    kind: observe
    fault: preaccept
  end: premature.done
```

**Ожидаемые вызовы, результаты и состояние:**

```yaml transcript
expect:
  step: premature
  baseline: step.start
  calls:
  - id: premature.dispatch
    boundary: owner
    operation: cal.dispatch
    count: 1
    arguments:
      unaccepted_command: $command
    result:
      source: real_boundary
      fields:
        disposition: REJECTED
    after:
    - premature.fault
  events:
  - id: premature.fault
    operation: fault.reached
    count: 1
    fields:
      fault: preaccept
  - id: premature.done
    operation: cal.step.closed
    count: 1
    after:
    - premature.dispatch.result
    - premature.fault
  state:
    cal.plan_revisions:
      delta: 0
    cal.effect_intents:
      delta: 0
    cal.provider_sends:
      delta: 0
    cal.claims_without_evidence:
      eq: 0
```

## Сбой до commit

**Пользователь · crash-before-accept:** Да, добавь.

```yaml transcript
step:
  id: crash-before
  input:
    kind: authenticated_adoption
    message_ref: crash-before-accept
    principal: owner
    proposal: $proposal
    display_digest: $display.digest
    fault: publication-before
    capture:
      crash_command: authenticated_command
  end: crash-before.done
```

Fault hook исполняет настоящий adoption/publication путь и обрывает процесс у указанной границы. Нет фикстурного успешного результата владельца.

**Ожидаемые вызовы, результаты и состояние:**

```yaml transcript
expect:
  step: crash-before
  baseline: step.start
  calls: []
  events:
  - id: crash-before.fault
    operation: fault.reached
    count: 1
    fields:
      fault: publication-before
  - id: crash-before.done
    operation: cal.step.closed
    count: 1
    after:
    - crash-before.fault
  state:
    cal.atomic_batch:
      eq: NONE
    cal.plan_revisions:
      delta: 0
    cal.effect_intents:
      delta: 0
    cal.provider_sends:
      delta: 0
```

## Открыть то же хранилище и повторить команду

Событие среды; пользователь не произносит техническую команду.

```yaml transcript
step:
  id: recover-first
  input:
    kind: owner_command_replay
    envelope: $crash_command
    bytes: identical
  end: recover-first.done
```

**Ожидаемые вызовы, результаты и состояние:**

```yaml transcript
expect:
  step: recover-first
  baseline: step.start
  calls:
  - id: recover-first.open
    boundary: owner
    operation: cal.restart
    count: 1
    arguments:
      store: same_durable_store
      worker_ticks: 0
    result:
      source: real_boundary
  - id: recover-first.execute
    boundary: owner
    operation: cal.replay
    count: 1
    envelope:
      eq_input: true
    result:
      source: real_boundary
      fields:
        disposition: COMMITTED
    after:
    - recover-first.open.result
  events:
  - id: recover-first.done
    operation: cal.step.closed
    count: 1
    after:
    - recover-first.open.result
    - recover-first.execute.result
  state:
    cal.atomic_batch:
      eq: COMPLETE
    cal.plan_revisions:
      eq: 1
    cal.effect_intents:
      eq: 1
    cal.provider_sends:
      eq: 0
    cal.changed_historical_results:
      eq: 0
```

## Сбой после commit

**Пользователь · crash-after-accept:** Да, добавь.

```yaml transcript
step:
  id: crash-after
  input:
    kind: authenticated_adoption
    message_ref: crash-after-accept
    principal: owner
    proposal: $proposal
    display_digest: $display.digest
    fault: publication-after
    capture:
      crash_command: authenticated_command
  end: crash-after.done
```

Fault hook исполняет настоящий adoption/publication путь и обрывает процесс у указанной границы. Нет фикстурного успешного результата владельца.

**Ожидаемые вызовы, результаты и состояние:**

```yaml transcript
expect:
  step: crash-after
  baseline: step.start
  calls: []
  events:
  - id: crash-after.fault
    operation: fault.reached
    count: 1
    fields:
      fault: publication-after
  - id: crash-after.done
    operation: cal.step.closed
    count: 1
    after:
    - crash-after.fault
  state:
    cal.atomic_batch:
      eq: COMPLETE
    cal.plan_revisions:
      delta: 1
    cal.effect_intents:
      delta: 1
    cal.provider_sends:
      delta: 0
```

## Открыть то же хранилище и повторить команду

Событие среды; пользователь не произносит техническую команду.

```yaml transcript
step:
  id: recover-replay
  input:
    kind: owner_command_replay
    envelope: $crash_command
    bytes: identical
  end: recover-replay.done
```

**Ожидаемые вызовы, результаты и состояние:**

```yaml transcript
expect:
  step: recover-replay
  baseline: step.start
  calls:
  - id: recover-replay.open
    boundary: owner
    operation: cal.restart
    count: 1
    arguments:
      store: same_durable_store
      worker_ticks: 0
    result:
      source: real_boundary
  - id: recover-replay.execute
    boundary: owner
    operation: cal.replay
    count: 1
    envelope:
      eq_input: true
    result:
      source: real_boundary
      fields:
        disposition: REPLAY
    after:
    - recover-replay.open.result
  events:
  - id: recover-replay.done
    operation: cal.step.closed
    count: 1
    after:
    - recover-replay.open.result
    - recover-replay.execute.result
  state:
    cal.atomic_batch:
      eq: COMPLETE
    cal.plan_revisions:
      eq: 1
    cal.effect_intents:
      eq: 1
    cal.provider_sends:
      eq: 0
    cal.changed_historical_results:
      eq: 0
```

## Изменить полномочия до отправки

Событие среды; пользователь не произносит техническую команду.

```yaml transcript
step:
  id: invalidate-before
  input:
    kind: observe
    fault: invalidate-before
  end: invalidate-before.done
```

**Ожидаемые вызовы, результаты и состояние:**

```yaml transcript
expect:
  step: invalidate-before
  baseline: step.start
  calls:
  - id: invalidate-before.dispatch
    boundary: owner
    operation: cal.dispatch
    count: 1
    arguments:
      effect: $effect
    result:
      source: real_boundary
      fields:
        disposition: REJECTED
    after:
    - invalidate-before.fault
  events:
  - id: invalidate-before.fault
    operation: fault.reached
    count: 1
    fields:
      fault: invalidate-before
  - id: invalidate-before.done
    operation: cal.step.closed
    count: 1
    after:
    - invalidate-before.dispatch.result
    - invalidate-before.fault
  state:
    cal.provider_sends:
      delta: 0
    cal.effect:
      eq: $effect
    cal.transmissions:
      eq: 0
    cal.provider_events:
      eq: 0
    cal.changed_historical_results:
      eq: 0
```

## Изменить полномочия после отправки

Событие среды; пользователь не произносит техническую команду.

```yaml transcript
step:
  id: invalidate-after
  input:
    kind: observe
    fault: invalidate-after
  end: invalidate-after.done
```

**Ожидаемые вызовы, результаты и состояние:**

```yaml transcript
expect:
  step: invalidate-after
  baseline: step.start
  calls: []
  events:
  - id: invalidate-after.fault
    operation: fault.reached
    count: 1
    fields:
      fault: invalidate-after
  - id: invalidate-after.done
    operation: cal.step.closed
    count: 1
    after:
    - invalidate-after.fault
  state:
    cal.provider_sends:
      delta: 0
    cal.effect:
      eq: $effect
    cal.transmissions:
      eq: 1
    cal.provider_events:
      eq: 1
    cal.changed_historical_results:
      eq: 0
```

## Запреты

```yaml transcript
forbid:
  use: drafts/r14-r17/common.md#/forbid
```
