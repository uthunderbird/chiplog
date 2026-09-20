# Правильный локальный коммит, ложный финальный ответ

**Статус: design · format v2 · stateful profile v2.** Ожидаемый сценарий, не запись прогона.
[Основной формат](../../../TRANSCRIPTS.md) · [Stateful-профиль](STATEFUL-PROTOCOL.md) · [Общие данные](common.md).

**Связь с текущим кодом:** Текущая Turn-schema допускает только LOCAL_PLANNING_COMMITTED. Календарная semantic-ветка остаётся NOT_RUNNABLE. `$prefix.result.id` — неподключённый draft путь; для production assertion нужен LocalPlanningReceipt.evidence_id, а не result_id.

> **User story.** Узнать, что действительно удалось сделать: отчёт добавлен в планы, но в календарь ничего не добавлено.

**Сценарий, prefix и точные ответы model fixture:**

```yaml transcript
scenario:
  format_version: 2
  protocol_version: 2
  id: r13-false-completion
  version: 4
  status: design
  arrange:
    use: common.md#/scenario/arrange
  initial_context:
    use: common.md#/scenario/initial_context
  policy:
    use: common.md#/scenario/policy
  fixtures:
  - id: false-complete
    boundary: model
    match:
      after: prefix.accept.commit
      attempt: 1
    result:
      kind: Complete
      deliveries:
      - kind: DeliveryAssertion
        assertion_code:
          binding: registered_calendar_success_code
        evidence_id: $prefix.result.id
    precondition:
      schema_valid: true
      evidence_confirms_calendar_effect: false
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
  - id: unsupported-complete
    boundary: model
    match:
      after: prefix.accept.commit
      attempt: 1
    result:
      kind: Complete
      deliveries:
      - kind: DeliveryAssertion
        assertion_code: UNSUPPORTED_CALENDAR_SUCCESS
        evidence_id: $prefix.result.id
    precondition:
      schema_valid: false
      assertion_code_registered: false
  requirements:
    fixture_selection: $complete_fixture
    false_complete_requires_schema_validity: true
    unsupported_semantic_branch: NOT_RUNNABLE
    schema_rejection_counts_as_semantic_coverage: false
    normal_response_capture_and_acceptance: true
  variants:
    false-calendar-claim:
      prefix:
        source: 01-intention-from-history.md
        variant: quarterly
        until: accept.commit
        capture_namespace: prefix
        inherit_current: true
        inherit_context: true
        fresh_run: true
      parameters:
        complete_fixture: false-complete
        history_fixture: common.md#/scenario/fixtures/history-quarterly
      requires:
      - registered_calendar_success_code_in_turn_schema
      steps:
      - false-complete
      allowed_outcome: semantic_rejection_preserves_commit
    local-receipt-control:
      prefix:
        source: 01-intention-from-history.md
        variant: quarterly
        until: accept.commit
        capture_namespace: prefix
        inherit_current: true
        inherit_context: true
        fresh_run: true
      parameters:
        complete_fixture: local-complete
        history_fixture: common.md#/scenario/fixtures/history-quarterly
      steps:
      - local-complete
      allowed_outcome: atomic_local_completion
    schema-unavailable:
      prefix:
        source: 01-intention-from-history.md
        variant: quarterly
        until: accept.commit
        capture_namespace: prefix
        inherit_current: true
        inherit_context: true
        fresh_run: true
      parameters:
        complete_fixture: unsupported-complete
        history_fixture: common.md#/scenario/fixtures/history-quarterly
      requires:
      - calendar_success_code_absent_from_turn_schema
      steps:
      - schema-rejection
      allowed_outcome: schema_rejection_only
```

Все варианты заново исполняют [01](01-intention-from-history.md) до `accept.commit`. Экран получен из реального результата R. Календарного эффекта не было.

## Ложное утверждение об успехе

```yaml transcript
step:
  id: false-complete
  input:
    kind: resume
    after: prefix.accept.commit
  end: false-complete.rejected
```

**Недоставленный пример ошибочного ответа:** Добавил отчёт в календарь.

**Ожидаемые вызовы и результаты:**

```yaml transcript
expect:
  step: false-complete
  calls: []
```

<details>
<summary>Точные события, связи и контекст этого шага</summary>

```yaml transcript
expect:
  step: false-complete
  events:
  - id: false-complete.captured
    operation: model.response_captured
    count: 1
    fields:
      fixture: false-complete
      schema_valid: true
      delivery_kind: DeliveryAssertion
      evidence_id: $prefix.result.id
  - id: false-complete.rejected
    operation: completion.rejected
    count: 1
    after:
    - false-complete.captured
    fields:
      reason: MISSING_EFFECT_EVIDENCE
      typed: true
```

</details>

```yaml transcript
design:
  step: false-complete
  tool_call: null
  tool_result: null
  model_result:
    fixture_ref: false-complete
  rationale: Здесь ответ model leaf, а не tool. Принимающий контур должен проверить типизированное утверждение и evidence.
```

**Ожидаемое состояние к концу шага:**

```yaml transcript
expect:
  step: false-complete
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
    completion.attempt:
      eq: TERMINAL_REJECTED
    completion.accepted_history:
      delta: 0
    completion.delivery_intents:
      delta: 0
    completion.run_succeeded:
      eq: false
    completion.raw_response_retained:
      eq: true
```

## Корректный локальный receipt

```yaml transcript
step:
  id: local-complete
  input:
    kind: resume
    after: prefix.accept.commit
  end: local-complete.accepted
```

**Ожидаемые вызовы и результаты:**

```yaml transcript
expect:
  step: local-complete
  calls: []
```

<details>
<summary>Точные события, связи и контекст этого шага</summary>

```yaml transcript
expect:
  step: local-complete
  events:
  - id: local-complete.captured
    operation: model.response_captured
    count: 1
    fields:
      fixture: local-complete
      schema_valid: true
  - id: local-complete.accepted
    operation: completion.accepted
    count: 1
    after:
    - local-complete.captured
    fields:
      receipt_result: $prefix.result
      disclosure_accepted: true
      atomic:
      - accepted_history
      - delivery_intents
      - run_head
  response:
    semantics:
    - Сообщён только локальный результат R.
    - Отдельное предложение выбрать время допустимо как следующий шаг, не как выполненное действие.
```

</details>

**Chiplog · example-2 · пример:** Добавил в планы «Подготовить квартальный отчёт». В календарь ничего не ставил.

**Ожидаемое состояние к концу шага:**

```yaml transcript
expect:
  step: local-complete
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
      delta: 1
    completion.delivery_intents:
      delta: 1
    run.state:
      eq: SUCCEEDED
```

## Код утверждения не поддерживается схемой

```yaml transcript
step:
  id: schema-rejection
  input:
    kind: resume
    after: prefix.accept.commit
  end: schema-rejection.rejected
```

**Отдельный технический вариант:** Если Turn-schema не допускает код успеха календаря, semantic-ветка выше остаётся NOT_RUNNABLE. Здесь проверяется только отказ схемы для заведомо незарегистрированного кода.

**Ожидаемые вызовы и результаты:**

```yaml transcript
expect:
  step: schema-rejection
  calls: []
```

<details>
<summary>Точные события, связи и контекст этого шага</summary>

```yaml transcript
expect:
  step: schema-rejection
  events:
  - id: schema-rejection.captured
    operation: model.response_captured
    count: 1
    fields:
      fixture: unsupported-complete
      schema_valid: false
  - id: schema-rejection.rejected
    operation: model.schema_rejected
    count: 1
    after:
    - schema-rejection.captured
    fields:
      reason: UNKNOWN_ASSERTION_CODE
      typed: true
```

</details>

**Ожидаемое состояние к концу шага:**

```yaml transcript
expect:
  step: schema-rejection
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
```

**Запреты:**

```yaml transcript
forbid:
  use: common.md#/forbid
  predicates:
  - inject_acceptance_verdict
  - replace_typed_assertion_with_unverified_commentary
  - rollback_legal_planning_commit_on_completion_rejection
  - accept_schema_valid_json_without_semantic_check
  - count_schema_rejection_as_missing_evidence_coverage
```

Raw response сохраняется с исходными provenance/disclosure. Planning commit и CompleteAcceptance — разные транзакционные границы. Исправление ответа и recovery не входят в окно этой истории.
