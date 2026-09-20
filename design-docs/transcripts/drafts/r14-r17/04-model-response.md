# Ответ модели потерялся

**Статус: design · format v2 · stateful profile v2 · component history.** Не запись прогона.

Естественная реплика задаёт мотив истории. Описанный разрез готовит её на offline source и проверяет owner/broker механизм; понимание реплики моделью и полный production loop здесь ещё не подключены. Пример ответа — цель будущей интеграции, не наблюдение component-прогона.
[Общие bindings](mechanisms-common.md) · [План](../../../R14-R17-TRANSCRIPT-PLAN.md).

> **User story.** Не запускать модель повторно лишь потому, что первый ответ не дошёл.

R14.5 / A108: пять состояний ModelCallAttempt, proof-gated replacement и fresh selector generation. Сопоставление draft labels с точными enum/схемой обязательно перед исполнением.

## Варианты

| Вариант | Событие среды и проверяемый исход |
|---|---|
| possible-exposure | Пропустить request bytes через внешний byte observer и потерять ответ. |
| proof-before-emission | Остановить request до любого byte exposure; registered proof verifier принимает точный head proof; разрешить один replacement. |
| timeout-is-not-proof | Передать timeout как якобы доказательство no exposure. |
| stale-proof | Валидный по форме proof относится к другому attempt head. |
| late-old-generation | После законного proof-gated replacement доставить поздний ответ старого поколения. |
| restart-unknown | После possible emission открыть то же хранилище и выполнить четыре worker ticks. |

```yaml transcript
scenario:
  format_version: 2
  protocol_version: 2
  id: p04-model-response
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
    possible-exposure:
      parameters:
        setup_spec:
          attempt: allocated_via_real_model_attempt_boundary
          lineage: one_original_lineage
          generation: 0
          provider: offline_byte_observer
          replacement_budget: 1
        case_name: possible-exposure
        case_description: Пропустить request bytes через внешний byte observer и потерять ответ.
        expected_audit:
          request_transfers_total: 1
          replacement_transfers: 0
          exposure: possible_or_observed
          no_exposure_proof: null
          selected_generation: 0
          outcome: unknown
        response_semantics: Сообщает только наблюдаемый результат; неизвестное не превращает в успех.
        model_delta: 1
        delivery_delta: 0
        fault_count: 1
        intervention:
          drop: response_after_request_transfer
      steps:
      - setup
      - request
      - exercise-fault
      - inspect
      allowed_outcome: Пропустить request bytes через внешний byte observer и потерять ответ.
      requires:
      - registered_model_driver
      - independent_journal_and_transport_observers
    proof-before-emission:
      parameters:
        setup_spec:
          attempt: allocated_via_real_model_attempt_boundary
          lineage: one_original_lineage
          generation: 0
          provider: offline_byte_observer
          replacement_budget: 1
        case_name: proof-before-emission
        case_description: Остановить request до любого byte exposure; registered proof verifier принимает
          точный head proof; разрешить один replacement.
        expected_audit:
          request_transfers_total: 1
          replacement_transfers: 1
          original_request_exposed: false
          proof_verdict: verified_exact_binding
          same_lineage: true
          selected_generation: 1
        response_semantics: Принимает ответ только от выбранного replacement того же lineage.
        model_delta: 1
        delivery_delta: 0
        fault_count: 1
        intervention:
          stop: before_first_request_byte
          proof: registered_pre_emission_cas
      steps:
      - setup
      - request
      - exercise-fault
      - inspect
      allowed_outcome: Остановить request до любого byte exposure; registered proof verifier принимает
        точный head proof; разрешить один replacement.
      requires:
      - registered_model_driver
      - independent_journal_and_transport_observers
    timeout-is-not-proof:
      parameters:
        setup_spec:
          attempt: allocated_via_real_model_attempt_boundary
          lineage: one_original_lineage
          generation: 0
          provider: offline_byte_observer
          replacement_budget: 1
        case_name: timeout-is-not-proof
        case_description: Передать timeout как якобы доказательство no exposure.
        expected_audit:
          replacement_transfers: 0
          proof_verdict: rejected
          selected_generation: 0
          outcome: unknown
        response_semantics: Сообщает только наблюдаемый результат; неизвестное не превращает в успех.
        model_delta: 1
        delivery_delta: 0
        fault_count: 1
        intervention:
          proof_payload: timeout_only
          sequence:
          - original-request-transfer
          - lost-response
          - submit-invalid-proof
      steps:
      - setup
      - request
      - exercise-fault
      - inspect
      allowed_outcome: Передать timeout как якобы доказательство no exposure.
      requires:
      - registered_model_driver
      - independent_journal_and_transport_observers
    stale-proof:
      parameters:
        setup_spec:
          attempt: allocated_via_real_model_attempt_boundary
          lineage: one_original_lineage
          generation: 0
          provider: offline_byte_observer
          replacement_budget: 1
        case_name: stale-proof
        case_description: Валидный по форме proof относится к другому attempt head.
        expected_audit:
          replacement_transfers: 0
          proof_verdict: rejected_wrong_binding
          selected_generation: 0
        response_semantics: Сообщает только наблюдаемый результат; неизвестное не превращает в успех.
        model_delta: 1
        delivery_delta: 0
        fault_count: 1
        intervention:
          proof_attempt_head: different
          sequence:
          - original-request-transfer
          - lost-response
          - submit-invalid-proof
      steps:
      - setup
      - request
      - exercise-fault
      - inspect
      allowed_outcome: Валидный по форме proof относится к другому attempt head.
      requires:
      - registered_model_driver
      - independent_journal_and_transport_observers
    late-old-generation:
      parameters:
        setup_spec:
          attempt: allocated_via_real_model_attempt_boundary
          lineage: one_original_lineage
          generation: 0
          provider: offline_byte_observer
          replacement_budget: 1
        case_name: late-old-generation
        case_description: После законного proof-gated replacement доставить поздний ответ старого
          поколения.
        expected_audit:
          replacement_transfers: 1
          selected_generation: 1
          old_response_selected: false
          old_response_retained_with_provenance: true
          same_lineage: true
        response_semantics: Сообщает только наблюдаемый результат; неизвестное не превращает в успех.
        model_delta: 1
        delivery_delta: 0
        fault_count: 1
        intervention:
          sequence:
          - verified-no-exposure
          - replace
          - late-old-response
      steps:
      - setup
      - request
      - exercise-fault
      - inspect
      allowed_outcome: После законного proof-gated replacement доставить поздний ответ старого поколения.
      requires:
      - registered_model_driver
      - independent_journal_and_transport_observers
    restart-unknown:
      parameters:
        setup_spec:
          attempt: allocated_via_real_model_attempt_boundary
          lineage: one_original_lineage
          generation: 0
          provider: offline_byte_observer
          replacement_budget: 1
        case_name: restart-unknown
        case_description: После possible emission открыть то же хранилище и выполнить четыре worker
          ticks.
        expected_audit:
          replacement_transfers: 0
          attempt_identity_unchanged: true
          selected_generation: 0
          budget_not_reset: true
          outcome: unknown
        response_semantics: Сообщает только наблюдаемый результат; неизвестное не превращает в успех.
        model_delta: 1
        delivery_delta: 0
        fault_count: 1
        intervention:
          sequence:
          - possible-emission
          - lost-response
          - restart
      steps:
      - setup
      - request
      - exercise-fault
      - inspect
      allowed_outcome: После possible emission открыть то же хранилище и выполнить четыре worker
        ticks.
      requires:
      - registered_model_driver
      - independent_journal_and_transport_observers
  requirements:
    grounding: 'R14.5 / A108: пять состояний ModelCallAttempt, proof-gated replacement и fresh selector
      generation. Сопоставление draft labels с точными enum/схемой обязательно перед исполнением.'
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
    boundary: mech14_17.model.exercise
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
    operation: mech14_17.model.setup
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
    operation: mech14_17.model.observed
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

**Пользователь · request:** Собери краткий план отчёта по этим материалам.

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
    operation: mech14_17.model.request
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
    operation: mech14_17.model.observed
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
    operation: mech14_17.model.exercise
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
    operation: mech14_17.model.observed
    count: 1
    same_call_as: action
    fields:
      subject: $mechanism_subject
      completed_registered_case: $case_name
```

## Независимая проверка и ответ

**Chiplog · answer · пример:** План пока не готов: ответ не пришёл. Не могу сказать, завершилась ли обработка запроса.

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
    operation: mech14_17.model.inspect
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
    operation: mech14_17.model.observed
    count: 1
    same_call_as: audit
    fields:
      baseline: $mechanism_baseline
      subject: $mechanism_subject
  state:
    mech14_17.model.audit:
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

Этот набор не доказывает весь пятисостоянийный lifecycle и качество текста модели. Нужна полная таблица переходов R14.5. Byte observer независим от локального send marker; ноль локальных receipts не является no-exposure proof.

Все нули относятся к конечному trace от setup до audit с четырьмя worker ticks. Они не доказывают вечное отсутствие повторов. Без зарегистрированных driver, observer и fault mappings каждый вариант остаётся NOT_RUNNABLE; успешная компиляция не является runtime PASS.
