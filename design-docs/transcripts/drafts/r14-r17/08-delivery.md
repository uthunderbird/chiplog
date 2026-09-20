# Результат готов, ответ не доставлен

**Статус: design · format v2 · stateful profile v2 · component history.** Не запись прогона.

Естественная реплика задаёт мотив истории. Описанный разрез готовит её на offline source и проверяет owner/broker механизм; понимание реплики моделью и полный production loop здесь ещё не подключены. Пример ответа — цель будущей интеграции, не наблюдение component-прогона.
[Общие bindings](mechanisms-common.md) · [План](../../../R14-R17-TRANSCRIPT-PLAN.md).

> **User story.** Доставить уже принятый результат без повторного выполнения работы и без отправки другому адресату.

R16 + R17.3–4 / A10, A32, A34, A107: committed owner query, evidence-bound deterministic rendering, exact recipient и last-boundary disclosure. R17 delivery_preparation.py:prepare_completion/revalidate_delivery — места подключения, не готовые transcript bindings.

## Варианты

| Вариант | Событие среды и проверяемый исход |
|---|---|
| lost-ack | Fake transport принимает bytes и теряет ack; worker делает bounded reconciliation. |
| endpoint-changed | Endpoint head меняется до последней обратимой send boundary. |
| disclosure-narrowed | Доступ адресата сужается до send. |
| late-receipt | После неоднозначной передачи приходит верная квитанция исходного delivery attempt. |
| wrong-recipient-receipt | Подать внешнюю квитанцию другого recipient при совпадающем внешнем message id. |
| cli-confirmed | Отправить принятый deterministic render через CLI driver. |
| telegram-confirmed | Та же проекция через Telegram driver с отдельным transport witness. |

```yaml transcript
scenario:
  format_version: 2
  protocol_version: 2
  id: p08-delivery
  version: 1
  status: design
  arrange:
    use: mechanisms-common.md#/scenario/arrange
  initial_context:
    principal: mechanism-owner
    messages:
    - id: request
      role: user
      message_ref: request
    tools: []
    instructions: Сообщай только результат допустимых owner reads; не используй скрытый test observer.
      История до setup не считается пройденным диалогом.
  policy:
    use: mechanisms-common.md#/scenario/policy
  fixtures: []
  variants:
    lost-ack:
      parameters:
        setup_spec:
          owner_result: produce_through_real_owner_boundary
          closed_evidence: produce_through_real_resolver
          completion: accept_real_render_bytes_before_delivery
          recipient_selection: ORIGIN_EXACT
          original_endpoint: offline-origin
          alternate_endpoint: offline-other
          delivery_state: pending
          model_rerun_budget: 0
        case_name: lost-ack
        case_description: Fake transport принимает bytes и теряет ack; worker делает bounded reconciliation.
        expected_audit:
          accepted_render_bytes_unchanged: true
          delivery_outcome: unknown
          replacement_deliveries: 0
          recipient: offline-origin
          render_claims_closed_evidence: true
        response_semantics: Сообщает только наблюдаемый результат; неизвестное не превращает в успех.
        model_delta: 0
        delivery_delta: 1
        fault_count: 1
        intervention:
          sequence:
          - transport-accept
          - lose-ack
          - reconcile-unavailable
      steps:
      - setup
      - request
      - exercise-fault
      - inspect
      allowed_outcome: Fake transport принимает bytes и теряет ack; worker делает bounded reconciliation.
      requires:
      - registered_delivery_driver
      - independent_journal_and_transport_observers
    endpoint-changed:
      parameters:
        setup_spec:
          owner_result: produce_through_real_owner_boundary
          closed_evidence: produce_through_real_resolver
          completion: accept_real_render_bytes_before_delivery
          recipient_selection: ORIGIN_EXACT
          original_endpoint: offline-origin
          alternate_endpoint: offline-other
          delivery_state: pending
          model_rerun_budget: 0
        case_name: endpoint-changed
        case_description: Endpoint head меняется до последней обратимой send boundary.
        expected_audit:
          transport_transfers: 0
          old_binding_sent: false
          fallback_recipient_used: false
          disposition: held
        response_semantics: Сообщает только наблюдаемый результат; неизвестное не превращает в успех.
        model_delta: 0
        delivery_delta: 0
        fault_count: 1
        intervention:
          change_before_send: endpoint_head
      steps:
      - setup
      - request
      - exercise-fault
      - inspect
      allowed_outcome: Endpoint head меняется до последней обратимой send boundary.
      requires:
      - registered_delivery_driver
      - independent_journal_and_transport_observers
    disclosure-narrowed:
      parameters:
        setup_spec:
          owner_result: produce_through_real_owner_boundary
          closed_evidence: produce_through_real_resolver
          completion: accept_real_render_bytes_before_delivery
          recipient_selection: ORIGIN_EXACT
          original_endpoint: offline-origin
          alternate_endpoint: offline-other
          delivery_state: pending
          model_rerun_budget: 0
        case_name: disclosure-narrowed
        case_description: Доступ адресата сужается до send.
        expected_audit:
          transport_transfers: 0
          unauthorized_disclosures: 0
          fallback_recipient_used: false
          accepted_history_not_rewritten: true
        response_semantics: Сообщает только наблюдаемый результат; неизвестное не превращает в успех.
        model_delta: 0
        delivery_delta: 0
        fault_count: 1
        intervention:
          change_before_send: disclosure_entitlement
      steps:
      - setup
      - request
      - exercise-fault
      - inspect
      allowed_outcome: Доступ адресата сужается до send.
      requires:
      - registered_delivery_driver
      - independent_journal_and_transport_observers
    late-receipt:
      parameters:
        setup_spec:
          owner_result: produce_through_real_owner_boundary
          closed_evidence: produce_through_real_resolver
          completion: accept_real_render_bytes_before_delivery
          recipient_selection: ORIGIN_EXACT
          original_endpoint: offline-origin
          alternate_endpoint: offline-other
          delivery_state: pending
          model_rerun_budget: 0
        case_name: late-receipt
        case_description: После неоднозначной передачи приходит верная квитанция исходного delivery
          attempt.
        expected_audit:
          delivery_outcome: confirmed
          replacement_deliveries: 0
          receipt_bound_to_original_attempt: true
          accepted_render_bytes_unchanged: true
        response_semantics: Статус доставки закрывается исходной квитанцией; сам результат не генерируется
          заново.
        model_delta: 0
        delivery_delta: 1
        fault_count: 1
        intervention:
          sequence:
          - transport-accept
          - lose-ack
          - authenticated-late-receipt
      steps:
      - setup
      - request
      - exercise-fault
      - inspect
      allowed_outcome: После неоднозначной передачи приходит верная квитанция исходного delivery
        attempt.
      requires:
      - registered_delivery_driver
      - independent_journal_and_transport_observers
    wrong-recipient-receipt:
      parameters:
        setup_spec:
          owner_result: produce_through_real_owner_boundary
          closed_evidence: produce_through_real_resolver
          completion: accept_real_render_bytes_before_delivery
          recipient_selection: ORIGIN_EXACT
          original_endpoint: offline-origin
          alternate_endpoint: offline-other
          delivery_state: pending
          model_rerun_budget: 0
        case_name: wrong-recipient-receipt
        case_description: Подать внешнюю квитанцию другого recipient при совпадающем внешнем message
          id.
        expected_audit:
          delivery_outcome: unknown
          wrong_recipient_receipt_accepted: false
          replacement_deliveries: 0
        response_semantics: Сообщает только наблюдаемый результат; неизвестное не превращает в успех.
        model_delta: 0
        delivery_delta: 1
        fault_count: 1
        intervention:
          sequence:
          - transport-accept
          - lose-ack
          - wrong-recipient-receipt
      steps:
      - setup
      - request
      - exercise-fault
      - inspect
      allowed_outcome: Подать внешнюю квитанцию другого recipient при совпадающем внешнем message
        id.
      requires:
      - registered_delivery_driver
      - independent_journal_and_transport_observers
    cli-confirmed:
      parameters:
        setup_spec:
          owner_result: produce_through_real_owner_boundary
          closed_evidence: produce_through_real_resolver
          completion: accept_real_render_bytes_before_delivery
          recipient_selection: ORIGIN_EXACT
          original_endpoint: offline-origin
          alternate_endpoint: offline-other
          delivery_state: pending
          model_rerun_budget: 0
        case_name: cli-confirmed
        case_description: Отправить принятый deterministic render через CLI driver.
        expected_audit:
          channel: cli
          delivery_outcome: confirmed
          recipient_selection: ORIGIN_EXACT
          exact_recipient_verified: true
          accepted_render_bytes_unchanged: true
        response_semantics: CLI получает ровно принятый evidence-bound текст.
        model_delta: 0
        delivery_delta: 1
        fault_count: 0
        intervention:
          drive: ordinary_registered_boundary
      steps:
      - setup
      - request
      - exercise
      - inspect
      allowed_outcome: Отправить принятый deterministic render через CLI driver.
      requires:
      - registered_delivery_driver
      - independent_journal_and_transport_observers
    telegram-confirmed:
      parameters:
        setup_spec:
          owner_result: produce_through_real_owner_boundary
          closed_evidence: produce_through_real_resolver
          completion: accept_real_render_bytes_before_delivery
          recipient_selection: ORIGIN_EXACT
          original_endpoint: offline-origin
          alternate_endpoint: offline-other
          delivery_state: pending
          model_rerun_budget: 0
        case_name: telegram-confirmed
        case_description: Та же проекция через Telegram driver с отдельным transport witness.
        expected_audit:
          channel: telegram
          delivery_outcome: confirmed
          recipient_selection: ORIGIN_EXACT
          exact_recipient_verified: true
          accepted_render_bytes_unchanged: true
        response_semantics: Telegram получает ту же каноническую проекцию; channel auth проверяется
          собственным binding.
        model_delta: 0
        delivery_delta: 1
        fault_count: 0
        intervention:
          drive: ordinary_registered_boundary
      steps:
      - setup
      - request
      - exercise
      - inspect
      allowed_outcome: Та же проекция через Telegram driver с отдельным transport witness.
      requires:
      - registered_delivery_driver
      - independent_journal_and_transport_observers
  requirements:
    grounding: 'R16 + R17.3–4 / A10, A32, A34, A107: committed owner query, evidence-bound deterministic
      rendering, exact recipient и last-boundary disclosure. R17 delivery_preparation.py:prepare_completion/revalidate_delivery
      — места подключения, не готовые transcript bindings.'
    setup: Execute actual boundaries described by setup_spec; never seed accepted results, authority
      or receipts. Capture identities before intervention.
    driver_ops: Draft driver operations below are not model tools; public schema mapping remains
      unregistered.
    independent_audit: Read stored manifest, original streams, versioned heads and external logs;
      never return expected_audit as actual.
    snapshot_scope: same tenant, principal, run/call lineage and command; exact ordered manifests
    fault_scope: one injected sequence per fault variant, each sub-action witnessed in raw hook trace
    baseline: capture at setup; inspect cumulative effects from that capture
    visible_reply: observed runtime reply semantics; prose sample is not evidence
    request_cutoff: Stage canonical user message at offline source; application admission and next
      model call belong to exercise, not request. No fabricated accepted ingress.
    action_cutoff: Stop after owner decision before unrequested model continuation or effect dispatch.
      Delivery uses already accepted render; model scenario alone permits bounded request transfer.
    scope: Component histories with natural-language motivation. request stages source input only;
      no claim of actual model interpretation or production-loop trajectory. Visible response is
      a future integration requirement, not exercised by this owner-only slice.
    response_semantics: Per-variant response_semantics is the integration target; only explicitly
      registered production-loop+renderer extension may execute it. Component runs report owner observations
      separately, never full transcript PASS.
  faults:
  - id: mech-intervention
    boundary: mech14_17.delivery.exercise
    after: action.call
    before: action.result
    change: $intervention
    count: 1
    require_reached: true
```

## Подготовить достигнутое состояние

Setup выполняет реальные owner/broker boundaries с fixture identity factory. Он останавливается до проверяемого вмешательства и захватывает исходные записи и счётчики. Это технический prefix, а не заявленный успешный пользовательский диалог.

```yaml transcript
step:
  id: setup
  input:
    kind: resume
    act: prepare_real_owner_state
    envelope: $setup_spec
  end: setup.observed
```
```yaml transcript
expect:
  step: setup
  calls:
  - id: setup.owner
    boundary: harness_owner_driver
    operation: mech14_17.delivery.setup
    arguments:
      spec: $setup_spec
      case: $case_name
    count: 1
    result:
      source: real_boundary
      capture:
        mechanism_baseline: observed_baseline
        mechanism_subject: subject_binding
  events:
  - id: setup.observed
    operation: mech14_17.delivery.observed
    count: 1
    same_call_as: setup.owner
    fields:
      cutoff: before_intervention
      baseline: $mechanism_baseline
  state:
    mech14_17.external_effects:
      delta: 0
```

## Реплика владельца

**Пользователь · request:** Ты закончил? Пришли результат сюда.

```yaml transcript
step:
  id: request
  input:
    kind: message
    message_ref: request
    already_in_initial_context: true
  end: request.observed
```
```yaml transcript
expect:
  step: request
  calls:
  - id: request.owner
    boundary: harness_owner_driver
    operation: mech14_17.delivery.request
    arguments:
      message: $message.request
      subject: $mechanism_subject
    count: 1
    result:
      source: real_boundary
      fields:
        canonical_source_input_prepared_once: true
        application_admission_not_yet_claimed: true
  events:
  - id: request.observed
    operation: mech14_17.delivery.observed
    count: 1
    same_call_as: request.owner
  state:
    mech14_17.external_effects:
      delta: 0
```

## Событие среды

Пользовательская реплика не подменяет вмешательство: hook/driver выполняет выбранную последовательность на исходном subject. Fault считается достигнутым по отдельному trace внутри action call. Если sub-action не достигнут или driver не умеет нужный cutoff, это FAIL при зарегистрированном binding; отсутствие самого binding — NOT_RUNNABLE.

```yaml transcript
step:
  id: exercise
  input:
    kind: resume
    act: drive_selected_case
    envelope: $mechanism_subject
  end: action.observed
```
```yaml transcript
expect:
  step: exercise
  calls:
  - id: action
    boundary: harness_owner_driver
    operation: mech14_17.delivery.exercise
    arguments:
      case: $case_name
      subject: $mechanism_subject
      intervention: $intervention
      worker_ticks: 4
    count: 1
    result:
      source: real_boundary
      capture:
        mechanism_actual: boundary_result
  events:
  - id: action.observed
    operation: mech14_17.delivery.observed
    count: 1
    same_call_as: action
    fields:
      subject: $mechanism_subject
      completed_registered_case: $case_name
```
```yaml transcript
step:
  id: exercise-fault
  input:
    kind: resume
    act: drive_selected_case
    envelope: $mechanism_subject
    fault: mech-intervention
  end: action.observed
```
```yaml transcript
expect:
  step: exercise-fault
  calls:
  - id: action
    boundary: harness_owner_driver
    operation: mech14_17.delivery.exercise
    arguments:
      case: $case_name
      subject: $mechanism_subject
      intervention: $intervention
      worker_ticks: 4
    count: 1
    result:
      source: real_boundary
      capture:
        mechanism_actual: boundary_result
  events:
  - id: action.fault
    operation: fault.reached
    count: 1
    fields:
      fault: mech-intervention
      change: $intervention
  - id: action.observed
    operation: mech14_17.delivery.observed
    count: 1
    same_call_as: action
    fields:
      subject: $mechanism_subject
      completed_registered_case: $case_name
```

## Независимая проверка и ответ

**Chiplog · answer · пример:** Результат готов, но подтверждения доставки предыдущего ответа пока нет.

Пример относится к основной ветке; target semantics записана в variant.parameters.response_semantics. Component-разрез не вызывает модель ради ответа. Проверка реальной реплики потребует отдельного production-loop/renderer binding; до него полный разговор не получает PASS.

```yaml transcript
step:
  id: inspect
  input:
    kind: observe
    act: read_independent_actual_state
    envelope: $mechanism_subject
  end: audit.observed
```
```yaml transcript
expect:
  step: inspect
  baseline: setup.observed
  calls:
  - id: audit
    boundary: independent_observer
    operation: mech14_17.delivery.inspect
    arguments:
      subject: $mechanism_subject
      baseline: $mechanism_baseline
      case: $case_name
    count: 1
    result:
      source: real_boundary
      fields:
        audit: $expected_audit
  events:
  - id: audit.observed
    operation: mech14_17.delivery.observed
    count: 1
    same_call_as: audit
    fields:
      baseline: $mechanism_baseline
      subject: $mechanism_subject
  state:
    mech14_17.delivery.audit:
      eq: $expected_audit
    mech14_17.external_effects:
      delta: 0
    mech14_17.model_transfers:
      eq: $model_delta
    mech14_17.delivery_transfers:
      eq: $delivery_delta
    mech14_17.fault_hits:
      eq: $fault_count
```
```yaml transcript
forbid:
  use: mechanisms-common.md#/forbid
```

## Границы

Calendar T01 целиком остаётся R18. Setup owner-результата здесь не является прохождением календарного диалога. MODEL_SELECTED_EXACT, prospective-only narrowing после отправки и полная parity matrix остаются техническими ветками R17; этим набором они не закрыты.

Все нули относятся к конечному trace от setup до audit с четырьмя worker ticks. Они не доказывают вечное отсутствие повторов. Без зарегистрированных driver, observer и fault mappings каждый вариант остаётся NOT_RUNNABLE; успешная компиляция не является runtime PASS.
