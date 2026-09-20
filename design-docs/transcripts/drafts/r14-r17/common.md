# Общие условия календарных историй R14–R17

**Статус: design library.** [Формат](../../../TRANSCRIPTS.md) · [План](../../../R14-R17-TRANSCRIPT-PLAN.md).

Имена `cal.*` — локальные design bindings для будущего адаптера, не новые ToolSpecs продукта. Generic `HermeticEffectsProvider` из R16 ещё требует calendar mapping. Fixtures остаются в сценариях, пока mapping не согласован. Все result/state fields ниже — типизированные проекции для привязки; production envelope проверяется целиком.

```yaml transcript
scenario:
  format_version: 2
  protocol_version: 2
  id: calendar-r14-r17-common
  version: 1
  status: design
  kind: library
  arrange:
    isolation: fresh_database_per_variant
    principal: owner
    tenant: calendar-fixture
    channel: hermetic-telegram
    clock: '2026-08-22T09:00:00+05:00'
    timezone: Asia/Almaty
    planning:
      revisions: []
    calendar:
      events: []
    provider:
      real_connections: []
    authority_setup: independently authenticated fixture principal; no authored boolean grants authority
  initial_context:
    clock: '2026-08-22T09:00:00+05:00'
    timezone: Asia/Almaty
    messages: []
    screens: []
    prior_tool_results: []
    summaries: []
    attention_tail: []
    instructions:
      artifact: calendar-design-context-v1
      text: Уточняй время, показывай предложение и жди подтверждения. Сообщай только подтверждённый результат.
    tools:
      binding: cal.read/display model interface pending; no current ToolSpec asserted
  policy:
    model_calls: 12
    tool_calls: 24
    worker_ticks_per_drain: 4
    worker_tick_budget: 16
    clock_advance_seconds_per_tick: 1
    budget_scope: whole variant including executed prefix; restart/takeover never resets
    fault_window: ends after last declared event and four bounded worker ticks where drain is requested
    provider_mode: hermetic_only
    observer_context: never expose oracle, faults or expected state to model
  bindings:
    cal.read:
      kind: draft_boundary_mapping
      production_schema: null
      input: exact complete authenticated production envelope; map pending
      result: actual owner result or declared external response; never injected internal success
    cal.display:
      kind: draft_boundary_mapping
      production_schema: null
      input: exact complete authenticated production envelope; map pending
      result: actual owner result or declared external response; never injected internal success
    cal.accept:
      kind: draft_boundary_mapping
      production_schema: null
      input: exact complete authenticated production envelope; map pending
      result: actual owner result or declared external response; never injected internal success
    cal.dispatch:
      kind: draft_boundary_mapping
      production_schema: null
      input: exact complete authenticated production envelope; map pending
      result: actual owner result or declared external response; never injected internal success
    cal.evidence:
      kind: draft_boundary_mapping
      production_schema: null
      input: exact complete authenticated production envelope; map pending
      result: actual owner result or declared external response; never injected internal success
    cal.render:
      kind: draft_boundary_mapping
      production_schema: null
      input: exact complete authenticated production envelope; map pending
      result: actual owner result or declared external response; never injected internal success
    cal.replay:
      kind: draft_boundary_mapping
      production_schema: null
      input: exact complete authenticated production envelope; map pending
      result: actual owner result or declared external response; never injected internal success
    cal.reconcile:
      kind: draft_boundary_mapping
      production_schema: null
      input: exact complete authenticated production envelope; map pending
      result: actual owner result or declared external response; never injected internal success
    cal.restart:
      kind: draft_boundary_mapping
      production_schema: null
      input: exact complete authenticated production envelope; map pending
      result: actual owner result or declared external response; never injected internal success
    cal.takeover:
      kind: draft_boundary_mapping
      production_schema: null
      input: exact complete authenticated production envelope; map pending
      result: actual owner result or declared external response; never injected internal success
    cal.replacement:
      kind: draft_boundary_mapping
      production_schema: null
      input: exact complete authenticated production envelope; map pending
      result: actual owner result or declared external response; never injected internal success
    cal.invalidate:
      kind: draft_boundary_mapping
      production_schema: null
      input: exact complete authenticated production envelope; map pending
      result: actual owner result or declared external response; never injected internal success
    cal.continuation:
      kind: draft_boundary_mapping
      production_schema: null
      input: exact complete authenticated production envelope; map pending
      result: actual owner result or declared external response; never injected internal success
    cal.redisplay:
      kind: draft_boundary_mapping
      production_schema: null
      input: exact complete authenticated production envelope; map pending
      result: actual owner result or declared external response; never injected internal success
    cal.clarification.accepted:
      kind: observer_event
      production_schema: null
    cal.step.closed:
      kind: observer_event
      production_schema: null
    cal.publication.committed:
      kind: observer_event
      production_schema: null
    cal.send_committed:
      kind: observer_event
      production_schema: null
    cal.provider.transfer:
      kind: observer_event
      production_schema: null
    fault.reached:
      kind: observer_event
      production_schema: null
    cal.outcome.unknown:
      kind: observer_event
      production_schema: null
    cal.status.accepted:
      kind: observer_event
      production_schema: null
    cal.stale_worker.rejected:
      kind: observer_event
      production_schema: null
    cal.raw_evidence.committed:
      kind: observer_event
      production_schema: null
  observations:
    cal.plan_revisions: 'count: committed Planning revisions for the original subject, independently read from owner journal'
    cal.effect_intents: 'count: committed external intents for the original adopted action, including rival/replacement identities'
    cal.transmissions: 'count: durable transmission children for the original action, including replacement attempts'
    cal.provider_sends: 'count: independent last-boundary outbound submission log for original action across identities and duplicate bytes; survives app restart; submission does not prove remote effect'
    cal.provider_events: 'count: independently confirmed fake-calendar events for the original action; unavailable in opaque-transmission variant'
    cal.effect: 'object: full original immutable effect intent, not current UI projection'
    cal.command_result: 'object: full original publication result from broker replay lookup'
    cal.atomic_batch: 'value: NONE or COMPLETE after independent enumeration of exact expected participants; PARTIAL fails'
    cal.outcome: 'value: application reducer outcome for original transmission, UNKNOWN or CONFIRMED'
    cal.obligations_open: 'count: original-action recovery obligations still open'
    cal.obligation_identity: 'object: immutable original recovery obligation reference'
    cal.continuation_ready: 'value: boolean from actual continuation admission, not accounting alone'
    cal.model_calls: 'count: actual model requests for this scenario including prefix'
    cal.claims_without_evidence: 'count: accepted consequential success claims without exact closed owner evidence'
    cal.replacement_attempts: 'count: replacement transmission children for original action, independently enumerated'
    cal.changed_historical_results: 'count: previously committed results whose full bytes changed'
    cal.current_display: 'object: exact immutable active display reference'
```

```yaml transcript
forbid:
  scope: whole_scenario_including_prefix
  predicates:
  - external_effect_before_authority
  - fixture_as_evidence_of_internal_commit
  - inject_acceptance_verdict
  - hidden_oracle_in_model_context
  - provider_or_real_recipient_exposure
  - count_unreached_fault_as_tested
  - infer_no_write_from_unchanged_screen
  - overwrite_prior_committed_result
```
