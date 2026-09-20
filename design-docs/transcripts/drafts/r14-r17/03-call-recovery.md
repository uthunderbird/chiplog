# Выполнение прервалось

**Статус: design · format v2 · stateful profile v2 · component history.** Не запись прогона.

Естественная реплика задаёт мотив истории. Описанный разрез готовит её на offline source и проверяет owner/broker механизм; понимание реплики моделью и полный production loop здесь ещё не подключены. Пример ответа — цель будущей интеграции, не наблюдение component-прогона.
[Общие bindings](mechanisms-common.md) · [План](../../../R14-R17-TRANSCRIPT-PLAN.md).

> **User story.** После сбоя понять, какие части работы завершились и можно ли продолжить.

R14.1–4: sealed fan-out, acceptance branches, resolver closure и current semantic reduction; A15, A38–A47, A49–A52, A54–A57, A59. R14 recovery_domain.py даёт места проверки accounting/continuation, но не готовый driver.

## Варианты

| Вариант | Событие среды и проверяемый исход |
|---|---|
| partial-fanout | Одна из двух принятых read-only проверок завершена; вторая теряет ответ. |
| omitted-member | Второй член выкинут из предложенного recovery manifest при прежнем sealed head. |
| cancel-before-accept | Отмена выигрывает serial order перед acceptance второго вызова. |
| cancel-after-accept | Acceptance выигрывает; отмена не стирает результат/obligation принятого вызова. |
| budget-across-successor | Потратить две read-only попытки через resume и successor, перезапустить store и попытаться третью. |
| raw-before-closure | Приходит raw evidence, но resolver ещё не закрыл исходное obligation. |
| closed-current-reduction | Resolver закрывает исходный read; оба вызова accounted, consumer читает current reduction. |

```yaml transcript
scenario:
  format_version: 2
  protocol_version: 2
  id: p03-call-recovery
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
    partial-fanout:
      parameters:
        setup_spec:
          sealed_calls:
          - read-source-a
          - read-source-b
          first_call: accepted_and_terminal
          second_call: accepted_read_only_pending
          read_only_budget: 2
          allocate_via: real_broker_and_call_owner
          initial_continuation_ready: false
        case_name: partial-fanout
        case_description: Одна из двух принятых read-only проверок завершена; вторая теряет ответ.
        expected_audit:
          manifest:
          - read-source-a
          - read-source-b
          branches:
          - TERMINAL
          - READ_ONLY_RETRY_PENDING
          continuation_ready: false
          obligation_stream: original
          remaining_budget: 2
        response_semantics: Сообщает только наблюдаемый результат; неизвестное не превращает в успех.
        model_delta: 0
        delivery_delta: 0
        fault_count: 1
        intervention:
          lose_response_for: read-source-b
      steps:
      - setup
      - request
      - exercise-fault
      - inspect
      allowed_outcome: Одна из двух принятых read-only проверок завершена; вторая теряет ответ.
      requires:
      - registered_recovery_driver
      - independent_journal_and_transport_observers
    omitted-member:
      parameters:
        setup_spec:
          sealed_calls:
          - read-source-a
          - read-source-b
          first_call: accepted_and_terminal
          second_call: accepted_read_only_pending
          read_only_budget: 2
          allocate_via: real_broker_and_call_owner
          initial_continuation_ready: false
        case_name: omitted-member
        case_description: Второй член выкинут из предложенного recovery manifest при прежнем sealed
          head.
        expected_audit:
          decision: typed_rejection
          original_manifest_unchanged: true
          continuation_ready: false
        response_semantics: Сообщает только наблюдаемый результат; неизвестное не превращает в успех.
        model_delta: 0
        delivery_delta: 0
        fault_count: 1
        intervention:
          omit_manifest_member: read-source-b
      steps:
      - setup
      - request
      - exercise-fault
      - inspect
      allowed_outcome: Второй член выкинут из предложенного recovery manifest при прежнем sealed
        head.
      requires:
      - registered_recovery_driver
      - independent_journal_and_transport_observers
    cancel-before-accept:
      parameters:
        setup_spec:
          sealed_calls:
          - read-source-a
          - read-source-b
          first_call: accepted_and_terminal
          second_call: initialized_not_yet_accepted
          read_only_budget: 2
          allocate_via: real_broker_and_call_owner
          initial_continuation_ready: false
        case_name: cancel-before-accept
        case_description: Отмена выигрывает serial order перед acceptance второго вызова.
        expected_audit:
          second_call_branch: cancelled_before_acceptance
          second_call_acceptance_count: 0
          second_call_intents: 0
          manifest_cardinality: 2
        response_semantics: Сообщает только наблюдаемый результат; неизвестное не превращает в успех.
        model_delta: 0
        delivery_delta: 0
        fault_count: 1
        intervention:
          serial_order:
          - cancel-second
          - accept-second
      steps:
      - setup
      - request
      - exercise-fault
      - inspect
      allowed_outcome: Отмена выигрывает serial order перед acceptance второго вызова.
      requires:
      - registered_recovery_driver
      - independent_journal_and_transport_observers
    cancel-after-accept:
      parameters:
        setup_spec:
          sealed_calls:
          - read-source-a
          - read-source-b
          first_call: accepted_and_terminal
          second_call: initialized_not_yet_accepted
          read_only_budget: 2
          allocate_via: real_broker_and_call_owner
          initial_continuation_ready: false
        case_name: cancel-after-accept
        case_description: Acceptance выигрывает; отмена не стирает результат/obligation принятого
          вызова.
        expected_audit:
          second_call_branch: accepted_with_original_obligation
          second_call_acceptance_count: 1
          second_call_intents_unchanged: true
          continuation_ready: false
        response_semantics: Сообщает только наблюдаемый результат; неизвестное не превращает в успех.
        model_delta: 0
        delivery_delta: 0
        fault_count: 1
        intervention:
          serial_order:
          - accept-second
          - cancel-second
      steps:
      - setup
      - request
      - exercise-fault
      - inspect
      allowed_outcome: Acceptance выигрывает; отмена не стирает результат/obligation принятого вызова.
      requires:
      - registered_recovery_driver
      - independent_journal_and_transport_observers
    budget-across-successor:
      parameters:
        setup_spec:
          sealed_calls:
          - read-source-a
          - read-source-b
          first_call: accepted_and_terminal
          second_call: accepted_read_only_pending
          read_only_budget: 2
          allocate_via: real_broker_and_call_owner
          initial_continuation_ready: false
        case_name: budget-across-successor
        case_description: Потратить две read-only попытки через resume и successor, перезапустить
          store и попытаться третью.
        expected_audit:
          read_only_attempts_total: 2
          remaining_budget: 0
          third_attempt: denied
          obligation_stream: original
          continuation_ready: false
        response_semantics: Сообщает только наблюдаемый результат; неизвестное не превращает в успех.
        model_delta: 0
        delivery_delta: 0
        fault_count: 1
        intervention:
          sequence:
          - read-only-retry
          - successor
          - read-only-retry
          - restart
          - third-retry
      steps:
      - setup
      - request
      - exercise-fault
      - inspect
      allowed_outcome: Потратить две read-only попытки через resume и successor, перезапустить store
        и попытаться третью.
      requires:
      - registered_recovery_driver
      - independent_journal_and_transport_observers
    raw-before-closure:
      parameters:
        setup_spec:
          sealed_calls:
          - read-source-a
          - read-source-b
          first_call: accepted_and_terminal
          second_call: accepted_read_only_pending
          read_only_budget: 2
          allocate_via: real_broker_and_call_owner
          initial_continuation_ready: false
        case_name: raw-before-closure
        case_description: Приходит raw evidence, но resolver ещё не закрыл исходное obligation.
        expected_audit:
          raw_evidence_count: 1
          resolver_closure_count: 0
          terminal_accounting_is_not_continuation: true
          continuation_ready: false
        response_semantics: Сообщает только наблюдаемый результат; неизвестное не превращает в успех.
        model_delta: 0
        delivery_delta: 0
        fault_count: 1
        intervention:
          pause_after: raw_evidence_append
      steps:
      - setup
      - request
      - exercise-fault
      - inspect
      allowed_outcome: Приходит raw evidence, но resolver ещё не закрыл исходное obligation.
      requires:
      - registered_recovery_driver
      - independent_journal_and_transport_observers
    closed-current-reduction:
      parameters:
        setup_spec:
          sealed_calls:
          - read-source-a
          - read-source-b
          first_call: accepted_and_terminal
          second_call: accepted_read_only_pending
          read_only_budget: 2
          allocate_via: real_broker_and_call_owner
          initial_continuation_ready: false
        case_name: closed-current-reduction
        case_description: Resolver закрывает исходный read; оба вызова accounted, consumer читает
          current reduction.
        expected_audit:
          manifest:
          - read-source-a
          - read-source-b
          terminal_calls: 2
          resolver_closure_count: 1
          continuation_ready: true
          consumer_reduction: current_exact_head
        response_semantics: Подтверждает завершение двух проверок и продолжает только после closure
          и актуального reduction.
        model_delta: 0
        delivery_delta: 0
        fault_count: 0
        intervention:
          drive: ordinary_registered_boundary
      steps:
      - setup
      - request
      - exercise
      - inspect
      allowed_outcome: Resolver закрывает исходный read; оба вызова accounted, consumer читает current
        reduction.
      requires:
      - registered_recovery_driver
      - independent_journal_and_transport_observers
  requirements:
    grounding: 'R14.1–4: sealed fan-out, acceptance branches, resolver closure и current semantic
      reduction; A15, A38–A47, A49–A52, A54–A57, A59. R14 recovery_domain.py даёт места проверки
      accounting/continuation, но не готовый driver.'
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
    boundary: mech14_17.recovery.exercise
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
    operation: mech14_17.recovery.setup
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
    operation: mech14_17.recovery.observed
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

**Пользователь · request:** Ты уже проверил материалы для отчёта? Продолжай с того места, где остановился.

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
    operation: mech14_17.recovery.request
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
    operation: mech14_17.recovery.observed
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
    operation: mech14_17.recovery.exercise
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
    operation: mech14_17.recovery.observed
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
    operation: mech14_17.recovery.exercise
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
    operation: mech14_17.recovery.observed
    count: 1
    same_call_as: action
    fields:
      subject: $mechanism_subject
      completed_registered_case: $case_name
```

## Независимая проверка и ответ

**Chiplog · answer · пример:** Одна проверка завершилась, по второй результата пока нет. Продолжить сборку отчёта сейчас не могу.

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
    operation: mech14_17.recovery.inspect
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
    operation: mech14_17.recovery.observed
    count: 1
    same_call_as: audit
    fields:
      baseline: $mechanism_baseline
      subject: $mechanism_subject
  state:
    mech14_17.recovery.audit:
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

Пары omission/addition/substitution/duplicate/reorder, rival evidence и все варианты writer registry остаются отдельной технической матрицей R14. Здесь membership и accounting наблюдаются независимо от видимого ответа.

Все нули относятся к конечному trace от setup до audit с четырьмя worker ticks. Они не доказывают вечное отсутствие повторов. Без зарегистрированных driver, observer и fault mappings каждый вариант остаётся NOT_RUNNABLE; успешная компиляция не является runtime PASS.
