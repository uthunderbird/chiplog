# Работу продолжил другой исполнитель

**Статус: design · format v2 · stateful profile v2 · component history.** Не запись прогона.

Естественная реплика задаёт мотив истории. Описанный разрез готовит её на offline source и проверяет owner/broker механизм; понимание реплики моделью и полный production loop здесь ещё не подключены. Пример ответа — цель будущей интеграции, не наблюдение component-прогона.
[Общие bindings](mechanisms-common.md) · [План](../../../R14-R17-TRANSCRIPT-PLAN.md).

> **User story.** После смены исполнителя сохранить одну работу и не дать старому worker дописать результат.

R14.3–4 + R15.3 / A40, A43, A47, A51, A53, A58, A99: lease, generation, stable lineage и physical epoch rollover. Один успешный takeover не закрывает A99.

## Варианты

| Вариант | Событие среды и проверяемый исход |
|---|---|
| expiry-only | Двигать clock за expiry без законного takeover и дать old-worker submit. |
| takeover-before-submit | Законный takeover коммитится раньше submit старого worker. |
| submit-before-takeover | Сначала законный old submit, затем takeover и exact replay результата. |
| wrong-fence | New-worker подаёт верный payload с чужим root/generation fence. |
| exhaustion | Установить счётчик через документированную boundary factory на uint64 max; попытаться инкремент без rollover. |
| rollover | Journal-decided authenticated rollover, затем старый epoch submit и duplicate rollover. |

```yaml transcript
scenario:
  format_version: 2
  protocol_version: 2
  id: p06-takeover
  version: 2
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
    expiry-only:
      parameters:
        setup_spec:
          lineage: created_by_real_scheduler_owner
          workers:
          - old-worker
          - new-worker
          lease: old-worker-current
          read_only_budget_initial: 2
          read_only_budget_consumed: 1
          dispatch: held
          authority: original_binding
          lease_expiry: '2026-09-20T09:00:30+05:00'
          initial_clock: '2026-09-20T09:00:00+05:00'
          registered_max_lease_duration_seconds: 60
        case_name: expiry-only
        case_description: Двигать clock за expiry без законного takeover и дать old-worker submit.
        expected_audit:
          old_worker_commit: rejected
          new_worker_authority: false
          new_runs: 0
          stable_lineage_unchanged: true
          remaining_budget: 1
        response_semantics: Сообщает только наблюдаемый результат; неизвестное не превращает в успех.
        model_delta: 0
        delivery_delta: 0
        fault_count: 1
        intervention:
          advance_clock_past_expiry: true
          clock_after: '2026-09-20T09:00:31+05:00'
        setup_audit:
          lease_kind: HELD
          trusted_expiry: '2026-09-20T09:00:30+05:00'
          current_clock: '2026-09-20T09:00:00+05:00'
      steps:
      - setup
      - request
      - exercise-fault
      - inspect
      allowed_outcome: Двигать clock за expiry без законного takeover и дать old-worker submit.
      requires:
      - registered_takeover_driver
      - independent_journal_and_transport_observers
    takeover-before-submit:
      parameters:
        setup_spec:
          lineage: created_by_real_scheduler_owner
          workers:
          - old-worker
          - new-worker
          lease: old-worker-current
          read_only_budget_initial: 2
          read_only_budget_consumed: 1
          dispatch: held
          authority: original_binding
          lease_expiry: '2026-09-20T09:00:30+05:00'
          initial_clock: '2026-09-20T09:00:00+05:00'
          registered_max_lease_duration_seconds: 60
        case_name: takeover-before-submit
        case_description: Законный takeover коммитится раньше submit старого worker.
        expected_audit:
          old_worker_commit: rejected
          new_worker_commit: accepted
          current_worker: new-worker
          stable_lineage_unchanged: true
          remaining_budget: 1
          takeover_clock: '2026-09-20T09:00:31+05:00'
          takeover_positive_expiry_proof_verified: true
          takeover_proof_bound_to_exact_submission_and_current_fence: true
        response_semantics: Сообщает только наблюдаемый результат; неизвестное не превращает в успех.
        model_delta: 0
        delivery_delta: 0
        fault_count: 1
        intervention:
          timeline:
          - act: advance_registered_clock
            to: '2026-09-20T09:00:31+05:00'
          - act: issue_exact_takeover_proof
            requirements:
              issuer: registered_clock_and_authenticated_holder_boundary
              exact_bindings:
              - command_id
              - command_payload_fingerprint
              - current_fence_fingerprint
              - submission_id
              - clock_contract_version
              require_positive_expiry: true
              proposed_lease_identity: fresh_from_factory
              proposed_expiry: '2026-09-20T09:01:00+05:00'
              registered_max_lease_duration_seconds: 60
          - act: publish_takeover
            require_issued_now_at_or_after_old_expiry: true
            generation: observed_generation_plus_one
          - act: old-submit
            use: captured_old_fence
          - act: new-submit
            use: actual_new_lease_fence
          worker_ticks_do_not_advance_clock: true
        setup_audit:
          lease_kind: HELD
          trusted_expiry: '2026-09-20T09:00:30+05:00'
          current_clock: '2026-09-20T09:00:00+05:00'
      steps:
      - setup
      - request
      - exercise-fault
      - inspect
      allowed_outcome: Законный takeover коммитится раньше submit старого worker.
      requires:
      - registered_takeover_driver
      - independent_journal_and_transport_observers
    submit-before-takeover:
      parameters:
        setup_spec:
          lineage: created_by_real_scheduler_owner
          workers:
          - old-worker
          - new-worker
          lease: old-worker-current
          read_only_budget_initial: 2
          read_only_budget_consumed: 1
          dispatch: held
          authority: original_binding
          lease_expiry: '2026-09-20T09:00:30+05:00'
          initial_clock: '2026-09-20T09:00:00+05:00'
          registered_max_lease_duration_seconds: 60
        case_name: submit-before-takeover
        case_description: Сначала законный old submit, затем takeover и exact replay результата.
        expected_audit:
          old_worker_commit: accepted_once
          new_worker_replay: historical_result
          rival_publications: 0
          stable_lineage_unchanged: true
          remaining_budget: 1
          takeover_clock: '2026-09-20T09:00:31+05:00'
          takeover_positive_expiry_proof_verified: true
          takeover_proof_bound_to_exact_submission_and_current_fence: true
          old_submit_clock: '2026-09-20T09:00:10+05:00'
        response_semantics: Сообщает только наблюдаемый результат; неизвестное не превращает в успех.
        model_delta: 0
        delivery_delta: 0
        fault_count: 1
        intervention:
          timeline:
          - act: advance_registered_clock
            to: '2026-09-20T09:00:10+05:00'
          - act: old-submit
            require_current_lease_live: true
            capture_exact_accepted_command_and_result: true
            publication_keeps_run_nonterminal: true
          - act: advance_registered_clock
            to: '2026-09-20T09:00:31+05:00'
          - act: issue_exact_takeover_proof
            requirements:
              issuer: registered_clock_and_authenticated_holder_boundary
              exact_bindings:
              - command_id
              - command_payload_fingerprint
              - current_fence_fingerprint
              - submission_id
              - clock_contract_version
              require_positive_expiry: true
              proposed_lease_identity: fresh_from_factory
              proposed_expiry: '2026-09-20T09:01:00+05:00'
              registered_max_lease_duration_seconds: 60
          - act: publish_takeover
            require_issued_now_at_or_after_old_expiry: true
            generation: observed_generation_plus_one
          - act: exact-replay
            use: captured_old_accepted_command_bytes
            expect: captured_historical_result
          worker_ticks_do_not_advance_clock: true
        setup_audit:
          lease_kind: HELD
          trusted_expiry: '2026-09-20T09:00:30+05:00'
          current_clock: '2026-09-20T09:00:00+05:00'
      steps:
      - setup
      - request
      - exercise-fault
      - inspect
      allowed_outcome: Сначала законный old submit, затем takeover и exact replay результата.
      requires:
      - registered_takeover_driver
      - independent_journal_and_transport_observers
    wrong-fence:
      parameters:
        setup_spec:
          lineage: created_by_real_scheduler_owner
          workers:
          - old-worker
          - new-worker
          lease: old-worker-current
          read_only_budget_initial: 2
          read_only_budget_consumed: 1
          dispatch: held
          authority: original_binding
          lease_expiry: '2026-09-20T09:00:30+05:00'
          initial_clock: '2026-09-20T09:00:00+05:00'
          registered_max_lease_duration_seconds: 60
        case_name: wrong-fence
        case_description: New-worker подаёт верный payload с чужим root/generation fence.
        expected_audit:
          new_worker_commit: rejected
          rival_publications: 0
          stable_lineage_unchanged: true
        response_semantics: Сообщает только наблюдаемый результат; неизвестное не превращает в успех.
        model_delta: 0
        delivery_delta: 0
        fault_count: 1
        intervention:
          replace_fence: different_root_or_generation
        setup_audit:
          lease_kind: HELD
          trusted_expiry: '2026-09-20T09:00:30+05:00'
          current_clock: '2026-09-20T09:00:00+05:00'
      steps:
      - setup
      - request
      - exercise-fault
      - inspect
      allowed_outcome: New-worker подаёт верный payload с чужим root/generation fence.
      requires:
      - registered_takeover_driver
      - independent_journal_and_transport_observers
    exhaustion:
      parameters:
        setup_spec:
          lineage: created_by_real_scheduler_owner
          workers:
          - old-worker
          - new-worker
          lease: old-worker-current
          read_only_budget_initial: 2
          read_only_budget_consumed: 1
          dispatch: held
          authority: original_binding
          lease_expiry: '2026-09-20T09:00:30+05:00'
          initial_clock: '2026-09-20T09:00:00+05:00'
          registered_max_lease_duration_seconds: 60
        case_name: exhaustion
        case_description: Установить счётчик через документированную boundary factory на uint64 max;
          попытаться инкремент без rollover.
        expected_audit:
          counter_wraps: 0
          counter_resets: 0
          publication: held
          physical_epoch_unchanged: true
        response_semantics: Сообщает только наблюдаемый результат; неизвестное не превращает в успех.
        model_delta: 0
        delivery_delta: 0
        fault_count: 1
        intervention:
          counter_at_uint64_max: true
          attempt_plain_increment: true
        setup_audit:
          lease_kind: HELD
          trusted_expiry: '2026-09-20T09:00:30+05:00'
          current_clock: '2026-09-20T09:00:00+05:00'
      steps:
      - setup
      - request
      - exercise-fault
      - inspect
      allowed_outcome: Установить счётчик через документированную boundary factory на uint64 max;
        попытаться инкремент без rollover.
      requires:
      - registered_takeover_driver
      - independent_journal_and_transport_observers
    rollover:
      parameters:
        setup_spec:
          lineage: created_by_real_scheduler_owner
          workers:
          - old-worker
          - new-worker
          lease: maximum_generation_held_history_via_registered_boundary_factory
          read_only_budget_initial: 2
          read_only_budget_consumed: 1
          dispatch: held
          authority: original_binding
          lease_expiry: '2026-09-20T09:00:30+05:00'
          initial_clock: '2026-09-20T09:00:00+05:00'
          registered_max_lease_duration_seconds: 60
          held_generation: 18446744073709551615
          physical_selector_version: 0
          prepare_sequence:
          - act: materialize_maximum_generation_held_history
            via: registered_boundary_history_factory
            verify: owner_journal_history_contains_exact_held_lease_and_generation
            no_direct_hold_or_rollover_dto_injection: true
          - act: advance_registered_clock
            to: '2026-09-20T09:00:31+05:00'
          - act: issue_exact_takeover_proof
            requirements:
              issuer: registered_clock_and_authenticated_holder_boundary
              exact_bindings:
              - command_id
              - command_payload_fingerprint
              - current_fence_fingerprint
              - submission_id
              - clock_contract_version
              require_positive_expiry: true
              proposed_lease_identity: fresh_from_factory
              proposed_expiry: '2026-09-20T09:01:00+05:00'
              registered_max_lease_duration_seconds: 60
          - act: publish_generation_exhaustion
            via: real_scheduler_lease_owner_and_broker
            proposed_generation: 18446744073709551615
            require_committed_kind: GENERATION_EXHAUSTED_HOLD
          - act: capture_setup_baseline
            after: durable_exhaustion_publication
            include:
            - stable_lineage
            - physical_root_and_selector
            - exhaustion_hold_head
            - exhausted_command_id
            - preceding_held_lease
            - authority_epoch
            - trusted_expiry
            - complete_used_epoch_and_lease_history
            - original_run_and_authority
            - remaining_budget
            - external_journal_counters
        case_name: rollover
        case_description: Journal-decided authenticated rollover, затем старый epoch submit и duplicate
          rollover.
        expected_audit:
          current_physical_epochs: 1
          stable_lineage_unchanged: true
          original_run_unchanged: true
          original_authority_unchanged: true
          old_epoch_commit: rejected
          duplicate_rollover: replay
          remaining_budget: 1
          baseline_lease_kind: GENERATION_EXHAUSTED_HOLD
          baseline_generation: 18446744073709551615
          baseline_exhaustion_head: $mechanism_lease_snapshot.lease_head
          new_rollover_decisions: 1
          successor_lease_kind: UNLEASED
          successor_generation: 0
          successor_selector_version: 1
          original_exhaustion_history_unchanged: true
        response_semantics: Сообщает только наблюдаемый результат; неизвестное не превращает в успех.
        model_delta: 0
        delivery_delta: 0
        fault_count: 1
        intervention:
          baseline_exhausted_snapshot: $mechanism_lease_snapshot
          sequence:
          - act: issue_registered_rollover_authority
            bind:
            - exact_snapshot_fingerprint
            - exact_exhaustion_tuple
            - command_payload_fingerprint
            - submission_id
            - authority_epoch
            - predecessor_rollover
          - act: publish_authenticated_rollover
            via: real_scheduler_owner_and_broker
            require_baseline_kind: GENERATION_EXHAUSTED_HOLD
          - act: old-epoch-submit
            fence: captured_pre_rollover_epoch
          - act: exact-rollover-replay
            bytes: captured_accepted_rollover_command
        setup_audit:
          lease_kind: GENERATION_EXHAUSTED_HOLD
          generation: 18446744073709551615
          preceding_held_generation: 18446744073709551615
          trusted_expiry: '2026-09-20T09:00:30+05:00'
          current_clock: '2026-09-20T09:00:31+05:00'
          durable_exhaustion_publications: 1
          hold_tuple_matches_committed_preceding_lease: true
      steps:
      - setup
      - request
      - exercise-fault
      - inspect
      allowed_outcome: Journal-decided authenticated rollover, затем старый epoch submit и duplicate
        rollover.
      requires:
      - registered_takeover_driver
      - independent_journal_and_transport_observers
  requirements:
    grounding: 'R14.3–4 + R15.3 / A40, A43, A47, A51, A53, A58, A99: lease, generation, stable lineage
      и physical epoch rollover. Один успешный takeover не закрывает A99.'
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
    takeover_clock: Four worker ticks are a bounded polling budget, not time passage. Use the variant
      timeline and independently observed registered clock proofs; takeover at 09:00:31 follows expiry
      at 09:00:30. Old submit in submit-first commits at 09:00:10 before advancing time.
    rollover_baseline: Rollover starts only after the setup driver publishes GENERATION_EXHAUSTED_HOLD
      through the real lease owner/broker from HELD UINT64_MAX and captures the exact committed exhaustion
      tuple. Missing maximum-generation history factory or proof issuer is NOT_RUNNABLE, not permission
      to inject a successful hold.
  faults:
  - id: mech-intervention
    boundary: mech14_17.takeover.exercise
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
    operation: mech14_17.takeover.setup
    arguments:
      spec: $setup_spec
      case: $case_name
    count: 1
    result:
      source: real_boundary
      capture:
        mechanism_baseline: observed_baseline
        mechanism_subject: subject_binding
        mechanism_lease_snapshot: actual_committed_lease_snapshot
      fields:
        setup_audit: $setup_audit
  events:
  - id: setup.observed
    operation: mech14_17.takeover.observed
    count: 1
    same_call_as: setup.owner
    fields:
      cutoff: before_intervention
      baseline: $mechanism_baseline
      lease_snapshot: $mechanism_lease_snapshot
  state:
    mech14_17.external_effects:
      delta: 0
```

В takeover-first clock явно сдвигается на 09:00:31 до выдачи proof. В submit-first старый submit коммитится в 09:00:10, и лишь затем clock сдвигается за expiry 09:00:30. Proof выдаётся зарегистрированной clock boundary и связывается с exact command/fence/submission; одного authored timestamp недостаточно. Четыре ticks сами clock не двигают.

В rollover baseline снимается после durable `GENERATION_EXHAUSTED_HOLD`: factory готовит проверяемую историю `HELD` с generation `UINT64_MAX`, затем настоящий takeover reducer/broker публикует exhaustion при доказанном expiry. Захватываются preceding lease, exhaustion head/command, authority epoch и physical selector. Rollover использует именно этот snapshot, а не обычный `HELD` из остальных вариантов.

## Реплика владельца

**Пользователь · request:** Проверка зависла? Можешь продолжить?

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
    operation: mech14_17.takeover.request
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
    operation: mech14_17.takeover.observed
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
    operation: mech14_17.takeover.exercise
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
    operation: mech14_17.takeover.observed
    count: 1
    same_call_as: action
    fields:
      subject: $mechanism_subject
      completed_registered_case: $case_name
```

## Независимая проверка и ответ

**Chiplog · answer · пример:** Да, продолжаю с того места, где остановился.

Пример относится к ветке takeover-before-submit; target semantics записана в variant.parameters.response_semantics. Component-разрез не вызывает модель ради ответа. Проверка реальной реплики потребует отдельного production-loop/renderer binding; до него полный разговор не получает PASS.

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
    operation: mech14_17.takeover.inspect
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
    operation: mech14_17.takeover.observed
    count: 1
    same_call_as: audit
    fields:
      baseline: $mechanism_baseline
      subject: $mechanism_subject
  state:
    mech14_17.takeover.audit:
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

Factory не вставляет принятое lease или rollover решение: получает его через owner/broker. Граничный counter setup должен иметь отдельный зарегистрированный binding. Все writer paths R14–R17 и pre-root/live/post-terminal/rollover variants требуют двунаправленной A99 inventory, а не расширения списка этих реплик.

Все нули относятся к конечному trace от setup до audit с четырьмя worker ticks. Они не доказывают вечное отсутствие повторов. Без зарегистрированных driver, observer и fault mappings каждый вариант остаётся NOT_RUNNABLE; успешная компиляция не является runtime PASS.
