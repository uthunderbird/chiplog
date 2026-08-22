# Транскрипты

Транскрипт — versioned Markdown-сценарий пользовательского взаимодействия с Chiplog. Видимый текст показывает один связный путь от намерения пользователя до осознанного исхода. Скрытые HTML-комментарии документируют предполагаемую механику и задают контракт будущего eval.

Транскрипт не является записью фактического прогона и не программирует отдельный agent loop. Eval собирает тот же production loop, что и продукт. Runner компилирует authored-транскрипт в scenario bundle, а фактические model responses, tool calls, результаты, screens, effects и trace сохраняет отдельным result artifact со ссылкой на ID, версию и digest транскрипта.

## Два назначения, три слоя

1. **Visible story** — user story и пример сквозного диалога для дизайна фичи.
2. **Authored hidden contract** — arrangement, fixtures, ожидания и запреты для eval.
3. **Observed result** — отдельный артефакт запуска. Он никогда не записывается обратно как ожидаемое поведение.

Скрытый contract делится ещё строже:

- `scenario` задаёт исходное состояние, injected boundaries, fixtures и допустимые исходы;
- `design` объясняет предполагаемые tool calls/results и rationale, но игнорируется eval-компилятором;
- `expect` и `forbid` задают проверяемые свойства production trace и наблюдаемого состояния.

Так tool-вызов можно спроектировать до реализации, не заставляя eval исполнять заранее написанный путь. Fixture отвечает только если production loop сам сделал matcher-совместимый запрос.

## Форма файла

```markdown
# Короткое название

> **User story.** Как <роль>, я хочу <исход>, для чего <действие>.

<!--
scenario:
  id: stable-id
  version: 1
  vision_version: 2026-08-22.2
  arrange: {}
  fixtures: []
  allowed_outcomes: []
-->

**Пользователь:** ...

<!--
design:
  tool_call: illustrative only
  rationale: ...
-->

**Chiplog:** ...

<!--
expect:
  response: ...
  state: ...
-->
```

Каждый комментарий содержит ровно один верхнеуровневый discriminator: `scenario`, `design`, `expect` или `forbid`. Неизвестный discriminator, неизвестное поле или смешение modalities должны завершать компиляцию ошибкой.

## Семантика

### Visible story

- Реплики пользователя — точные stimuli, если `scenario` не объявляет варианты ввода.
- Реплики Chiplog — exemplar желаемого опыта, а не golden strings.
- Точная формулировка проверяется только явным `expect.response.exact` и нужна лишь там, где слова сами несут контракт.
- История заканчивается сознательно разрешённым, отложенным либо честно неопределённым исходом; «успех» не обязателен.

### Fixtures

Fixture связывает публичную injected boundary, matcher запроса и ответ внешнего мира. Он не вызывает tool и не задаёт порядок действий модели. Невызванный fixture допустим, если отдельный `expect` не требует соответствующего наблюдения.

### Expectations and forbids

Ожидания описывают семантические свойства: прочитан ли актуальный state до предложения, создана ли ровно одна intent/effect identity, показаны ли consequence и planning-change status. Запреты являются hard gates: например, внешний effect до authority или повтор неизвестного effect.

Когда порядок существенен, используются стабильные event labels и только три отношения:

- `after` — событие доступно после перечисленных причин;
- `before` — наблюдение обязано предшествовать указанной границе;
- `concurrent_with` — оба порядка разрешены, причинной зависимости нет.

Это partial-order contract, а не scheduler script. Абсолютное время приходит через injected clock только в сценариях, где время является предметом поведения.

### Allowed outcomes

Сценарий задаёт множество семантически допустимых исходов. Один красивый exemplar не запрещает другие корректные вопросы, формулировки или число turns. Deterministic hard gates проверяются отдельно и не могут быть прощены LLM judge.

## Версионирование и результаты

- `scenario.id` стабилен для одной проверяемой истории; смысловое изменение повышает `version`.
- Scenario bundle связывает ID, version, digest, VisionVersion, prompt/model/tool/policy/budget/clock/adapter artifacts.
- Result artifact связывает тот же identity с полной наблюдаемой trace и feature vector.
- Изменение visible story, arrangement, fixtures, expectations или forbids меняет digest. Типографическая правка тоже меняет authored artifact, даже если компилятор затем докажет неизменность bundle.

## Начальный набор

- [`calendar-proposal-confirmation.md`](transcripts/calendar-proposal-confirmation.md) — proposal, exact adoption и receipt.
- [`fact-claim-without-plan-change.md`](transcripts/fact-claim-without-plan-change.md) — Fact Claim без молчаливого изменения Plan.
- [`unknown-calendar-outcome.md`](transcripts/unknown-calendar-outcome.md) — неизвестный provider outcome без слепого retry.

Следующий различающий сценарий: stale proposal после изменения authoritative calendar head. Он появится вместе с точной схемой matcher/assertion registry, когда начнётся реализация eval compiler.
