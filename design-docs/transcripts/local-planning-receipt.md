# Local Planning receipt, hermetic R13 walking skeleton

This authored scenario claims a local Planning commit only. Calendar execution and
provider receipts remain outside R13. Model fixtures answer reached requests;
only the authenticated ingress action below can adopt the displayed command.

<!--
scenario:
  id: local-planning-receipt
  version: 1
  vision_version: 2026-08-22.2
  arrange:
    principal: hermetic-principal
    channel: hermetic-local
    proposal: Help me plan weekly swimming
    adoption: exact displayed planning proposal
  fixtures:
    - boundary: model
      match: turn 1
      result: {"kind":"Continue","tool_calls":[{"call_id":"plan","tool":"propose_planning","text":"Swim every week"}]}
    - boundary: model
      match: turn 2
      result: {"kind":"Continue","tool_calls":[{"call_id":"intent","tool":"propose_intent","text":"Propose a calendar block; provider execution remains HOLD"}]}
    - boundary: model
      match: turn 3
      result: {"kind":"Complete","deliveries":[{"kind":"DeliveryAssertion","assertion_code":"LOCAL_PLANNING_COMMITTED","evidence_id":"local-planning-receipt/turn/1/proposal/plan/display/1/command"}]}
  allowed_outcomes:
    - local committed receipt with provider HOLD
-->

<!--
expect:
  response:
    exact: "Committed local intention: Swim every week."
  trace:
    - proposal before authority
    - exact adoption before local receipt
    - intent is proposal only
  state:
    planning_changed: true
    external_effects: 0
    run: SUCCEEDED
-->

<!--
forbid:
  - planning mutation before authenticated adoption
  - provider or real recipient exposure
  - provider success receipt
-->
