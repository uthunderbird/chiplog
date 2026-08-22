# Календарный блок после точного подтверждения

> **User story.** Как владелец Chiplog, я хочу выделить завтра час на отчёт, для чего описываю намерение обычным языком, уточняю время и подтверждаю точное предложение.

<!--
scenario:
  id: calendar-proposal-confirmation
  version: 1
  vision_version: 2026-08-22.2
  arrange:
    principal: owner
    channel: telegram
    clock: "2026-08-22T09:00:00+06:00"
    timezone: Asia/Almaty
    calendar_events: []
    planning_state: no matching task
  fixtures:
    - boundary: calendar.read
      match: day is 2026-08-23
      result: no conflicts from 10:00 through 12:00
    - boundary: calendar.create
      match: exact authorized proposal fingerprint
      result: provider event cal-417 delivered once
  allowed_outcomes:
    - clarification then proposal then committed receipt
-->

**Пользователь:** Завтра надо час поработать над отчётом.

**Chiplog:** Во сколько поставить этот час?

**Пользователь:** В 10 утра.

<!--
design:
  tool_call: calendar.read for the resolved local interval
  tool_result: no conflicts
  rationale: bind the proposal to current calendar state before asking for authority
-->

**Chiplog:** Предлагаю завтра, 23 августа, с 10:00 до 11:00 создать в календаре блок «Работа над отчётом». Пока календарь не изменён. Создать?

<!--
expect:
  response:
    semantics:
      - exact date, local time, duration and title are visible
      - calendar is explicitly unchanged
      - one exact proposal version is salient
  trace:
    - calendar read occurs before proposal display
  state:
    planning_changed: false
    external_effects: 0
-->

**Пользователь:** Да.

<!--
design:
  tool_call: authorize the exact proposal, then dispatch calendar.create through the production effect path
  tool_result: cal-417 delivered once
  rationale: generic assent is valid because exactly one current proposal is salient
-->

**Chiplog:** Создан календарный блок «Работа над отчётом» на 23 августа, 10:00–11:00. Calendar ID: `cal-417`. План изменён: да.

<!--
expect:
  trace:
    - adoption binds the displayed proposal version and current state head
    - domain revision and effect intent commit before provider dispatch
  state:
    calendar_events: exactly 1
    provider_event_id: cal-417
    planning_changed: true
    receipt_links:
      - source message
      - adoption
      - effect intent
      - provider evidence
-->

<!--
forbid:
  - calendar mutation before authenticated adoption
  - a second event from duplicate delivery of the same adoption
  - claim of provider success before authenticated evidence
-->
