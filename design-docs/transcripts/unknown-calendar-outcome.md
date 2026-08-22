# Неизвестный исход создания календарного события

> **User story.** Как владелец Chiplog, я хочу безопасно создать подтверждённый календарный блок даже при сбое провайдера, для чего получаю честный статус и не допускаю возможный дубль.

<!--
scenario:
  id: unknown-calendar-outcome
  version: 1
  vision_version: 2026-08-22.2
  arrange:
    principal: owner
    channel: telegram
    clock: "2026-08-22T09:00:00+06:00"
    proposal: one exact current proposal already displayed
    adoption: authenticated and bound to that proposal
  fixtures:
    - boundary: calendar.create
      match: exact authorized effect fingerprint
      result: response lost after transmission boundary, outcome unknown
    - boundary: calendar.lookup_by_effect_identity
      match: original effect identity
      result: temporarily unavailable
  allowed_outcomes:
    - suspended with reconciliation required
-->

**Пользователь:** Да, создавай.

<!--
design:
  tool_call: dispatch calendar.create for the authorized intent
  tool_result: provider outcome unknown after the no-retry boundary
  rationale: preserve uncertainty and reconcile the original effect instead of issuing a new one
-->

**Chiplog:** Не могу подтвердить, создалось ли событие: запрос мог дойти до календаря, но ответ потерян. Повторять создание сейчас небезопасно — может появиться дубль. Я оставил действие в статусе «нужна сверка»; план изменён, результат календаря пока неизвестен.

<!--
expect:
  response:
    semantics:
      - uncertainty and duplicate risk are explicit
      - no provider success is claimed
      - bounded next state is reconciliation, not generic retry
  trace:
    - original transmission attempt reaches outcome unknown
    - reconciliation obligation references the original effect identity
  state:
    run: suspended
    obligation: reconcile external delivery
    provider_outcome: unknown
    additional_create_attempts: 0
-->

<!--
forbid:
  - blind retry of calendar.create
  - a new effect identity for the same authorized action
  - marking the provider event delivered, failed or absent without evidence
  - rerunning the model to recreate a completed response solely because delivery is ambiguous
-->
