# Design docs

- `BRIEF.md` — продуктовый бриф, подготовленный до стрима.
- `VISION.md` — публичный долгосрочный продуктовый контракт, созданный на основе брифа.
- [`plan-fact-journal/`](plan-fact-journal/README.md) — сохранённая подробная модель разделения планирования, фактов и журнала; нормативный статус её частей определяется `VISION.md`. Старый путь `PLAN-FACT-JOURNAL.md` сохранён как compatibility index.
- `HEXAGONAL-CODE-LAYOUT.md` — выбранная физическая структура production-кода: capability-гексагоны, порты, адаптеры, platform substrate и composition root.
- `TRANSCRIPTS.md` — формат человекочитаемых сценариев для дизайна агентских фич и последующей компиляции в eval scenario bundles; примеры лежат в `transcripts/`.

Рабочий runbook стрима находится в `scratchpads/` и в Git не попадает.
