# Старое или чужое подтверждение

**Статус: design · format v2 · stateful profile v2.** Ожидаемый сценарий, не запись прогона.
[Основной формат](../../../TRANSCRIPTS.md) · [Stateful-профиль](STATEFUL-PROTOCOL.md) · [Общие данные](common.md).

**Связь с текущим кодом:** Ветка rebuild требует нового proposal и model-tool вызова. Текущий R13 допускает новый display того же proposal без вызова модели. Этот draft пока описывает более узкий проектируемый маршрут.

> **User story.** Добавить отчёт в планы после своего подтверждения. Если ответ пришёл слишком поздно или не от того человека, запись не должна появиться сама.

**Сценарий, настоящий prefix и варианты:**

```yaml transcript
scenario:
  format_version: 2
  protocol_version: 2
  id: r13-exact-adoption
  version: 4
  status: design
  arrange:
    use: common.md#/scenario/arrange
  initial_context:
    use: common.md#/scenario/initial_context
  policy:
    use: common.md#/scenario/policy
  fixtures: []
  faults:
  - id: stale-authority
    boundary: authority.owner_update
    after: prefix.request.display
    before: stale.adoption
    dependency_of: $prefix.binding
    change:
      head:
        from: A1
        to: A2
      allowed: true
    count: 1
    require_reached: true
  baselines:
    stale: after_fault_before_adoption
    other_steps: step.start
  variants:
    stale:
      prefix:
        source: 01-intention-from-history.md
        variant: quarterly
        until: request.display
        capture_namespace: prefix
        inherit_current: true
        inherit_context: true
        fresh_run: true
      steps:
      - stale
      - rebuild
      - old-replay
      - fresh-adopt
      allowed_outcome: fresh_adoption_local_receipt
      parameters:
        history_fixture: common.md#/scenario/fixtures/history-quarterly
    wrong-display:
      prefix:
        source: 01-intention-from-history.md
        variant: quarterly
        until: request.display
        capture_namespace: prefix
        inherit_current: true
        inherit_context: true
        fresh_run: true
      steps:
      - wrong-display
      allowed_outcome: typed_rejection_without_execute
      parameters:
        history_fixture: common.md#/scenario/fixtures/history-quarterly
    wrong-principal:
      prefix:
        source: 01-intention-from-history.md
        variant: quarterly
        until: request.display
        capture_namespace: prefix
        inherit_current: true
        inherit_context: true
        fresh_run: true
      steps:
      - wrong-principal
      allowed_outcome: typed_rejection_without_disclosure
      parameters:
        history_fixture: common.md#/scenario/fixtures/history-quarterly
```

Каждый вариант заново исполняет [01](01-intention-from-history.md) до наблюдённого `request.display`. P1, его экран, команда и binding получены от владельца.

## Условия изменились

**Chiplog · example-1 · пример:** Добавить в планы «Подготовить квартальный отчёт»? В календарь пока ничего не ставлю.

**Пользователь · stale:** Да, добавь.

```yaml transcript
step:
  id: stale
  input:
    kind: authenticated_adoption
    principal: owner
    proposal: $prefix.proposal
    display_digest: $prefix.display
    capture:
      old_adoption: authenticated_act
    fault_before_adoption: stale-authority
    message_ref: stale
  end: stale.rejected
```

**Ожидаемые вызовы и результаты:**

```yaml transcript
expect:
  step: stale
  calls:
  - id: stale.execute
    boundary: owner
    operation: planning.execute
    arguments:
      command: $prefix.command
      binding: $prefix.binding
      adoption: $old_adoption
    count:
      min: 0
      max: 1
    result:
      source: real_boundary
      fields:
        disposition: STALE
        result: null
```

<details>
<summary>Точные события, связи и контекст этого шага</summary>

```yaml transcript
expect:
  step: stale
  events:
  - id: stale.fault
    operation: fault.reached
    count: 1
    fields:
      fault: stale-authority
      changed_dependency_of: $prefix.binding
  - id: stale.rejected
    operation: adoption.rejected
    count: 1
    after:
    - stale.fault
    fields:
      reason: STALE
      typed: true
```

</details>

**Chiplog · example-2 · пример:** Пока ты отвечал, условия изменились. Я ничего не добавил.

**Ожидаемое состояние к концу шага:**

```yaml transcript
expect:
  step: stale
  baseline: after_fault_before_adoption
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
```

## Новое предложение

```yaml transcript
step:
  id: rebuild
  input:
    kind: resume
    after: stale.rejected
  end: rebuild.display
```

**Ожидаемые вызовы и результаты:**

```yaml transcript
expect:
  step: rebuild
  calls:
  - id: rebuild.propose
    boundary: model_tool
    operation: planning.propose_intention
    arguments:
      purpose: Подготовить квартальный отчёт
    count: 1
    result:
      source: real_boundary
      kind: owner_proposal_reference
      capture:
        rebuilt_proposal: proposal
```

<details>
<summary>Точные события, связи и контекст этого шага</summary>

```yaml transcript
expect:
  step: rebuild
  events:
  - id: rebuild.display
    operation: proposal.displayed
    count: 1
    after:
    - rebuild.propose.result
    capture:
      new_proposal: proposal
      new_display: display_digest
      new_command: command
      new_binding: binding
      new_screen: screen_snapshot
    fields:
      purpose: Подготовить квартальный отчёт
      scheduled_time: null
      proposal: $rebuilt_proposal
  relations:
  - left: $new_proposal
    op: not_eq
    right: $prefix.proposal
  - left: $new_display
    op: not_eq
    right: $prefix.display
  - left: $new_binding
    op: not_eq
    right: $prefix.binding
  context:
    updates:
    - screen: planning
      from_revision: $prefix.proposal_screen.revision
      after_event: rebuild.display
      from_observed_result: $new_screen
```

</details>

**Chiplog · example-3 · пример:** Добавить в планы «Подготовить квартальный отчёт»? В календарь пока ничего не ставлю.

**Ожидаемое состояние к концу шага:**

```yaml transcript
expect:
  step: rebuild
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
      eq: $new_proposal
    proposal.status:
      eq: DISPLAYED
```

## Старое согласие не переносится

```yaml transcript
step:
  id: old-replay
  input:
    kind: authenticated_adoption_replay
    act: $old_adoption
    bytes: identical
  end: old-replay.rejected
```

**Transport, old-replay:** Повторно доставляет прежнее подтверждение P1, без изменения его байтов.

**Ожидаемые вызовы и результаты:**

```yaml transcript
expect:
  step: old-replay
  calls:
  - id: old-replay.execute
    boundary: owner
    operation: planning.execute
    arguments:
      command: $prefix.command
      binding: $prefix.binding
      adoption: $old_adoption
    count:
      min: 0
      max: 1
    result:
      source: real_boundary
      fields:
        disposition: STALE
        result: null
```

<details>
<summary>Точные события, связи и контекст этого шага</summary>

```yaml transcript
expect:
  step: old-replay
  events:
  - id: old-replay.rejected
    operation: adoption.rejected
    count: 1
    fields:
      reason: STALE
      typed: true
```

</details>

**Chiplog · example-4 · пример:** Это ответ на прошлое сообщение. Подтверди, пожалуйста, последнее.

**Ожидаемое состояние к концу шага:**

```yaml transcript
expect:
  step: old-replay
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
      eq: $new_proposal
    proposal.status:
      eq: DISPLAYED
```

## Принятие P2

**Пользователь · fresh-adopt:** Да, теперь добавляй.

```yaml transcript
step:
  id: fresh-adopt
  input:
    kind: authenticated_adoption
    principal: owner
    proposal: $new_proposal
    display_digest: $new_display
    capture:
      new_adoption: authenticated_act
    message_ref: fresh-adopt
  end: fresh-adopt.complete
```

**Ожидаемые вызовы и результаты:**

```yaml transcript
expect:
  step: fresh-adopt
  calls:
  - id: fresh-adopt.execute
    boundary: owner
    operation: planning.execute
    arguments:
      kind: CreateIntentionLine
      purpose: Подготовить квартальный отчёт
      command: $new_command
      binding: $new_binding
      adoption: $new_adoption
    count: 1
    result:
      source: real_boundary
      fields:
        disposition: COMMITTED
      capture:
        new_result: result
        new_committed_screen: screen_snapshot
```

<details>
<summary>Точные события, связи и контекст этого шага</summary>

```yaml transcript
expect:
  step: fresh-adopt
  events:
  - id: fresh-adopt.commit
    operation: planning.result
    count: 1
    same_call_as: fresh-adopt.execute
    fields:
      disposition: COMMITTED
      result: $new_result
  - id: fresh-adopt.complete
    operation: completion.accepted
    count: 1
    after:
    - fresh-adopt.commit
    fields:
      receipt_result: $new_result
      claim: LOCAL_PLANNING_COMMITTED
      disclosure_accepted: true
  context:
    updates:
    - screen: planning
      from_revision: $new_screen.revision
      after_event: fresh-adopt.commit
      from_observed_result: $new_committed_screen
    before_next_model_call: true
```

</details>

**Chiplog · example-5 · пример:** Добавил в планы «Подготовить квартальный отчёт». В календарь ничего не ставил.

**Ожидаемое состояние к концу шага:**

```yaml transcript
expect:
  step: fresh-adopt
  baseline: step.start
  state:
    planning.results:
      delta: 1
    planning.intentions:
      delta: 1
    planning.result:
      eq: $new_result
    calendar.execution_intents:
      delta: 0
    provider.effects:
      delta: 0
    run.state:
      eq: SUCCEEDED
```

## Digest другого display

```yaml transcript
step:
  id: wrong-display
  input:
    kind: authenticated_adoption
    principal: owner
    proposal: $prefix.proposal
    display_digest:
      different_from: $prefix.display
      type: valid_digest
  end: wrong-display.rejected
```

**Вход, wrong-display:** Независимый вариант после нового prefix P1.

**Ожидаемые вызовы и результаты:**

```yaml transcript
expect:
  step: wrong-display
  calls:
  - id: wrong-display.execute
    boundary: owner
    operation: planning.execute
    count: 0
```

<details>
<summary>Точные события, связи и контекст этого шага</summary>

```yaml transcript
expect:
  step: wrong-display
  events:
  - id: wrong-display.rejected
    operation: adoption.rejected
    count: 1
    fields:
      reason: DISPLAY_MISMATCH
      typed: true
```

</details>

**Chiplog · example-6 · пример:** Не получилось подтвердить. Пока ничего не добавил.

**Ожидаемое состояние к концу шага:**

```yaml transcript
expect:
  step: wrong-display
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
```

## Подтверждение другого principal

```yaml transcript
step:
  id: wrong-principal
  input:
    kind: authenticated_adoption
    principal: other-owner
    proposal: $prefix.proposal
    display_digest: $prefix.display
  end: wrong-principal.rejected
```

**Вход, wrong-principal:** Независимый вариант после нового prefix P1.

**Ожидаемые вызовы и результаты:**

```yaml transcript
expect:
  step: wrong-principal
  calls:
  - id: wrong-principal.execute
    boundary: owner
    operation: planning.execute
    count: 0
```

<details>
<summary>Точные события, связи и контекст этого шага</summary>

```yaml transcript
expect:
  step: wrong-principal
  events:
  - id: wrong-principal.rejected
    operation: adoption.rejected
    count: 1
    fields:
      reason: UNAUTHORIZED_PRINCIPAL
      typed: true
```

</details>

**Chiplog · example-7 · пример:** У тебя нет доступа к этому действию.

**Ожидаемое состояние к концу шага:**

```yaml transcript
expect:
  step: wrong-principal
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
    visibility.disclosed_to_other_principal:
      eq: false
```

**Запреты:**

```yaml transcript
forbid:
  use: common.md#/forbid
  predicates:
  - reuse_old_adoption_for_new_display
  - mutate_existing_adoption
  - inject_authoritative_proposal
  - treat_unreached_authority_fault_as_stale_coverage
```

Wrong-display и wrong-principal заканчиваются typed rejection; восстановление в их контракт не входит. Для stale наблюдатель проверяет реально прочитанную dependency binding; смена постороннего head — setup FAIL.
