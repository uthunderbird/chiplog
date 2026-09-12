# Факт без молчаливого изменения плана

> **User story.** Как владелец Chiplog, я хочу честно записать, что не сходил в бассейн во вторник, для чего сообщаю результат и отдельно решаю, менять ли будущий план.

<!--
scenario:
  id: fact-claim-without-plan-change
  version: 2
  vision_version: 2026-08-22.2
  arrange:
    principal: owner
    channel: cli
    clock: "2026-08-22T18:00:00+06:00"
    planning_state: pool task for 2026-08-18 remains current
    fact_state: no outcome claim for that occurrence
  fixtures: []
  allowed_outcomes:
    - fact recorded and plan remains unchanged
    - clarification if Tuesday does not resolve to one occurrence
-->

**Пользователь:** Я не ходил в бассейн во вторник.

<!--
design:
  tool_call: resolve the exact occurrence, display immutable claim and consequence; append only after exact confirmation
  tool_result: claim stored against the 2026-08-18 occurrence
  rationale: a factual report does not authorize a planning revision
-->

**Chiplog:** Подтвердите точную запись: занятие в бассейне 18 августа не состоялось. Изменится только журнал фактов; план останется прежним.

**Пользователь:** Подтверждаю эту точную запись через привязанную к показу команду подтверждения.

**Chiplog:** Записал ваш факт: занятие в бассейне 18 августа не состоялось. Это изменило журнал фактов, но не план. Задача в плане остаётся в прежнем состоянии. Хотите отдельно решить, что с ней делать?

<!--
expect:
  response:
    semantics:
      - record is labeled as the principal's Fact Claim
      - exact historical subject is visible
      - Plan staying unchanged is explicit
      - any planning action is offered separately
  state:
    fact_claims: exactly 1 owner-authored claim
    planning_changed: false
    planning_revisions_added: 0
-->

<!--
forbid:
  - automatic task completion, cancellation, rescheduling or reopening
  - treating the plan expectation as proof of what happened
  - presenting provider or model inference as the principal's claim
-->
