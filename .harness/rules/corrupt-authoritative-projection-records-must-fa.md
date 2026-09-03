---
id: corrupt-authoritative-projection-records-must-fa
error_class: тихое частичное чтение повреждённого авторитетного durable record
reproducer: .harness/reproducers/corrupt-authoritative-projection-records-must-fa
born: 2026-09-03
---

## Провал

corrupt authoritative projection records must fail loudly

## Правило

Публичный Chiplog adapter, читающий authoritative durable records, не возвращает
частичный результат при некорректных bytes, decode/schema или integrity mismatch.
Он поднимает typed integrity error с operation, tenant и record identity и сохраняет
исходную причину через exception chaining. `except …: continue` допустим лишь для
явно non-authoritative optional display data.

## Где живёт

Проектное правило: класс ограничен authoritative durable projection Chiplog и живёт
в source policy `src/AGENTS.md §1`; regression input живёт в этом каталоге и в
R6 integration test. Это fail-loud instruction, не новый blocking harness gate.

## Дешёвый обход

`except (decode errors): continue`: он даёт зелёный happy-path и короткий renderer,
но превращает повреждение durable state в правдоподобный неполный ответ.

## Как удалить

Удали правило и прогони `.harness/reproducers/corrupt-authoritative-projection-records-must-fa`.
Гейт всё равно ловит — правило было мёртвым весом.
Ошибка рецидивирует — верни.
