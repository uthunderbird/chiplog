# Ответ календаря потерян

**Статус: design · format v2 · stateful v2.** Ожидаемый сценарий, не запись исполнения.
[Формат](../TRANSCRIPTS.md) · [План](../R14-R17-TRANSCRIPT-PLAN.md) · [Bindings и наблюдения](drafts/r14-r17/common.md).

> **User story.** Понять, что произошло после сбоя, и не создать повторное событие.

Все `cal.*` — проектируемые mappings, не готовые API. Без runtime bindings каждый вариант — `NOT_RUNNABLE`; compile PASS не доказывает поведение. Реплики Chiplog — примеры.

```yaml transcript
scenario:
  format_version: 2
  protocol_version: 2
  id: unknown-calendar-outcome
  version: 3
  status: design
  arrange:
    use: drafts/r14-r17/common.md#/scenario/arrange
  initial_context:
    use: drafts/r14-r17/common.md#/scenario/initial_context
  policy:
    use: drafts/r14-r17/common.md#/scenario/policy
  fixtures:
  - id: lost-fixture
    boundary: cal.provider
    match:
      effect: $prefix.effect
    result:
      kind: LOST_RESPONSE_AFTER_EFFECT
      response_received: false
    steps:
    - lost
  - id: opaque-fixture
    boundary: cal.provider
    match:
      effect: $prefix.effect
    result:
      kind: OPAQUE_TRANSMISSION
      response_received: false
    steps:
    - opaque
  - id: lookup-unavailable
    boundary: cal.reconcile
    match:
      effect: $prefix.effect
    result:
      availability: UNAVAILABLE
      outcome: UNKNOWN
    steps:
    - lookup
  - id: late-confirm
    boundary: cal.evidence
    match:
      transmission: $transmission
      effect: $prefix.effect
    result:
      source: independently_authenticated_provider
      outcome: CONFIRMED
      provider_event_id: cal-417
    steps:
    - late
  requirements:
    full_envelope_validation: true
    independent_provider_log: true
    no_oracle_in_application_context: true
    production_loop_integration: R18
    all_step_cutoffs_must_be_reached: true
    prefix_excludes_send: true
    oracle_not_used_as_application_evidence: true
  variants:
    unknown-held:
      steps:
      - lost
      - inquire
      - lookup
      - drain
      allowed_outcome: unknown-held
      requires:
      - stateful_runtime
      - calendar_payload_mapping
      - R16_broker_dispatch
      - R18_calendar_loop
      prefix:
        source: calendar-proposal-confirmation.md
        variant: confirmed
        until: adopt.commit
        capture_namespace: prefix
        inherit_current: true
        inherit_context: true
        fresh_run: true
    opaque-transmission:
      steps:
      - opaque
      - inquire
      - lookup
      - drain
      allowed_outcome: opaque-transmission
      requires:
      - stateful_runtime
      - calendar_payload_mapping
      - R16_broker_dispatch
      - R18_calendar_loop
      prefix:
        source: calendar-proposal-confirmation.md
        variant: confirmed
        until: adopt.commit
        capture_namespace: prefix
        inherit_current: true
        inherit_context: true
        fresh_run: true
    restart-held:
      steps:
      - lost
      - restart
      - inquire
      - lookup
      - drain
      allowed_outcome: restart-held
      requires:
      - stateful_runtime
      - calendar_payload_mapping
      - R16_broker_dispatch
      - R18_calendar_loop
      prefix:
        source: calendar-proposal-confirmation.md
        variant: confirmed
        until: adopt.commit
        capture_namespace: prefix
        inherit_current: true
        inherit_context: true
        fresh_run: true
    takeover-held:
      steps:
      - lost
      - takeover
      - drain
      allowed_outcome: takeover-held
      requires:
      - stateful_runtime
      - calendar_payload_mapping
      - R16_broker_dispatch
      - R18_calendar_loop
      prefix:
        source: calendar-proposal-confirmation.md
        variant: confirmed
        until: adopt.commit
        capture_namespace: prefix
        inherit_current: true
        inherit_context: true
        fresh_run: true
    late-confirmation:
      steps:
      - lost
      - late
      - close
      - answer
      allowed_outcome: late-confirmation
      requires:
      - stateful_runtime
      - calendar_payload_mapping
      - R16_broker_dispatch
      - R18_calendar_loop
      prefix:
        source: calendar-proposal-confirmation.md
        variant: confirmed
        until: adopt.commit
        capture_namespace: prefix
        inherit_current: true
        inherit_context: true
        fresh_run: true
    conflict-before-closure:
      steps:
      - lost
      - late
      - conflict-before
      - held
      allowed_outcome: conflict-before-closure
      requires:
      - stateful_runtime
      - calendar_payload_mapping
      - R16_broker_dispatch
      - R18_calendar_loop
      prefix:
        source: calendar-proposal-confirmation.md
        variant: confirmed
        until: adopt.commit
        capture_namespace: prefix
        inherit_current: true
        inherit_context: true
        fresh_run: true
    conflict-after-closure:
      steps:
      - lost
      - late
      - close
      - conflict-after
      - held
      allowed_outcome: conflict-after-closure
      requires:
      - stateful_runtime
      - calendar_payload_mapping
      - R16_broker_dispatch
      - R18_calendar_loop
      prefix:
        source: calendar-proposal-confirmation.md
        variant: confirmed
        until: adopt.commit
        capture_namespace: prefix
        inherit_current: true
        inherit_context: true
        fresh_run: true
    wrong-correlation:
      steps:
      - lost
      - wrong
      - drain
      allowed_outcome: wrong-correlation
      requires:
      - stateful_runtime
      - calendar_payload_mapping
      - R16_broker_dispatch
      - R18_calendar_loop
      prefix:
        source: calendar-proposal-confirmation.md
        variant: confirmed
        until: adopt.commit
        capture_namespace: prefix
        inherit_current: true
        inherit_context: true
        fresh_run: true
    exact-replay:
      steps:
      - lost
      - replay
      - drain
      allowed_outcome: exact-replay
      requires:
      - stateful_runtime
      - calendar_payload_mapping
      - R16_broker_dispatch
      - R18_calendar_loop
      prefix:
        source: calendar-proposal-confirmation.md
        variant: confirmed
        until: adopt.commit
        capture_namespace: prefix
        inherit_current: true
        inherit_context: true
        fresh_run: true
    changed-replay:
      steps:
      - lost
      - changed
      - drain
      allowed_outcome: changed-replay
      requires:
      - stateful_runtime
      - calendar_payload_mapping
      - R16_broker_dispatch
      - R18_calendar_loop
      prefix:
        source: calendar-proposal-confirmation.md
        variant: confirmed
        until: adopt.commit
        capture_namespace: prefix
        inherit_current: true
        inherit_context: true
        fresh_run: true
    replacement:
      steps:
      - lost
      - replacement
      - drain
      allowed_outcome: replacement
      requires:
      - stateful_runtime
      - calendar_payload_mapping
      - R16_broker_dispatch
      - R18_calendar_loop
      prefix:
        source: calendar-proposal-confirmation.md
        variant: confirmed
        until: adopt.commit
        capture_namespace: prefix
        inherit_current: true
        inherit_context: true
        fresh_run: true
  faults:
  - id: lost-response
    boundary: provider.after_effect_before_response
    count: 1
    require_reached: true
    change:
      response: lost
      observer_effect_knowledge: ONE
  - id: opaque-response
    boundary: provider.after_possible_transmission_before_observation
    count: 1
    require_reached: true
    change:
      response: lost
      observer_effect_knowledge: UNKNOWN
  - id: restart
    boundary: recovery.before_worker_activation
    count: 1
    require_reached: true
    change:
      process: restart_same_store
  - id: takeover
    boundary: recovery.before_worker_activation
    count: 1
    require_reached: true
    change:
      process: expire_takeover_then_stale_submit
  - id: conflict-before
    boundary: recovery.semantic_reduction
    count: 1
    require_reached: true
    change:
      witness: authenticated_incompatible_evidence
  - id: conflict-after
    boundary: recovery.semantic_reduction
    count: 1
    require_reached: true
    change:
      witness: authenticated_incompatible_evidence
  - id: wrong
    boundary: evidence.authentication
    count: 1
    require_reached: true
    change:
      witness: wrong_attempt
  - id: changed
    boundary: broker.replay
    count: 1
    require_reached: true
    change:
      mutation: same_command_changed_payload
  - id: replacement
    boundary: effects.replacement_guard
    count: 1
    require_reached: true
    change:
      mutation: new_identity_same_action
```

## Потерять ответ после выполненного действия

Событие среды; пользователь не произносит техническую команду.

```yaml transcript
step:
  id: lost
  input:
    kind: observe
    fault: lost-response
  end: lost.done
```

Observer журнала fake-provider изолирован от приложения. Его запись доказывает выполненный эффект, но не даёт reducer подтверждения.

**Ожидаемые вызовы, результаты и состояние:**

```yaml transcript
expect:
  step: lost
  baseline: step.start
  calls:
  - id: lost.dispatch
    boundary: provider
    operation: cal.dispatch
    count: 1
    arguments:
      effect: $prefix.effect
    result:
      fixture: lost-fixture
    after:
    - lost.send-committed
  events:
  - id: lost.send-committed
    operation: cal.send_committed
    count: 1
    capture:
      transmission: transmission
  - id: lost.fault
    operation: fault.reached
    count: 1
    after:
    - lost.send-committed
    fields:
      fault: lost-response
  - id: lost.unknown
    operation: cal.outcome.unknown
    count: 1
    after:
    - lost.dispatch.result
    - lost.fault
    fields:
      effect: $prefix.effect
    capture:
      obligation: obligation
  - id: lost.done
    operation: cal.step.closed
    count: 1
    after:
    - lost.dispatch.result
    - lost.send-committed
    - lost.fault
    - lost.unknown
  state:
    cal.transmissions:
      delta: 1
    cal.outcome:
      eq: UNKNOWN
    cal.obligations_open:
      eq: 1
    cal.continuation_ready:
      eq: false
    cal.effect:
      eq: $prefix.effect
    cal.provider_sends:
      delta: 1
    cal.provider_events:
      delta: 1
```

## Потерять ответ после возможной передачи

Событие среды; пользователь не произносит техническую команду.

```yaml transcript
step:
  id: opaque
  input:
    kind: observe
    fault: opaque-response
  end: opaque.done
```

Observer журнала fake-provider изолирован от приложения. Число внешних событий неизвестно. Не проверяем provider_events=0 или 1. Независимый монитор последней границы фиксирует ровно одну исходящую попытку в этом окне, включая повторные bytes с той же identity. Он не доказывает доставку или эффект. Последующие окна запрещают любые дополнительные отправки.

**Ожидаемые вызовы, результаты и состояние:**

```yaml transcript
expect:
  step: opaque
  baseline: step.start
  calls:
  - id: opaque.dispatch
    boundary: provider
    operation: cal.dispatch
    count: 1
    arguments:
      effect: $prefix.effect
    result:
      fixture: opaque-fixture
    after:
    - opaque.send-committed
  events:
  - id: opaque.send-committed
    operation: cal.send_committed
    count: 1
    capture:
      transmission: transmission
  - id: opaque.fault
    operation: fault.reached
    count: 1
    after:
    - opaque.send-committed
    fields:
      fault: opaque-response
  - id: opaque.unknown
    operation: cal.outcome.unknown
    count: 1
    after:
    - opaque.dispatch.result
    - opaque.fault
    fields:
      effect: $prefix.effect
    capture:
      obligation: obligation
  - id: opaque.done
    operation: cal.step.closed
    count: 1
    after:
    - opaque.dispatch.result
    - opaque.send-committed
    - opaque.fault
    - opaque.unknown
  state:
    cal.transmissions:
      delta: 1
    cal.outcome:
      eq: UNKNOWN
    cal.obligations_open:
      eq: 1
    cal.continuation_ready:
      eq: false
    cal.effect:
      eq: $prefix.effect
    cal.provider_sends:
      delta: 1
```

## Уточнить результат

**Пользователь · inquire:** Получилось добавить?

```yaml transcript
step:
  id: inquire
  input:
    kind: message
    message_ref: inquire
  end: inquire.done
```

**Chiplog · inquire-reply · пример:** Календарь не ответил. Пока не могу подтвердить, что событие создалось. Ещё раз отправлять запрос не буду, чтобы не сделать дубль.

**Ожидаемые вызовы, результаты и состояние:**

```yaml transcript
expect:
  step: inquire
  baseline: step.start
  calls: []
  events:
  - id: inquire.answer
    operation: cal.status.accepted
    count: 1
    fields:
      outcome: UNKNOWN
      effect: $prefix.effect
  - id: inquire.done
    operation: cal.step.closed
    count: 1
    after:
    - inquire.answer
  state:
    cal.outcome:
      eq: UNKNOWN
    cal.provider_sends:
      delta: 0
    cal.replacement_attempts:
      eq: 0
    cal.claims_without_evidence:
      eq: 0
  response:
    semantics:
    - Неизвестность сохранена; не обещать успешную фоновую проверку.
```

## Сверить исходное действие

Событие среды; пользователь не произносит техническую команду.

```yaml transcript
step:
  id: lookup
  input:
    kind: observe
  end: lookup.done
```

**Ожидаемые вызовы, результаты и состояние:**

```yaml transcript
expect:
  step: lookup
  baseline: step.start
  calls:
  - id: lookup.read
    boundary: provider
    operation: cal.reconcile
    count: 1
    arguments:
      effect: $prefix.effect
    result:
      fixture: lookup-unavailable
  events:
  - id: lookup.done
    operation: cal.step.closed
    count: 1
    after:
    - lookup.read.result
  state:
    cal.outcome:
      eq: UNKNOWN
    cal.obligations_open:
      eq: 1
    cal.obligation_identity:
      eq: $obligation
    cal.provider_sends:
      delta: 0
    cal.replacement_attempts:
      eq: 0
```

## Перезапуск

Событие среды; пользователь не произносит техническую команду.

```yaml transcript
step:
  id: restart
  input:
    kind: resume
    fault: restart
  end: restart.done
```

**Ожидаемые вызовы, результаты и состояние:**

```yaml transcript
expect:
  step: restart
  baseline: step.start
  calls:
  - id: restart.resume
    boundary: owner
    operation: cal.restart
    count: 1
    arguments:
      store: same_durable_store
      original_effect: $prefix.effect
      worker_ticks: 4
    result:
      source: real_boundary
      fields:
        original_obligation: $obligation
        budget_reset: false
    after:
    - restart.fault
  events:
  - id: restart.fault
    operation: fault.reached
    count: 1
    fields:
      fault: restart
  - id: restart.done
    operation: cal.step.closed
    count: 1
    after:
    - restart.resume.result
    - restart.fault
  state:
    cal.provider_sends:
      delta: 0
    cal.transmissions:
      delta: 0
    cal.effect:
      eq: $prefix.effect
    cal.obligation_identity:
      eq: $obligation
    cal.outcome:
      eq: UNKNOWN
    cal.replacement_attempts:
      eq: 0
```

## Смена исполнителя и возврат старого

Событие среды; пользователь не произносит техническую команду.

```yaml transcript
step:
  id: takeover
  input:
    kind: resume
    fault: takeover
  end: takeover.done
```

**Ожидаемые вызовы, результаты и состояние:**

```yaml transcript
expect:
  step: takeover
  baseline: step.start
  calls:
  - id: takeover.resume
    boundary: owner
    operation: cal.takeover
    count: 1
    arguments:
      store: same_durable_store
      original_effect: $prefix.effect
      worker_ticks: 4
    result:
      source: real_boundary
      fields:
        original_obligation: $obligation
        budget_reset: false
    after:
    - takeover.fault
  events:
  - id: takeover.fault
    operation: fault.reached
    count: 1
    fields:
      fault: takeover
  - id: takeover.stale
    operation: cal.stale_worker.rejected
    count: 1
    after:
    - takeover.resume.result
    fields:
      new_publications: 0
  - id: takeover.done
    operation: cal.step.closed
    count: 1
    after:
    - takeover.resume.result
    - takeover.fault
    - takeover.stale
  state:
    cal.provider_sends:
      delta: 0
    cal.transmissions:
      delta: 0
    cal.effect:
      eq: $prefix.effect
    cal.obligation_identity:
      eq: $obligation
    cal.outcome:
      eq: UNKNOWN
    cal.replacement_attempts:
      eq: 0
```

## Закончить ограниченное окно наблюдения

Событие среды; пользователь не произносит техническую команду.

```yaml transcript
step:
  id: drain
  input:
    kind: observe
  end: drain.done
```

Это конечный trace из четырёх ticks, не утверждение о вечном отсутствии retry. Clock и общий бюджет из common включают prefix и все предыдущие шаги; лимит worker ticks применяется к каждому указанному bounded drain.

**Ожидаемые вызовы, результаты и состояние:**

```yaml transcript
expect:
  step: drain
  baseline: step.start
  calls:
  - id: drain.resume
    boundary: owner
    operation: cal.continuation
    count: 1
    arguments:
      worker_ticks: 4
      clock_advance_seconds: 4
      reset_budget: false
    result:
      source: real_boundary
      fields:
        disposition: HOLD
  events:
  - id: drain.done
    operation: cal.step.closed
    count: 1
    after:
    - drain.resume.result
  state:
    cal.provider_sends:
      delta: 0
    cal.transmissions:
      delta: 0
    cal.replacement_attempts:
      eq: 0
    cal.obligations_open:
      eq: 1
    cal.obligation_identity:
      eq: $obligation
    cal.claims_without_evidence:
      eq: 0
    cal.outcome:
      eq: UNKNOWN
```

## Получить позднее свидетельство

Событие среды; пользователь не произносит техническую команду.

```yaml transcript
step:
  id: late
  input:
    kind: observe
  end: late.done
```

Fixture — только внешние bytes; durable evidence event выдаёт настоящий ingress. Наличие raw evidence не закрывает obligation.

**Ожидаемые вызовы, результаты и состояние:**

```yaml transcript
expect:
  step: late
  baseline: step.start
  calls:
  - id: late.ingest
    boundary: provider
    operation: cal.evidence
    count: 1
    arguments:
      transmission: $transmission
      effect: $prefix.effect
    result:
      fixture: late-confirm
  events:
  - id: late.durable
    operation: cal.raw_evidence.committed
    count: 1
    after:
    - late.ingest.result
    capture:
      witness: witness
  - id: late.done
    operation: cal.step.closed
    count: 1
    after:
    - late.ingest.result
    - late.durable
  state:
    cal.continuation_ready:
      eq: false
    cal.obligations_open:
      eq: 1
    cal.provider_sends:
      delta: 0
```

## Закрыть исходное обязательство

Событие среды; пользователь не произносит техническую команду.

```yaml transcript
step:
  id: close
  input:
    kind: observe
  end: close.done
```

**Ожидаемые вызовы, результаты и состояние:**

```yaml transcript
expect:
  step: close
  baseline: step.start
  calls:
  - id: close.resolve
    boundary: owner
    operation: cal.reconcile
    count: 1
    arguments:
      obligation: $obligation
      witness: $witness
      transmission: $transmission
    result:
      source: real_boundary
      fields:
        closure: CONFIRMED
      capture:
        closure: closure
        reduction: current_reduction
  events:
  - id: close.done
    operation: cal.step.closed
    count: 1
    after:
    - close.resolve.result
  state:
    cal.obligations_open:
      eq: 0
    cal.outcome:
      eq: CONFIRMED
    cal.continuation_ready:
      eq: true
    cal.provider_sends:
      delta: 0
    cal.effect:
      eq: $prefix.effect
```

## Сообщить подтверждённый исход

Событие среды; пользователь не произносит техническую команду.

```yaml transcript
step:
  id: answer
  input:
    kind: observe
  end: answer.done
```

**Chiplog · answer-reply · пример:** Да, событие есть в календаре: завтра с 10:00 до 11:00.

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
      effect: $prefix.effect
      closure: $closure
      reduction: $reduction
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
    cal.claims_without_evidence:
      eq: 0
  response:
    semantics:
    - Подтверждение относится к исходному действию и закрытому evidence.
```

## Противоречивые сведения до закрытия

Событие среды; пользователь не произносит техническую команду.

```yaml transcript
step:
  id: conflict-before
  input:
    kind: observe
    fault: conflict-before
  end: conflict-before.done
```

Mutation на реальной границе с reached witness; это не отказ YAML/parser. Историческое closure после conflict-after сохраняется, блокируется дальнейшее потребление.

**Ожидаемые вызовы, результаты и состояние:**

```yaml transcript
expect:
  step: conflict-before
  baseline: step.start
  calls:
  - id: conflict-before.ingest
    boundary: owner
    operation: cal.evidence
    count: 1
    arguments:
      original_transmission: $transmission
      input_fixture: conflict-before
    result:
      source: real_boundary
      fields:
        disposition: HOLD
    after:
    - conflict-before.fault
  events:
  - id: conflict-before.fault
    operation: fault.reached
    count: 1
    fields:
      fault: conflict-before
  - id: conflict-before.done
    operation: cal.step.closed
    count: 1
    after:
    - conflict-before.ingest.result
    - conflict-before.fault
  state:
    cal.provider_sends:
      delta: 0
    cal.changed_historical_results:
      eq: 0
    cal.claims_without_evidence:
      eq: 0
    cal.continuation_ready:
      eq: false
```

## Противоречивые сведения после закрытия

Событие среды; пользователь не произносит техническую команду.

```yaml transcript
step:
  id: conflict-after
  input:
    kind: observe
    fault: conflict-after
  end: conflict-after.done
```

Mutation на реальной границе с reached witness; это не отказ YAML/parser. Историческое closure после conflict-after сохраняется, блокируется дальнейшее потребление.

**Ожидаемые вызовы, результаты и состояние:**

```yaml transcript
expect:
  step: conflict-after
  baseline: step.start
  calls:
  - id: conflict-after.ingest
    boundary: owner
    operation: cal.evidence
    count: 1
    arguments:
      original_transmission: $transmission
      input_fixture: conflict-after
    result:
      source: real_boundary
      fields:
        disposition: HOLD
    after:
    - conflict-after.fault
  events:
  - id: conflict-after.fault
    operation: fault.reached
    count: 1
    fields:
      fault: conflict-after
  - id: conflict-after.done
    operation: cal.step.closed
    count: 1
    after:
    - conflict-after.ingest.result
    - conflict-after.fault
  state:
    cal.provider_sends:
      delta: 0
    cal.changed_historical_results:
      eq: 0
    cal.claims_without_evidence:
      eq: 0
    cal.continuation_ready:
      eq: false
```

## Неверная корреляция

Событие среды; пользователь не произносит техническую команду.

```yaml transcript
step:
  id: wrong
  input:
    kind: observe
    fault: wrong
  end: wrong.done
```

Mutation на реальной границе с reached witness; это не отказ YAML/parser. Историческое closure после conflict-after сохраняется, блокируется дальнейшее потребление.

**Ожидаемые вызовы, результаты и состояние:**

```yaml transcript
expect:
  step: wrong
  baseline: step.start
  calls:
  - id: wrong.ingest
    boundary: owner
    operation: cal.evidence
    count: 1
    arguments:
      original_transmission: $transmission
      input_fixture: wrong
    result:
      source: real_boundary
      fields:
        disposition: REJECTED
    after:
    - wrong.fault
  events:
  - id: wrong.fault
    operation: fault.reached
    count: 1
    fields:
      fault: wrong
  - id: wrong.done
    operation: cal.step.closed
    count: 1
    after:
    - wrong.ingest.result
    - wrong.fault
  state:
    cal.provider_sends:
      delta: 0
    cal.changed_historical_results:
      eq: 0
    cal.claims_without_evidence:
      eq: 0
    cal.obligations_open:
      eq: 1
    cal.obligation_identity:
      eq: $obligation
    cal.outcome:
      eq: UNKNOWN
```

## Проверить запрет дальнейшего продолжения

Событие среды; пользователь не произносит техническую команду.

```yaml transcript
step:
  id: held
  input:
    kind: observe
  end: held.done
```

**Ожидаемые вызовы, результаты и состояние:**

```yaml transcript
expect:
  step: held
  baseline: step.start
  calls:
  - id: held.check
    boundary: owner
    operation: cal.continuation
    count: 1
    arguments:
      effect: $prefix.effect
      worker_ticks: 4
    result:
      source: real_boundary
      fields:
        disposition: HOLD
  events:
  - id: held.done
    operation: cal.step.closed
    count: 1
    after:
    - held.check.result
  state:
    cal.continuation_ready:
      eq: false
    cal.provider_sends:
      delta: 0
    cal.changed_historical_results:
      eq: 0
```

## Повтор принятой команды

Событие среды; пользователь не произносит техническую команду.

```yaml transcript
step:
  id: replay
  input:
    kind: owner_command_replay
    envelope: $prefix.accepted_command
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
        result: $prefix.publication_result
  events:
  - id: replay.done
    operation: cal.step.closed
    count: 1
    after:
    - replay.execute.result
  state:
    cal.provider_sends:
      delta: 0
    cal.transmissions:
      delta: 0
    cal.effect_intents:
      delta: 0
    cal.effect:
      eq: $prefix.effect
    cal.obligation_identity:
      eq: $obligation
    cal.changed_historical_results:
      eq: 0
    cal.replacement_attempts:
      eq: 0
    cal.outcome:
      eq: UNKNOWN
    cal.command_result:
      eq: $prefix.publication_result
```

## Тот же ID с другим содержимым

Событие среды; пользователь не произносит техническую команду.

```yaml transcript
step:
  id: changed
  input:
    kind: owner_command_fault
    envelope: $prefix.accepted_command
    fault: changed
    replace:
      path: payload.title
      value: replacement-fixture
    preserve:
    - original_action
    - authority
  end: changed.done
```

**Ожидаемые вызовы, результаты и состояние:**

```yaml transcript
expect:
  step: changed
  baseline: step.start
  calls:
  - id: changed.execute
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
    - changed.fault
  events:
  - id: changed.fault
    operation: fault.reached
    count: 1
    fields:
      fault: changed
  - id: changed.done
    operation: cal.step.closed
    count: 1
    after:
    - changed.execute.result
    - changed.fault
  state:
    cal.provider_sends:
      delta: 0
    cal.transmissions:
      delta: 0
    cal.effect_intents:
      delta: 0
    cal.effect:
      eq: $prefix.effect
    cal.obligation_identity:
      eq: $obligation
    cal.changed_historical_results:
      eq: 0
    cal.replacement_attempts:
      eq: 0
    cal.outcome:
      eq: UNKNOWN
```

## Попытка заменить неизвестное действие

Событие среды; пользователь не произносит техническую команду.

```yaml transcript
step:
  id: replacement
  input:
    kind: owner_command_fault
    envelope: $prefix.accepted_command
    fault: replacement
    replace:
      path: command_id
      value: replacement-fixture
    preserve:
    - original_action
    - authority
  end: replacement.done
```

**Ожидаемые вызовы, результаты и состояние:**

```yaml transcript
expect:
  step: replacement
  baseline: step.start
  calls:
  - id: replacement.execute
    boundary: owner
    operation: cal.replacement
    count: 1
    envelope:
      eq_input: true
    result:
      source: real_boundary
      fields:
        disposition: REJECTED
    after:
    - replacement.fault
  events:
  - id: replacement.fault
    operation: fault.reached
    count: 1
    fields:
      fault: replacement
  - id: replacement.done
    operation: cal.step.closed
    count: 1
    after:
    - replacement.execute.result
    - replacement.fault
  state:
    cal.provider_sends:
      delta: 0
    cal.transmissions:
      delta: 0
    cal.effect_intents:
      delta: 0
    cal.effect:
      eq: $prefix.effect
    cal.obligation_identity:
      eq: $obligation
    cal.changed_historical_results:
      eq: 0
    cal.replacement_attempts:
      eq: 0
    cal.outcome:
      eq: UNKNOWN
```

## Запреты

```yaml transcript
forbid:
  use: drafts/r14-r17/common.md#/forbid
```
