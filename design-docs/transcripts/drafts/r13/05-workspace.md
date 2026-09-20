# Согласованные экраны и ограничения раскрытия

**Статус: design · format v2 · stateful profile v2.** Ожидаемый сценарий, не запись прогона.
[Основной формат](../../../TRANSCRIPTS.md) · [Stateful-профиль](STATEFUL-PROTOCOL.md) · [Общие данные](common.md).

**Связь с текущим кодом:** Символические snapshots/frontier/labels ещё не связаны с DTO и публичными observer paths. `$prefix.result.id` требует замены на реальный receipt.evidence_id при подключении runtime.

> **User story.** Добавить отчёт в планы, не принимая старые данные за актуальные и не раскрывая личное посторонним.

**Сценарий, независимые варианты, prefix и fault fixtures:**

```yaml transcript
scenario:
  format_version: 2
  protocol_version: 2
  id: r12-r13-workspace-contour
  version: 4
  status: design
  arrange:
    use: common.md#/scenario/arrange
  initial_context:
    use: common.md#/scenario/initial_context
  policy:
    use: common.md#/scenario/policy
  fixtures:
  - use: common.md#/scenario/fixtures/history-quarterly
  - id: local-complete
    boundary: model
    match:
      after: prefix.accept.commit
      attempt: 1
    result:
      kind: Complete
      deliveries:
      - kind: DeliveryAssertion
        assertion_code: LOCAL_PLANNING_COMMITTED
        evidence_id: $prefix.result.id
    steps:
    - narrowed-completion
  faults:
  - id: mixed-frontier
    boundary: workspace.before_batch_acceptance
    replace:
      family: journal
      field: frontier
      from: F1
      to: F2
    other_internal_families: F1
    count: 1
    require_reached: true
  - id: wrong-screen-revision
    boundary: model.before_context_emission
    after: prefix.request.display
    replace:
      screen: planning
      snapshot: $prefix.proposal_screen
      revision:
        different_from: $prefix.proposal_screen.revision
    count: 1
    require_reached: true
  - id: narrowed-display
    boundary: proposal.before_display_acceptance
    after: prefix.request.history.result
    replace:
      field: disclosure_label
      from: owner-private
      to: broader-than-owner-private
    derivation_authority: null
    count: 1
    require_reached: true
  - id: narrowed-completion
    boundary: completion.before_acceptance
    after: prefix.accept.commit
    replace:
      field: disclosure_label
      from: owner-private
      to: broader-than-owner-private
    derivation_authority: null
    count: 1
    require_reached: true
  requirements:
    label_binding: strictly_broader_recipient_set_without_authority
    component_scope: R12_workspace_ports_only
    end_to_end_scope: R13_production_loop
    no_success_from_unreached_fault: true
  variants:
    external-lag-control:
      prefix:
        source: 01-intention-from-history.md
        variant: quarterly
        until: accept.complete
        capture_namespace: prefix
        inherit_current: true
        inherit_context: true
        fresh_run: true
      steps:
      - external-lag
      allowed_outcome: local_receipt_with_stale_calendar
      parameters:
        history_fixture: common.md#/scenario/fixtures/history-quarterly
    mixed-frontier:
      steps:
      - mixed-frontier
      allowed_outcome: workspace_rejection_before_proposal
      parameters:
        history_fixture: common.md#/scenario/fixtures/history-quarterly
    wrong-screen-revision:
      prefix:
        source: 01-intention-from-history.md
        variant: quarterly
        until: request.display
        capture_namespace: prefix
        inherit_current: true
        inherit_context: true
        fresh_run: true
      steps:
      - wrong-screen-revision
      allowed_outcome: visibility_rejection_before_model_emission
      parameters:
        history_fixture: common.md#/scenario/fixtures/history-quarterly
    narrowed-label-before-display:
      prefix:
        source: 01-intention-from-history.md
        variant: quarterly
        until: request.history.result
        capture_namespace: prefix
        inherit_current: true
        inherit_context: true
        fresh_run: true
      steps:
      - narrowed-display
      allowed_outcome: display_rejection_without_commit
      parameters:
        history_fixture: common.md#/scenario/fixtures/history-quarterly
    narrowed-label-after-commit:
      prefix:
        source: 01-intention-from-history.md
        variant: quarterly
        until: accept.commit
        capture_namespace: prefix
        inherit_current: true
        inherit_context: true
        fresh_run: true
      steps:
      - narrowed-completion
      allowed_outcome: completion_rejection_preserves_commit
      parameters:
        history_fixture: common.md#/scenario/fixtures/history-quarterly
```

Каждая ветка начинает отдельный прогон. Подветки раскрытия намеренно разделены: отказ до display не равен отказу после законного коммита.

## Устаревший внешний календарь допустим

```yaml transcript
step:
  id: external-lag
  input:
    kind: observe
    after: prefix.accept.complete
  end: external-lag.checked
```

**Ответ из prefix — пример, не новый ответ шага observe:** Добавил в планы «Подготовить квартальный отчёт». В календарь ничего не ставил.

**Ожидаемые вызовы и результаты:**

```yaml transcript
expect:
  step: external-lag
  calls: []
```

<details>
<summary>Точные события, связи и контекст этого шага</summary>

```yaml transcript
expect:
  step: external-lag
  events:
  - id: external-lag.checked
    operation: observer.checkpoint
    count: 1
    fields:
      internal_frontier: F1
      calendar_observation: cal-old
      calendar_staleness: STALE
      receipt_result: $prefix.result
  response:
    semantics:
    - Нет утверждения об актуальной занятости календаря.
```

</details>

**Ожидаемое состояние к концу шага:**

```yaml transcript
expect:
  step: external-lag
  baseline: step.start
  state:
    planning.result:
      eq: $prefix.result
    calendar.observations:
      unchanged: true
    calendar.execution_intents:
      eq: 0
    provider.effects:
      eq: 0
    run.state:
      eq: SUCCEEDED
```

## Смешанный внутренний frontier

**Пользователь · request:** Добавь в планы то, что мы обсуждали про отчёт.

```yaml transcript
step:
  id: mixed-frontier
  input:
    kind: message
    message_ref: request
    already_in_initial_context: true
    fault: mixed-frontier
  end: mixed-frontier.rejected
```

**Ожидаемые вызовы и результаты:**

```yaml transcript
expect:
  step: mixed-frontier
  calls:
  - id: mixed-frontier.propose
    boundary: model_tool
    operation: planning.propose_intention
    count: 0
  - id: mixed-frontier.execute
    boundary: owner
    operation: planning.execute
    count: 0
```

<details>
<summary>Точные события, связи и контекст этого шага</summary>

```yaml transcript
expect:
  step: mixed-frontier
  events:
  - id: mixed-frontier.fault
    operation: fault.reached
    count: 1
    fields:
      fault: mixed-frontier
  - id: mixed-frontier.rejected
    operation: workspace.batch_rejected
    count: 1
    after:
    - mixed-frontier.fault
    fields:
      reason: MIXED_INTERNAL_FRONTIER
      typed: true
```

</details>

**Chiplog · example-2 · пример:** Не получается проверить, что уже есть в планах. Пока ничего не добавил.

**Ожидаемое состояние к концу шага:**

```yaml transcript
expect:
  step: mixed-frontier
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
    workspace.accepted_batches:
      delta: 0
    proposal.current:
      eq: null
```

## Подмена ревизии экрана

```yaml transcript
step:
  id: wrong-screen-revision
  input:
    kind: resume
    after: prefix.request.display
    fault: wrong-screen-revision
  end: wrong-screen-revision.rejected
```

**Система, wrong-screen-revision:** Останавливает передачу подменённого контекста до model emission.

**Ожидаемые вызовы и результаты:**

```yaml transcript
expect:
  step: wrong-screen-revision
  calls: []
```

<details>
<summary>Точные события, связи и контекст этого шага</summary>

```yaml transcript
expect:
  step: wrong-screen-revision
  events:
  - id: wrong-screen-revision.fault
    operation: fault.reached
    count: 1
    fields:
      fault: wrong-screen-revision
  - id: wrong-screen-revision.rejected
    operation: visibility.rejected
    count: 1
    after:
    - wrong-screen-revision.fault
    fields:
      reason: SCREEN_REVISION_MISMATCH
      typed: true
  context:
    expected_current:
      planning: $prefix.proposal_screen
    emission_with_injected_context: false
```

</details>

**Ожидаемое состояние к концу шага:**

```yaml transcript
expect:
  step: wrong-screen-revision
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
    visibility.accepted_substitutions:
      delta: 0
    proposal.current:
      eq: $prefix.proposal
```

## Более широкое раскрытие до display

```yaml transcript
step:
  id: narrowed-display
  input:
    kind: resume
    after: prefix.request.history.result
    fault: narrowed-display
  end: narrowed-display.rejected
```

**Система, narrowed-display:** Отклоняет предложение, производное от owner-private истории, с более широким доступом без основания.

**Ожидаемые вызовы и результаты:**

```yaml transcript
expect:
  step: narrowed-display
  calls:
  - id: narrowed-display.propose
    boundary: model_tool
    operation: planning.propose_intention
    arguments:
      purpose: Подготовить квартальный отчёт
    count: 1
    result:
      source: real_boundary
      fields:
        disposition: REJECTED
        reason: DISCLOSURE_VIOLATION
```

<details>
<summary>Точные события, связи и контекст этого шага</summary>

```yaml transcript
expect:
  step: narrowed-display
  events:
  - id: narrowed-display.fault
    operation: fault.reached
    count: 1
    fields:
      fault: narrowed-display
  - id: narrowed-display.rejected
    operation: proposal.display_rejected
    count: 1
    after:
    - narrowed-display.fault
    - narrowed-display.propose.call
    fields:
      reason: DISCLOSURE_VIOLATION
      typed: true
```

</details>

**Ожидаемое состояние к концу шага:**

```yaml transcript
expect:
  step: narrowed-display
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
    proposal.current:
      eq: null
    visibility.unauthorized_publications:
      delta: 0
```

## Более широкое раскрытие после коммита

```yaml transcript
step:
  id: narrowed-completion
  input:
    kind: resume
    after: prefix.accept.commit
    fault: narrowed-completion
  end: narrowed-completion.rejected
```

**Недоставленный пример ответа, публикация которого будет отклонена:** Добавил в планы «Подготовить квартальный отчёт».

**Ожидаемый исход системы:** публикация с подменённым label отклонена; законная запись в планах сохранена.

**Ожидаемые вызовы и результаты:**

```yaml transcript
expect:
  step: narrowed-completion
  calls: []
```

<details>
<summary>Точные события, связи и контекст этого шага</summary>

```yaml transcript
expect:
  step: narrowed-completion
  events:
  - id: narrowed-completion.captured
    operation: model.response_captured
    count: 1
    fields:
      fixture: local-complete
      schema_valid: true
      receipt_result: $prefix.result
  - id: narrowed-completion.fault
    operation: fault.reached
    count: 1
    after:
    - narrowed-completion.captured
    fields:
      fault: narrowed-completion
  - id: narrowed-completion.rejected
    operation: completion.rejected
    count: 1
    after:
    - narrowed-completion.fault
    fields:
      reason: DISCLOSURE_VIOLATION
      typed: true
```

</details>

**Ожидаемое состояние к концу шага:**

```yaml transcript
expect:
  step: narrowed-completion
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
    completion.accepted_history:
      delta: 0
    completion.delivery_intents:
      delta: 0
    completion.run_succeeded:
      eq: false
    visibility.unauthorized_publications:
      delta: 0
```

**Запреты:**

```yaml transcript
forbid:
  use: common.md#/forbid
  predicates:
  - accept_mixed_internal_frontier
  - emit_context_with_wrong_snapshot_revision
  - broaden_disclosure_without_authority
  - count_unreached_fault_as_tested
  - replace_typed_rejection_with_model_refusal
  - infer_no_write_from_unchanged_screen
```

`broader-than-owner-private` — fixture binding, который обязан разрешаться в реально более широкий набор получателей, не новая lattice. Observer отдельно проверяет owner publications, visibility и disclosure. Component-прогон R12 не считается доказательством model/CompleteAcceptance поведения R13.
