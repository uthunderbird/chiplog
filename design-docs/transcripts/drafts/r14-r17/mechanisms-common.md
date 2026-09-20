# Общие bindings механических историй P03–P08

**Статус: design; stateful v2.** Это контракт будущего подключения, не журнал исполнения.

Все `mech14_17.*` — локальные имена draft driver/observer, не ToolSpecs и не production API. `production_schema: null` запрещает считать варианты runnable. `fault.reached` — зарезервированное событие compiler; оно должно прийти от реально достигнутого hook. Fixtures могут задавать внешние bytes и clock, но не внутреннее успешное принятие.

```yaml transcript
scenario:
  format_version: 2
  protocol_version: 2
  id: r14-r17-mechanisms-common
  version: 1
  status: design
  kind: library
  arrange:
    isolation: fresh_database_and_external_logs_per_variant
    tenant: mechanism-fixture-tenant
    principal: mechanism-owner
    clock: '2026-09-20T09:00:00+05:00'
    timezone: Asia/Almaty
    real_connections: []
    identities: allocate_through_real_boundary_factories_not_literal_expected_ids
  policy:
    budget:
      model_calls: 4
      tool_calls: 12
      worker_ticks: 4
      restart_count: 1
      includes_prefix: true
      unreached_end: FAIL
    baseline: after_setup_before_request
    on_missing_binding: NOT_RUNNABLE
    on_undelivered_fault: FAIL
    hidden_from_model:
    - expect
    - forbid
    - variants
    - audit
    - external_observer_logs
    internal_results: real_boundary_only
    unlisted_external_connections: forbidden
    matching: exact_ordered_bytes_identity_and_binding
  bindings:
    mech14_17.recovery.setup:
      kind: harness_owner_driver
      input: draft observer/driver contract described in scenario; must map to exact public owner/broker
        schemas
      result: actual boundary result and independently read audit projection
      production_schema: null
    mech14_17.recovery.request:
      kind: harness_owner_driver
      input: draft observer/driver contract described in scenario; must map to exact public owner/broker
        schemas
      result: actual boundary result and independently read audit projection
      production_schema: null
    mech14_17.recovery.exercise:
      kind: harness_owner_driver
      input: draft observer/driver contract described in scenario; must map to exact public owner/broker
        schemas
      result: actual boundary result and independently read audit projection
      production_schema: null
    mech14_17.recovery.inspect:
      kind: independent_observer
      input: draft observer/driver contract described in scenario; must map to exact public owner/broker
        schemas
      result: actual boundary result and independently read audit projection
      production_schema: null
    mech14_17.recovery.observed:
      kind: observer_event
      production_schema: null
    mech14_17.model.setup:
      kind: harness_owner_driver
      input: draft observer/driver contract described in scenario; must map to exact public owner/broker
        schemas
      result: actual boundary result and independently read audit projection
      production_schema: null
    mech14_17.model.request:
      kind: harness_owner_driver
      input: draft observer/driver contract described in scenario; must map to exact public owner/broker
        schemas
      result: actual boundary result and independently read audit projection
      production_schema: null
    mech14_17.model.exercise:
      kind: harness_owner_driver
      input: draft observer/driver contract described in scenario; must map to exact public owner/broker
        schemas
      result: actual boundary result and independently read audit projection
      production_schema: null
    mech14_17.model.inspect:
      kind: independent_observer
      input: draft observer/driver contract described in scenario; must map to exact public owner/broker
        schemas
      result: actual boundary result and independently read audit projection
      production_schema: null
    mech14_17.model.observed:
      kind: observer_event
      production_schema: null
    mech14_17.scheduler.setup:
      kind: harness_owner_driver
      input: draft observer/driver contract described in scenario; must map to exact public owner/broker
        schemas
      result: actual boundary result and independently read audit projection
      production_schema: null
    mech14_17.scheduler.request:
      kind: harness_owner_driver
      input: draft observer/driver contract described in scenario; must map to exact public owner/broker
        schemas
      result: actual boundary result and independently read audit projection
      production_schema: null
    mech14_17.scheduler.exercise:
      kind: harness_owner_driver
      input: draft observer/driver contract described in scenario; must map to exact public owner/broker
        schemas
      result: actual boundary result and independently read audit projection
      production_schema: null
    mech14_17.scheduler.inspect:
      kind: independent_observer
      input: draft observer/driver contract described in scenario; must map to exact public owner/broker
        schemas
      result: actual boundary result and independently read audit projection
      production_schema: null
    mech14_17.scheduler.observed:
      kind: observer_event
      production_schema: null
    mech14_17.takeover.setup:
      kind: harness_owner_driver
      input: draft observer/driver contract described in scenario; must map to exact public owner/broker
        schemas
      result: actual boundary result and independently read audit projection
      production_schema: null
    mech14_17.takeover.request:
      kind: harness_owner_driver
      input: draft observer/driver contract described in scenario; must map to exact public owner/broker
        schemas
      result: actual boundary result and independently read audit projection
      production_schema: null
    mech14_17.takeover.exercise:
      kind: harness_owner_driver
      input: draft observer/driver contract described in scenario; must map to exact public owner/broker
        schemas
      result: actual boundary result and independently read audit projection
      production_schema: null
    mech14_17.takeover.inspect:
      kind: independent_observer
      input: draft observer/driver contract described in scenario; must map to exact public owner/broker
        schemas
      result: actual boundary result and independently read audit projection
      production_schema: null
    mech14_17.takeover.observed:
      kind: observer_event
      production_schema: null
    mech14_17.ingress.setup:
      kind: harness_owner_driver
      input: draft observer/driver contract described in scenario; must map to exact public owner/broker
        schemas
      result: actual boundary result and independently read audit projection
      production_schema: null
    mech14_17.ingress.request:
      kind: harness_owner_driver
      input: draft observer/driver contract described in scenario; must map to exact public owner/broker
        schemas
      result: actual boundary result and independently read audit projection
      production_schema: null
    mech14_17.ingress.exercise:
      kind: harness_owner_driver
      input: draft observer/driver contract described in scenario; must map to exact public owner/broker
        schemas
      result: actual boundary result and independently read audit projection
      production_schema: null
    mech14_17.ingress.inspect:
      kind: independent_observer
      input: draft observer/driver contract described in scenario; must map to exact public owner/broker
        schemas
      result: actual boundary result and independently read audit projection
      production_schema: null
    mech14_17.ingress.observed:
      kind: observer_event
      production_schema: null
    mech14_17.delivery.setup:
      kind: harness_owner_driver
      input: draft observer/driver contract described in scenario; must map to exact public owner/broker
        schemas
      result: actual boundary result and independently read audit projection
      production_schema: null
    mech14_17.delivery.request:
      kind: harness_owner_driver
      input: draft observer/driver contract described in scenario; must map to exact public owner/broker
        schemas
      result: actual boundary result and independently read audit projection
      production_schema: null
    mech14_17.delivery.exercise:
      kind: harness_owner_driver
      input: draft observer/driver contract described in scenario; must map to exact public owner/broker
        schemas
      result: actual boundary result and independently read audit projection
      production_schema: null
    mech14_17.delivery.inspect:
      kind: independent_observer
      input: draft observer/driver contract described in scenario; must map to exact public owner/broker
        schemas
      result: actual boundary result and independently read audit projection
      production_schema: null
    mech14_17.delivery.observed:
      kind: observer_event
      production_schema: null
    fault.reached:
      kind: observer_event
      production_schema: null
  observations:
    mech14_17.external_effects: 'Independent offline provider journal: effect identities and payload
      bytes; count new consequential effects since setup cut-off.'
    mech14_17.model_transfers: 'Independent model transport byte log: request transfers since setup
      cut-off, not local attempt rows.'
    mech14_17.delivery_transfers: 'Independent offline recipient transport log: exact bytes and exact
      authenticated recipient since setup cut-off.'
    mech14_17.fault_hits: Fault-hook trace correlated to the reached production boundary, not the
      authored fault declaration.
    mech14_17.recovery.audit: Independent structured audit of actual journals, owner reads and external
      logs for this variant; compare only the selected documented fields, never build this map from
      expect.
    mech14_17.model.audit: Independent structured audit of actual journals, owner reads and external
      logs for this variant; compare only the selected documented fields, never build this map from
      expect.
    mech14_17.scheduler.audit: Independent structured audit of actual journals, owner reads and external
      logs for this variant; compare only the selected documented fields, never build this map from
      expect.
    mech14_17.takeover.audit: Independent structured audit of actual journals, owner reads and external
      logs for this variant; compare only the selected documented fields, never build this map from
      expect.
    mech14_17.ingress.audit: Independent structured audit of actual journals, owner reads and external
      logs for this variant; compare only the selected documented fields, never build this map from
      expect.
    mech14_17.delivery.audit: Independent structured audit of actual journals, owner reads and external
      logs for this variant; compare only the selected documented fields, never build this map from
      expect.
```
```yaml transcript
forbid:
  scope: all_variants_including_prefix
  predicates:
  - fixture_as_evidence_of_internal_commit
  - hidden_oracle_in_model_context
  - count_unreached_fault_as_tested
  - overwrite_prior_committed_result
  - infer_no_write_from_unchanged_screen
  - provider_or_real_recipient_exposure
```

`provider_or_real_recipient_exposure` запрещает реальные сетевые поверхности; локальные fake transport logs обязательны. Audit maps — проектируемая схема проверяющего observer, не обещание полей владельца. Все их поля необходимо зарегистрировать с derivation/query paths перед запуском; одна непривязанная проверка оставляет весь вариант NOT_RUNNABLE. В bind-artifact фиксируются schema version, code/bindings digests и expected/actual.

Каждый audit читает фактические owner journals после ограниченного trace и независимый внешний журнал. Счётчики effects/model/delivery сравниваются с capture setup, не с числом строк на экране. Worker ticks конечны; wall-clock timeout не доказывает отсутствие повтора. Scripted model fixture проверяет orchestration, не языковое понимание настоящей модели.
