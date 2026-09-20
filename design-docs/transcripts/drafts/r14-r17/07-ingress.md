# Сообщение пришло повторно или во время сбоя

**Статус: design · format v2 · stateful profile v2 · component history.** Не запись прогона.

Естественная реплика задаёт мотив истории. Описанный разрез готовит её на offline source и проверяет owner/broker механизм; понимание реплики моделью и полный production loop здесь ещё не подключены. Пример ответа — цель будущей интеграции, не наблюдение component-прогона.
[Общие bindings](mechanisms-common.md) · [План](../../../R14-R17-TRANSCRIPT-PLAN.md).

> **User story.** Сохранить принятую просьбу при сбое канала и не исполнить повторное сообщение дважды.

R17.1–2/4 / A19, A28, A100–A104: exact raw custody, receipt token, ack/cursor, FIFO/deadline и quarantine. Прохождение одного source не заменяет source-specific ingress paths.

## Варианты

| Вариант | Событие среды и проверяемый исход |
|---|---|
| cli-duplicate | CLI intake сохраняет bytes; тот же аутентифицированный token приходит повторно. |
| telegram-push-crash | Push crash после custody commit до ack; повторить update. |
| telegram-poll-page | Poll page из двух updates; crash до custody второго. |
| destructive-read | Деструктивный read пересекает необратимую границу, bytes потеряны до CAS. |
| quarantine | Аутентифицированные useful bytes имеют ошибочный parse; затем новая parser version. |
| backlog-rebase | FIFO prefix блокируется; заполнить обычную ёмкость, restart и drain. |
| forged-witness | Telegram update с неверной аутентификацией source witness. |

```yaml transcript
scenario:
  format_version: 2
  protocol_version: 2
  id: p07-ingress
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
    cli-duplicate:
      parameters:
        setup_spec:
          raw_message: Добавь в планы подготовку отчёта.
          raw_encoding: utf-8
          transport_authentication: offline_registered_source_witness
          adoption: null
          initial_queue: empty
          ready_capacity: 2
          reserve_capacity: 1
          user_message_role: motivation_only_for_non_message_ingress
        case_name: cli-duplicate
        case_description: CLI intake сохраняет bytes; тот же аутентифицированный token приходит повторно.
        expected_audit:
          source: cli
          custody_records: 1
          admitted_messages: 1
          raw_bytes_exact: true
          ack_after_custody: true
        response_semantics: Сообщает только наблюдаемый результат; неизвестное не превращает в успех.
        model_delta: 0
        delivery_delta: 0
        fault_count: 1
        intervention:
          source_driver: cli
          sequence:
          - commit-custody
          - duplicate-token
      steps:
      - setup
      - request
      - exercise-fault
      - inspect
      allowed_outcome: CLI intake сохраняет bytes; тот же аутентифицированный token приходит повторно.
      requires:
      - registered_ingress_driver
      - independent_journal_and_transport_observers
    telegram-push-crash:
      parameters:
        setup_spec:
          raw_message: Добавь в планы подготовку отчёта.
          raw_encoding: utf-8
          transport_authentication: offline_registered_source_witness
          adoption: null
          initial_queue: empty
          ready_capacity: 2
          reserve_capacity: 1
          user_message_role: motivation_only_for_non_message_ingress
        case_name: telegram-push-crash
        case_description: Push crash после custody commit до ack; повторить update.
        expected_audit:
          source: telegram-push
          custody_records: 1
          admitted_messages: 1
          ack_after_custody: true
          raw_bytes_exact: true
        response_semantics: Сообщает только наблюдаемый результат; неизвестное не превращает в успех.
        model_delta: 0
        delivery_delta: 0
        fault_count: 1
        intervention:
          source_driver: telegram-push
          sequence:
          - commit-custody
          - crash-before-ack
          - restart
          - redelivery
      steps:
      - setup
      - request
      - exercise-fault
      - inspect
      allowed_outcome: Push crash после custody commit до ack; повторить update.
      requires:
      - registered_ingress_driver
      - independent_journal_and_transport_observers
    telegram-poll-page:
      parameters:
        setup_spec:
          raw_message: Добавь в планы подготовку отчёта.
          raw_encoding: utf-8
          transport_authentication: offline_registered_source_witness
          adoption: null
          initial_queue: empty
          ready_capacity: 2
          reserve_capacity: 1
          user_message_role: motivation_only_for_non_message_ingress
        case_name: telegram-poll-page
        case_description: Poll page из двух updates; crash до custody второго.
        expected_audit:
          source: telegram-poll
          page_members: 2
          cursor_past_unheld_member: false
          missing_bytes_have_loss_obligation: true
          first_member_admitted_once: true
        response_semantics: Сообщает только наблюдаемый результат; неизвестное не превращает в успех.
        model_delta: 0
        delivery_delta: 0
        fault_count: 1
        intervention:
          source_driver: telegram-poll
          page_size: 2
          crash_before_custody_member: 2
      steps:
      - setup
      - request
      - exercise-fault
      - inspect
      allowed_outcome: Poll page из двух updates; crash до custody второго.
      requires:
      - registered_ingress_driver
      - independent_journal_and_transport_observers
    destructive-read:
      parameters:
        setup_spec:
          raw_message: Добавь в планы подготовку отчёта.
          raw_encoding: utf-8
          transport_authentication: offline_registered_source_witness
          adoption: null
          initial_queue: empty
          ready_capacity: 2
          reserve_capacity: 1
          user_message_role: motivation_only_for_non_message_ingress
          source_raw_bytes_utf8: '{"tool_call":"fixture-read","body":"Материалы готовы"}'
        case_name: destructive-read
        case_description: Деструктивный read пересекает необратимую границу, bytes потеряны до CAS.
        expected_audit:
          loss_slot_preallocated: true
          enumerable_loss_obligations: 1
          false_replayable_receipts: 0
          source_release_has_durable_custody_state: true
        response_semantics: Сообщает только наблюдаемый результат; неизвестное не превращает в успех.
        model_delta: 0
        delivery_delta: 0
        fault_count: 1
        intervention:
          source_driver: destructive-tool-result
          sequence:
          - preallocate-loss-slot
          - irreversible-read
          - lose-raw-before-cas
      steps:
      - setup
      - request
      - exercise-fault
      - inspect
      allowed_outcome: Деструктивный read пересекает необратимую границу, bytes потеряны до CAS.
      requires:
      - registered_ingress_driver
      - independent_journal_and_transport_observers
    quarantine:
      parameters:
        setup_spec:
          raw_message: Добавь в планы подготовку отчёта.
          raw_encoding: utf-8
          transport_authentication: offline_registered_source_witness
          adoption: null
          initial_queue: empty
          ready_capacity: 2
          reserve_capacity: 1
          user_message_role: motivation_only_for_non_message_ingress
          source_raw_bytes_utf8: '{"provider_event_id":"fixture-1","kind":"result-v2","body":"Материалы
            готовы"}'
          parser_versions:
          - rejects-result-v2
          - supports-result-v2
        case_name: quarantine
        case_description: Аутентифицированные useful bytes имеют ошибочный parse; затем новая parser
          version.
        expected_audit:
          custody_bytes_unchanged: true
          quarantine_lineages: 1
          cas_selected_reprocess_successors: 1
          admitted_messages: 1
        response_semantics: Сообщает только наблюдаемый результат; неизвестное не превращает в успех.
        model_delta: 0
        delivery_delta: 0
        fault_count: 1
        intervention:
          source_driver: provider-callback
          sequence:
          - authenticated-malformed-bytes
          - quarantine
          - new-parser
          - reprocess
      steps:
      - setup
      - request
      - exercise-fault
      - inspect
      allowed_outcome: Аутентифицированные useful bytes имеют ошибочный parse; затем новая parser
        version.
      requires:
      - registered_ingress_driver
      - independent_journal_and_transport_observers
    backlog-rebase:
      parameters:
        setup_spec:
          raw_message: Добавь в планы подготовку отчёта.
          raw_encoding: utf-8
          transport_authentication: offline_registered_source_witness
          adoption: null
          initial_queue: empty
          ready_capacity: 2
          reserve_capacity: 1
          user_message_role: motivation_only_for_non_message_ingress
        case_name: backlog-rebase
        case_description: FIFO prefix блокируется; заполнить обычную ёмкость, restart и drain.
        expected_audit:
          ready_generation_fifo_preserved: true
          reserve_borrowed: false
          original_deadlines_preserved: true
          drain_joins_token_custody_fifo_bound: true
        response_semantics: Сообщает только наблюдаемый результат; неизвестное не превращает в успех.
        model_delta: 0
        delivery_delta: 0
        fault_count: 1
        intervention:
          source_driver: cli
          sequence:
          - blocked-prefix
          - fill-ordinary-capacity
          - rebase
          - restart
          - drain
      steps:
      - setup
      - request
      - exercise-fault
      - inspect
      allowed_outcome: FIFO prefix блокируется; заполнить обычную ёмкость, restart и drain.
      requires:
      - registered_ingress_driver
      - independent_journal_and_transport_observers
    forged-witness:
      parameters:
        setup_spec:
          raw_message: Добавь в планы подготовку отчёта.
          raw_encoding: utf-8
          transport_authentication: offline_registered_source_witness
          adoption: null
          initial_queue: empty
          ready_capacity: 2
          reserve_capacity: 1
          user_message_role: motivation_only_for_non_message_ingress
        case_name: forged-witness
        case_description: Telegram update с неверной аутентификацией source witness.
        expected_audit:
          source: telegram-push
          admitted_messages: 0
          forged_witness_accepted: false
        response_semantics: Сообщает только наблюдаемый результат; неизвестное не превращает в успех.
        model_delta: 0
        delivery_delta: 0
        fault_count: 1
        intervention:
          source_driver: telegram-push
          replace_witness: forged
      steps:
      - setup
      - request
      - exercise-fault
      - inspect
      allowed_outcome: Telegram update с неверной аутентификацией source witness.
      requires:
      - registered_ingress_driver
      - independent_journal_and_transport_observers
  requirements:
    grounding: 'R17.1–2/4 / A19, A28, A100–A104: exact raw custody, receipt token, ack/cursor, FIFO/deadline
      и quarantine. Прохождение одного source не заменяет source-specific ingress paths.'
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
    boundary: mech14_17.ingress.exercise
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
    operation: mech14_17.ingress.setup
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
    operation: mech14_17.ingress.observed
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

**Пользователь · request:** Добавь в планы подготовку отчёта.

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
    operation: mech14_17.ingress.request
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
    operation: mech14_17.ingress.observed
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
    operation: mech14_17.ingress.exercise
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
    operation: mech14_17.ingress.observed
    count: 1
    same_call_as: action
    fields:
      subject: $mechanism_subject
      completed_registered_case: $case_name
```

## Независимая проверка и ответ

**Chiplog · answer · пример:** Добавить «Подготовить отчёт» в планы?

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
    operation: mech14_17.ingress.inspect
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
    operation: mech14_17.ingress.observed
    count: 1
    same_call_as: audit
    fields:
      baseline: $mechanism_baseline
      subject: $mechanism_subject
  state:
    mech14_17.ingress.audit:
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

Это семь конкретных историй, не полный EvidenceIngressSurfaceManifest. Telegram push/poll, CLI, provider callback/poll, reconciliation и tool-result должны иметь отдельные зарегистрированные drivers и прогоны общего commit/ack контракта; dashboard ingress пуст. Предложение ещё не даёт authority изменять Plan.

Все нули относятся к конечному trace от setup до audit с четырьмя worker ticks. Они не доказывают вечное отсутствие повторов. Без зарегистрированных driver, observer и fault mappings каждый вариант остаётся NOT_RUNNABLE; успешная компиляция не является runtime PASS.
