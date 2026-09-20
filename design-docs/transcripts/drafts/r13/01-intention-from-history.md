# Отчёт из прошлого разговора

**Статус: design · format v2 · stateful profile v2.** Ожидаемый сценарий, не запись прогона.
[Основной формат](../../../TRANSCRIPTS.md) · [Stateful-профиль](STATEFUL-PROTOCOL.md) · [Общие данные](common.md).

**Связь с текущим кодом:** `history.read` и `planning.propose_intention` — проектируемые инструменты. Текущий R13 передаёт историю в начальном контексте и использует `propose_planning`. Этот вариант описывает желаемое поведение другого интерфейса; перенос формата не устраняет различие.

> **User story.** Вернуться к прошлому разговору об отчёте и добавить его в планы, не объясняя всё заново.

**Сценарий, исходный контекст и независимые варианты:**

```yaml transcript
scenario:
  format_version: 2
  protocol_version: 2
  id: r13-intention-from-history
  version: 4
  status: design
  arrange:
    use: common.md#/scenario/arrange
  initial_context:
    use: common.md#/scenario/initial_context
  policy:
    use: common.md#/scenario/policy
  fixtures:
  - use: $history_fixture
  evaluation:
    orchestration: hermetic_model_adapter
    history_understanding:
      variants:
      - quarterly
      - annual
      adapter: model_without_hidden_scenario
      required_separately: true
  variants:
    quarterly:
      parameters:
        purpose: Подготовить квартальный отчёт
        history_fixture: common.md#/scenario/fixtures/history-quarterly
      steps:
      - request
      - accept
      allowed_outcome: local_committed_receipt
    annual:
      parameters:
        purpose: Подготовить годовой отчёт
        history_fixture: common.md#/scenario/fixtures/history-annual
      steps:
      - request
      - accept
      allowed_outcome: local_committed_receipt
    unavailable:
      parameters:
        history_fixture: common.md#/scenario/fixtures/history-unavailable
      steps:
      - history-unavailable
      allowed_outcome: accepted_clarification_without_write
```

## Предложение из истории

**Пользователь · request:** Добавь в планы то, что мы обсуждали про отчёт.

```yaml transcript
step:
  id: request
  input:
    kind: message
    message_ref: request
    already_in_initial_context: true
  end: request.display
```

**Ожидаемые вызовы и результаты:**

```yaml transcript
expect:
  step: request
  calls:
  - id: request.history
    boundary: model_tool
    operation: history.read
    arguments:
      conversation: conversation-main
      before:
        message_ref: request
      limit: 20
    count: 1
    result:
      fixture: $history_fixture
  - id: request.propose
    boundary: model_tool
    operation: planning.propose_intention
    arguments:
      purpose: $purpose
    count: 1
    after:
    - request.history.result
    result:
      source: real_boundary
      kind: owner_proposal_reference
      capture:
        proposed_proposal: proposal
```

<details>
<summary>Точные события, связи и контекст этого шага</summary>

```yaml transcript
expect:
  step: request
  events:
  - id: request.display
    operation: proposal.displayed
    count: 1
    after:
    - request.propose.result
    capture:
      proposal: proposal
      display: display_digest
      command: command
      binding: binding
      interpretation: interpretation
      source_ingress: source_ingress
      result_manifest: result_manifest
      proposal_screen: screen_snapshot
    fields:
      purpose: $purpose
      scheduled_time: null
      active_intention: false
      proposal: $proposed_proposal
  context:
    updates:
    - screen: planning
      from_revision: 1
      after_event: request.display
      from_observed_result: $proposal_screen
    carry:
    - conversation
    - journal
    - calendar
```

</details>

```yaml transcript
design:
  step: request
  tool_call:
    expect_ref: request.history
  tool_result:
    fixture_ref: $history_fixture
  rationale: Ответ истории определяет purpose; прошлый факт и устаревший календарь не заменяют историю.
```

**Chiplog · example-1 · пример:** Добавить в планы «Подготовить квартальный отчёт»? В календарь пока ничего не ставлю.

**Ожидаемое состояние к концу шага:**

```yaml transcript
expect:
  step: request
  baseline: step.start
  state:
    planning.results:
      delta: 0
    planning.intentions:
      delta: 0
    proposal.current:
      eq: $proposal
    proposal.status:
      eq: DISPLAYED
    journal.claims:
      unchanged: true
    calendar.observations:
      unchanged: true
    provider.effects:
      delta: 0
```

## Принятие точного предложения

**Пользователь · accept:** Да, добавь.

```yaml transcript
step:
  id: accept
  input:
    kind: authenticated_adoption
    principal: owner
    proposal: $proposal
    display_digest: $display
    capture:
      adoption: authenticated_act
    message_ref: accept
  end: accept.complete
```

**Ожидаемые вызовы и результаты:**

```yaml transcript
expect:
  step: accept
  calls:
  - id: accept.execute
    boundary: owner
    operation: planning.execute
    arguments:
      kind: CreateIntentionLine
      purpose: $purpose
      command: $command
      binding: $binding
      adoption: $adoption
    count: 1
    after:
    - request.display
    result:
      source: real_boundary
      fields:
        disposition: COMMITTED
      capture:
        result: result
        accepted_command: accepted_envelope
        committed_screen: screen_snapshot
```

<details>
<summary>Точные события, связи и контекст этого шага</summary>

```yaml transcript
expect:
  step: accept
  events:
  - id: accept.commit
    operation: planning.result
    count: 1
    after:
    - accept.execute.call
    same_call_as: accept.execute
    fields:
      disposition: COMMITTED
      result: $result
  - id: accept.complete
    operation: completion.accepted
    count: 1
    after:
    - accept.commit
    fields:
      receipt_result: $result
      assertion_kind: DeliveryAssertion
      claim: LOCAL_PLANNING_COMMITTED
      disclosure_accepted: true
      atomic:
      - accepted_history
      - delivery_intents
      - run_head
  context:
    updates:
    - screen: planning
      from_revision: $proposal_screen.revision
      after_event: accept.commit
      from_observed_result: $committed_screen
    before_next_model_call: true
  response:
    semantics:
    - Названо точное намерение из истории.
    - Сообщён только локальный коммит, время не назначено.
    - Выбрать время предложено отдельно; успех календаря не заявлен.
```

</details>

**Chiplog · example-2 · пример:** Добавил в планы «Подготовить квартальный отчёт». В календарь ничего не ставил. Хочешь сейчас выбрать время?

**Ожидаемое состояние к концу шага:**

```yaml transcript
expect:
  step: accept
  baseline: step.start
  state:
    planning.results:
      delta: 1
    planning.intentions:
      delta: 1
    planning.result:
      eq: $result
    planning.purpose:
      eq: $purpose
    planning.scheduled_time:
      eq: null
    calendar.execution_intents:
      delta: 0
    provider.effects:
      delta: 0
    run.state:
      eq: SUCCEEDED
```

## История недоступна

```yaml transcript
step:
  id: history-unavailable
  input:
    kind: message
    message_ref: request
    already_in_initial_context: true
  end: history-unavailable.complete
```

**Вход пользователя:** та же реплика [request](#предложение-из-истории), определённая выше; новый независимый вариант.

**Ожидаемые вызовы и результаты:**

```yaml transcript
expect:
  step: history-unavailable
  calls:
  - id: history-unavailable.read
    boundary: model_tool
    operation: history.read
    arguments:
      conversation: conversation-main
      before:
        message_ref: request
      limit: 20
    count: 1
    result:
      fixture: $history_fixture
      fields:
        status: INDETERMINATE
        messages: null
```

<details>
<summary>Точные события, связи и контекст этого шага</summary>

```yaml transcript
expect:
  step: history-unavailable
  events:
  - id: history-unavailable.complete
    operation: completion.accepted
    count: 1
    after:
    - history-unavailable.read.result
    fields:
      response_kind: clarification
  response:
    semantics:
    - История недоступна; требуется уточнить название, а не угадать его.
```

</details>

**Chiplog · example-3 · пример:** Не получается открыть прошлую переписку. Напомни, что именно нужно сделать?

**Ожидаемое состояние к концу шага:**

```yaml transcript
expect:
  step: history-unavailable
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

**Запреты и граница доказательства:**

```yaml transcript
forbid:
  use: common.md#/forbid
  steps:
    history-unavailable:
      operations:
      - planning.propose_intention
      - planning.execute
  predicates:
  - infer_current_purpose_from_past_journal_or_stale_calendar
  - treat_scripted_adapter_as_evidence_of_history_understanding
```

Варианты annual и quarterly различаются только историей и ожидаемым purpose. Оба проходят настоящий prefix/loop; проверка понимания истории моделью выполняется отдельно от детерминированной проверки orchestration.
