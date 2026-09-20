# Записать плавание в планы: исполняемый сценарий R13

**Статус: executable · hermetic R13.** Проверяется локальная запись в планы через production loop. Провайдеры и реальные получатели остаются HOLD. Это сценарий, не отчёт о запуске.
[Формат](../TRANSCRIPTS.md).

> **User story.** Добавить еженедельное плавание в планы и понять, что время в календаре ещё не выбрано.

## Исходные условия и ответы модели

```yaml transcript
scenario:
  format_version: 2
  status: executable
  id: local-planning-receipt
  version: 2
  vision_version: 2026-08-22.2
  arrange:
    principal: hermetic-principal
    channel: hermetic-local
    proposal: $message.request
    adoption: exact displayed planning proposal
  fixtures:
    - boundary: model
      match: turn 1
      result: {"kind":"Continue","tool_calls":[{"call_id":"plan","tool":"propose_planning","text":"Swim every week"}]}
    - boundary: model
      match: turn 2
      result: {"kind":"Continue","tool_calls":[{"call_id":"intent","tool":"propose_intent","text":"Propose a calendar block; provider execution remains HOLD"}]}
    - boundary: model
      match: turn 3
      result: {"kind":"Complete","deliveries":[{"kind":"DeliveryAssertion","assertion_code":"LOCAL_PLANNING_COMMITTED","evidence_id":"local-planning-receipt/turn/1/proposal/plan/display/1/command"}]}
  allowed_outcomes:
    - local committed receipt with provider HOLD
```

Фикстуры задают ответы модели на реальные запросы. Это проверка оркестрации, а не понимания языка моделью. ToolOutcome, display и receipt возвращают настоящие обработчики.

## 1. Получить предложение и показать его

**Пользователь · request:** Help me plan weekly swimming

```yaml transcript
step:
  id: request
  input:
    kind: message
    message_ref: request
  end: proposal.displayed
```

**Вызов модели → `propose_planning`** — точные bytes заданы единожды в model fixture `turn 1` выше.

```yaml transcript
design:
  tool_call:
    boundary: model_tool
    fixture_index: 0
    call_index: 0
  tool_result:
    source: real_boundary
    type: ToolOutcome
    capture:
      proposal: proposal_id
  rationale: the model requests a proposal; the owner supplies its actual identity
```

**Действие интерфейса → `loop.display`**, затем показ полученного display:

```yaml transcript
design:
  tool_call:
    boundary: owner
    operation: loop.display
    arguments:
      proposal_id: $proposal
  tool_result:
    source: real_boundary
    type: ProposalDisplay
    capture:
      display: entire_result
  rationale: display contains the exact command and adoption act for confirmation
```

**Ожидаемое состояние после показа, до подтверждения:**

```yaml transcript
expect:
  step: request
  state:
    planning_changed:
      eq: false
    external_effects:
      eq: 0
    run:
      eq: ACTIVE
```

Driver проверяет настоящие planning_receipts и delivery records до передачи подтверждения.

## 2. Подтвердить показанную запись и получить результат

**Пользователь · accept:** Confirm this entry.

```yaml transcript
step:
  id: accept
  input:
    kind: authenticated_adoption
    message_ref: accept
    peer: hermetic-ingress
  end: run.succeeded
```

Реплика обозначает действие подтверждения. Driver вызывает authenticated ingress с данными реального display; текст сообщения сам по себе не является authority.

**Вызов владельца → `loop.adopt`:**

```yaml transcript
design:
  tool_call:
    boundary: owner
    operation: loop.adopt
    arguments:
      peer: hermetic-ingress
      display_id: $display.display_id
      digest: $display.display_digest
      adoption_act_id: $display.adoption_act_id
  tool_result:
    source: real_boundary
    type: LocalPlanningReceipt
    capture:
      receipt: entire_result
  rationale: authenticated ingress adopts the exact display
```

**Следующий вызов модели → `propose_intent`** — точные bytes в fixture `turn 2`. Это предложение дальнейшего действия, а не исполнение календаря.

```yaml transcript
design:
  tool_call:
    boundary: model_tool
    fixture_index: 1
    call_index: 0
  tool_result:
    source: real_boundary
    type: ToolOutcome
    expected:
      evidence_id: null
  rationale: proposing a calendar intent does not execute it
```

Fixture `turn 3` возвращает `DeliveryAssertion` для локальной записи. Runtime проверяет evidence и формирует следующий точный ответ.

**Chiplog · receipt · exact:** Committed local intention: Swim every week.

**Ожидаемые ответ и состояние:**

```yaml transcript
expect:
  step: accept
  response:
    exact: $message.receipt
  trace:
    - proposal before authority
    - exact adoption before local receipt
    - intent is proposal only
  state:
    planning_changed:
      eq: true
    external_effects:
      eq: 0
    run:
      eq: SUCCEEDED
```

Текущий formatter говорит технически. Желаемый будущий текст интерфейса: «Добавил еженедельное плавание в планы. В календарь пока ничего не ставил». Он не участвует в exact-проверке.

`design` объясняет реальные операции, но его произвольные поля не превращаются в проверки. Исполняемый контракт ограничен зарегистрированными шагами и проверками driver; полный фактический trace сохраняется отдельно.

## Запреты во всём сценарии

```yaml transcript
forbid:
  - planning mutation before authenticated adoption
  - provider or real recipient exposure
  - provider success receipt
```
