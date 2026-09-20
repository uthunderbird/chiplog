# Записать пропущенное занятие, сохранив план

**Статус: design.** Проектируемый сценарий; bindings журнала ещё не подключены к runner.
[Формат](../TRANSCRIPTS.md).

> **User story.** Записать, что во вторник не сходил в бассейн, и отдельно решить, менять ли будущие планы.

## Исходные условия

```yaml transcript
scenario:
  format_version: 2
  status: design
  id: fact-claim-without-plan-change
  version: 3
  vision_version: 2026-08-22.2
  arrange:
    principal: owner
    channel: cli
    clock: "2026-08-22T18:00:00+05:00"
    timezone: Asia/Almaty
    planning_state:
      title: Бассейн
      occurrence_date: "2026-08-18"
      current: true
    fact_state:
      occurrence_date: "2026-08-18"
      claims: []
  fixtures: []
  allowed_outcomes:
    - fact recorded and plan remains unchanged
    - clarification if Tuesday does not resolve to one occurrence
```

## 1. Уточнить, какую запись сохранить

**Пользователь · request:** Я не ходил в бассейн во вторник.

```yaml transcript
step:
  id: request
  input:
    kind: message
    message_ref: request
  end: claim.displayed
```

**Проектируемый вызов → найти занятие и показать запись:**

```yaml transcript
design:
  tool_call:
    boundary: owner
    operation: resolve_occurrence_and_display_claim
    arguments:
      title: Бассейн
      occurrence_date: "2026-08-18"
  tool_result:
    source: real_boundary
    capture:
      claim_display: displayed_claim
    expected:
      claim_displayed: true
      claim_committed: false
  rationale: a factual report does not authorize a planning revision
```

**Chiplog · proposal · пример:** Записать, что 18 августа ты не ходил в бассейн? Планы оставлю как есть.

**Ожидаемое состояние:**

```yaml transcript
expect:
  step: request
  state:
    fact_claims:
      eq: 0
    planning_changed:
      eq: false
    planning_revisions_added:
      eq: 0
```

## 2. Подтвердить запись

**Пользователь · accept:** Да, запиши.

```yaml transcript
step:
  id: accept
  input:
    kind: authenticated_adoption
    message_ref: accept
    principal: owner
  end: claim.receipt.accepted
```

Ingress связывает подтверждение с показанной записью. Пользователь не вводит идентификаторы и digest вручную.

**Проектируемое действие → сохранить подтверждённое сообщение:**

```yaml transcript
design:
  tool_call:
    boundary: owner
    operation: append_confirmed_claim
    arguments:
      displayed_claim: $claim_display
  tool_result:
    source: real_boundary
    expected:
      occurrence_date: "2026-08-18"
      author: owner
      committed: true
  rationale: only the displayed claim is adopted; planning is not revised
```

**Chiplog · receipt · пример:** Записал: 18 августа ты не ходил в бассейн. Планы не менял. Хочешь перенести занятие?

**Ожидаемые ответ и состояние:**

```yaml transcript
expect:
  step: accept
  response:
    semantics:
      - record is labeled as the principal's Fact Claim
      - exact historical subject is visible
      - Plan staying unchanged is explicit
      - any planning action is offered separately
  state:
    fact_claims:
      eq: 1
    planning_changed:
      eq: false
    planning_revisions_added:
      eq: 0
```

Если «во вторник» нельзя связать с одним занятием, допустим уточняющий вопрос. Показанный путь использует однозначную дату из arrange; запись до подтверждения запрещена.

## Запреты во всём сценарии

```yaml transcript
forbid:
  - automatic task completion, cancellation, rescheduling or reopening
  - treating the plan expectation as proof of what happened
  - presenting provider or model inference as the principal's claim
```
