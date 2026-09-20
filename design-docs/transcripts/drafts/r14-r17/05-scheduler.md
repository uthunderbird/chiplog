# Наступило время работы

**Статус: design · format v2 · stateful profile v2 · component history.** Не запись прогона.

Естественная реплика задаёт мотив истории. Описанный разрез готовит её на offline source и проверяет owner/broker механизм; понимание реплики моделью и полный production loop здесь ещё не подключены. Пример ответа — цель будущей интеграции, не наблюдение component-прогона.
[Общие bindings](mechanisms-common.md) · [План](../../../R14-R17-TRANSCRIPT-PLAN.md).

> **User story.** Запустить запланированную работу ровно для тех срабатываний, которые входят в проверенный интервал.

R15.1–2 / A17, A18, A98: revision-bound occurrence identity, полный eligible manifest, interval bound, overflow resolution. Пользователь не создаёт расписание через выдуманный tool: factory оформляет его через реальную owner boundary.

## Варианты

| Вариант | Событие среды и проверяемый исход |
|---|---|
| zero | Clock до первого due occurrence. |
| one-individual | Clock после первого, до второго occurrence. |
| two-coalesced | Два eligible occurrence, policy COALESCED. |
| two-skipped | Та же пара с policy SKIPPED. |
| overflow-held | Три due occurrence при limit=2; попытка раннего amendment. |
| resolve-overflow | После overflow разрешить successor bound=4; повторить тот же resolution. |
| omission | Убрать eligible occurrence из publication manifest. |
| crash-coalesce | Crash после atomic coalesce commit; повторная доставка после restart. |

```yaml transcript
scenario:
  format_version: 2
  protocol_version: 2
  id: p05-scheduler
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
    zero:
      parameters:
        setup_spec:
          schedule_definition:
            timezone: Asia/Almaty
            occurrence_times:
            - '2026-09-20T09:15:00+05:00'
            - '2026-09-20T09:45:00+05:00'
          interval_start: '2026-09-20T09:00:00+05:00'
          bound_limit: 2
          admission: real_schedule_owner
          effect_dispatch_disabled: true
        case_name: zero
        case_description: Clock до первого due occurrence.
        expected_audit:
          eligible_count: 0
          dispositions: []
          new_runs: 0
          bound: verified_empty_interval
        response_semantics: Сообщает, что в проверенном интервале срабатываний нет.
        model_delta: 0
        delivery_delta: 0
        fault_count: 0
        intervention:
          drive: ordinary_registered_boundary
        clock_after: '2026-09-20T09:10:00+05:00'
        schedule_policy: COALESCED
      steps:
      - setup
      - request
      - exercise
      - inspect
      allowed_outcome: Clock до первого due occurrence.
      requires:
      - registered_scheduler_driver
      - independent_journal_and_transport_observers
    one-individual:
      parameters:
        setup_spec:
          schedule_definition:
            timezone: Asia/Almaty
            occurrence_times:
            - '2026-09-20T09:15:00+05:00'
            - '2026-09-20T09:45:00+05:00'
          interval_start: '2026-09-20T09:00:00+05:00'
          bound_limit: 2
          admission: real_schedule_owner
          effect_dispatch_disabled: true
        case_name: one-individual
        case_description: Clock после первого, до второго occurrence.
        expected_audit:
          eligible_count: 1
          dispositions:
          - INDIVIDUAL
          new_runs: 1
          all_members_accounted: true
        response_semantics: Показывает один принятый запуск.
        model_delta: 0
        delivery_delta: 0
        fault_count: 0
        intervention:
          drive: ordinary_registered_boundary
        clock_after: '2026-09-20T09:30:00+05:00'
        schedule_policy: INDIVIDUAL
      steps:
      - setup
      - request
      - exercise
      - inspect
      allowed_outcome: Clock после первого, до второго occurrence.
      requires:
      - registered_scheduler_driver
      - independent_journal_and_transport_observers
    two-coalesced:
      parameters:
        setup_spec:
          schedule_definition:
            timezone: Asia/Almaty
            occurrence_times:
            - '2026-09-20T09:15:00+05:00'
            - '2026-09-20T09:45:00+05:00'
          interval_start: '2026-09-20T09:00:00+05:00'
          bound_limit: 2
          admission: real_schedule_owner
          effect_dispatch_disabled: true
        case_name: two-coalesced
        case_description: Два eligible occurrence, policy COALESCED.
        expected_audit:
          eligible_count: 2
          dispositions:
          - COALESCED
          - COALESCED
          new_runs: 1
          aggregate_manifest: both_exact_occurrences_in_canonical_order
          all_members_accounted: true
        response_semantics: Сообщает только наблюдаемый результат; неизвестное не превращает в успех.
        model_delta: 0
        delivery_delta: 0
        fault_count: 0
        intervention:
          drive: ordinary_registered_boundary
        clock_after: '2026-09-20T10:00:00+05:00'
        schedule_policy: COALESCED
      steps:
      - setup
      - request
      - exercise
      - inspect
      allowed_outcome: Два eligible occurrence, policy COALESCED.
      requires:
      - registered_scheduler_driver
      - independent_journal_and_transport_observers
    two-skipped:
      parameters:
        setup_spec:
          schedule_definition:
            timezone: Asia/Almaty
            occurrence_times:
            - '2026-09-20T09:15:00+05:00'
            - '2026-09-20T09:45:00+05:00'
          interval_start: '2026-09-20T09:00:00+05:00'
          bound_limit: 2
          admission: real_schedule_owner
          effect_dispatch_disabled: true
        case_name: two-skipped
        case_description: Та же пара с policy SKIPPED.
        expected_audit:
          eligible_count: 2
          dispositions:
          - SKIPPED
          - SKIPPED
          new_runs: 0
          all_members_accounted: true
        response_semantics: Показывает два пропущенных срабатывания с причиной политики; не сообщает
          о запуске.
        model_delta: 0
        delivery_delta: 0
        fault_count: 0
        intervention:
          drive: ordinary_registered_boundary
        clock_after: '2026-09-20T10:00:00+05:00'
        schedule_policy: SKIPPED
      steps:
      - setup
      - request
      - exercise
      - inspect
      allowed_outcome: Та же пара с policy SKIPPED.
      requires:
      - registered_scheduler_driver
      - independent_journal_and_transport_observers
    overflow-held:
      parameters:
        setup_spec:
          schedule_definition:
            timezone: Asia/Almaty
            occurrence_times:
            - '2026-09-20T09:15:00+05:00'
            - '2026-09-20T09:45:00+05:00'
            - '2026-09-20T09:50:00+05:00'
          interval_start: '2026-09-20T09:00:00+05:00'
          bound_limit: 2
          admission: real_schedule_owner
          effect_dispatch_disabled: true
          seal_revision_after_all_occurrences_defined: true
        case_name: overflow-held
        case_description: Три due occurrence при limit=2; попытка раннего amendment.
        expected_audit:
          eligible_count: 3
          active_overflow_holds: 1
          new_runs: 0
          bound_advanced: false
          amendment: rejected
        response_semantics: Сообщает только наблюдаемый результат; неизвестное не превращает в успех.
        model_delta: 0
        delivery_delta: 0
        fault_count: 1
        intervention:
          attempt_amendment_before_resolution: true
          attempt_amendment_while_overflow_hold_active: true
        clock_after: '2026-09-20T10:00:00+05:00'
        schedule_policy: COALESCED
      steps:
      - setup
      - request
      - exercise-fault
      - inspect
      allowed_outcome: Три due occurrence при limit=2; попытка раннего amendment.
      requires:
      - registered_scheduler_driver
      - independent_journal_and_transport_observers
    resolve-overflow:
      parameters:
        setup_spec:
          schedule_definition:
            timezone: Asia/Almaty
            occurrence_times:
            - '2026-09-20T09:15:00+05:00'
            - '2026-09-20T09:45:00+05:00'
            - '2026-09-20T09:50:00+05:00'
          interval_start: '2026-09-20T09:00:00+05:00'
          bound_limit: 2
          admission: real_schedule_owner
          effect_dispatch_disabled: true
          seal_revision_after_all_occurrences_defined: true
        case_name: resolve-overflow
        case_description: После overflow разрешить successor bound=4; повторить тот же resolution.
        expected_audit:
          eligible_count: 3
          resolution_decisions: 1
          resolution_parents_all_sub_batches: true
          bound_advances: 1
          replay_allocations: 0
        response_semantics: Сообщает только наблюдаемый результат; неизвестное не превращает в успех.
        model_delta: 0
        delivery_delta: 0
        fault_count: 1
        intervention:
          sequence:
          - overflow-at-two
          - successor-bound-four
          - resolve
          - exact-resolution-replay
        clock_after: '2026-09-20T10:00:00+05:00'
        schedule_policy: COALESCED
      steps:
      - setup
      - request
      - exercise-fault
      - inspect
      allowed_outcome: После overflow разрешить successor bound=4; повторить тот же resolution.
      requires:
      - registered_scheduler_driver
      - independent_journal_and_transport_observers
    omission:
      parameters:
        setup_spec:
          schedule_definition:
            timezone: Asia/Almaty
            occurrence_times:
            - '2026-09-20T09:15:00+05:00'
            - '2026-09-20T09:45:00+05:00'
          interval_start: '2026-09-20T09:00:00+05:00'
          bound_limit: 2
          admission: real_schedule_owner
          effect_dispatch_disabled: true
        case_name: omission
        case_description: Убрать eligible occurrence из publication manifest.
        expected_audit:
          decision: typed_rejection
          new_runs: 0
          bound_advanced: false
        response_semantics: Сообщает только наблюдаемый результат; неизвестное не превращает в успех.
        model_delta: 0
        delivery_delta: 0
        fault_count: 1
        intervention:
          omit_occurrence: second
        clock_after: '2026-09-20T10:00:00+05:00'
        schedule_policy: COALESCED
      steps:
      - setup
      - request
      - exercise-fault
      - inspect
      allowed_outcome: Убрать eligible occurrence из publication manifest.
      requires:
      - registered_scheduler_driver
      - independent_journal_and_transport_observers
    crash-coalesce:
      parameters:
        setup_spec:
          schedule_definition:
            timezone: Asia/Almaty
            occurrence_times:
            - '2026-09-20T09:15:00+05:00'
            - '2026-09-20T09:45:00+05:00'
          interval_start: '2026-09-20T09:00:00+05:00'
          bound_limit: 2
          admission: real_schedule_owner
          effect_dispatch_disabled: true
        case_name: crash-coalesce
        case_description: Crash после atomic coalesce commit; повторная доставка после restart.
        expected_audit:
          eligible_count: 2
          dispositions:
          - COALESCED
          - COALESCED
          new_runs: 1
          replay_allocations: 0
          bound_advances: 1
        response_semantics: Сообщает только наблюдаемый результат; неизвестное не превращает в успех.
        model_delta: 0
        delivery_delta: 0
        fault_count: 1
        intervention:
          sequence:
          - coalesce-commit
          - crash
          - restart
          - duplicate-delivery
        clock_after: '2026-09-20T10:00:00+05:00'
        schedule_policy: COALESCED
      steps:
      - setup
      - request
      - exercise-fault
      - inspect
      allowed_outcome: Crash после atomic coalesce commit; повторная доставка после restart.
      requires:
      - registered_scheduler_driver
      - independent_journal_and_transport_observers
  requirements:
    grounding: 'R15.1–2 / A17, A18, A98: revision-bound occurrence identity, полный eligible manifest,
      interval bound, overflow resolution. Пользователь не создаёт расписание через выдуманный tool:
      factory оформляет его через реальную owner boundary.'
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
    boundary: mech14_17.scheduler.exercise
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
    operation: mech14_17.scheduler.setup
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
    operation: mech14_17.scheduler.observed
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

**Пользователь · request:** Покажи, какие запланированные проверки отчёта должны были начаться к десяти.

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
    operation: mech14_17.scheduler.request
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
    operation: mech14_17.scheduler.observed
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
    operation: mech14_17.scheduler.exercise
    arguments:
      case: $case_name
      subject: $mechanism_subject
      intervention: $intervention
      worker_ticks: 4
      clock_after: $clock_after
      schedule_policy: $schedule_policy
    count: 1
    result:
      source: real_boundary
      capture:
        mechanism_actual: boundary_result
  events:
  - id: action.observed
    operation: mech14_17.scheduler.observed
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
    operation: mech14_17.scheduler.exercise
    arguments:
      case: $case_name
      subject: $mechanism_subject
      intervention: $intervention
      worker_ticks: 4
      clock_after: $clock_after
      schedule_policy: $schedule_policy
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
    operation: mech14_17.scheduler.observed
    count: 1
    same_call_as: action
    fields:
      subject: $mechanism_subject
      completed_registered_case: $case_name
```

## Независимая проверка и ответ

**Chiplog · answer · пример:** К десяти должны были пройти две проверки отчёта. Выполню их вместе.

Пример относится к ветке two-coalesced; target semantics записана в variant.parameters.response_semantics. Component-разрез не вызывает модель ради ответа. Проверка реальной реплики потребует отдельного production-loop/renderer binding; до него полный разговор не получает PASS.

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
    operation: mech14_17.scheduler.inspect
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
    operation: mech14_17.scheduler.observed
    count: 1
    same_call_as: audit
    fields:
      baseline: $mechanism_baseline
      subject: $mechanism_subject
  state:
    mech14_17.scheduler.audit:
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

Полный R15 дополнительно требует всех трёх CAS serial orders и representative/namespace substitution. В каждом варианте clock и policy выбираются до запуска и записываются в source bundle; expected manifest вычисляется независимым oracle из schedule revision, не берётся из результата scheduler.

Все нули относятся к конечному trace от setup до audit с четырьмя worker ticks. Они не доказывают вечное отсутствие повторов. Без зарегистрированных driver, observer и fault mappings каждый вариант остаётся NOT_RUNNABLE; успешная компиляция не является runtime PASS.
