# Интеграция харнесса

## Состояние до (шаг 0)

- source: локальный `../harness-boilerplate`, явно одобренный владельцем вместо ещё не опубликованного GitHub repository
- source commit: `25412952cb5a2d56a83d2b9e1c66521d4deca59b`; совпал с resolved local commit object
- upstream manifest: `.harness/upstream-manifest.txt`; SHA-256 `a101d3bc3ee4bbace0b449e0060274786ba03e4539dd6bc06ab27f3f83387bb9`
- license basis: владелец явно подтвердил право на перенос и лицензионную совместимость
- preserved notices: в source commit отсутствуют
- `core.hooksPath`: не задан
- pre-commit path: `.git/hooks/pre-commit`; файла нет
- baseline commit: `cfefc16609c766ad152f3aefd4b59cb39352cbac`
- project probe path: `src/chiplog/__harness_process_probe.py`
- project probe spec: invalid-syntax-v1
- project probe before result: PASSED

## Проект до интеграции (шаг 1)

- hook manager: отсутствует; точка крепления свободна
- lint: `uv run ruff check .` и `uv run ruff format --check .`; источник — `pyproject.toml`; ручной прогон прошёл
- typecheck: `uv run mypy`; источник — `pyproject.toml`; ручной прогон прошёл
- test: `uv run pytest`; источник — `pyproject.toml`; ручной прогон прошёл
- инструкции агенту: отсутствуют
- собственный свод практик: отсутствует; `design-docs/README.md` задаёт только размещение продуктовых документов
- структура: один Python package root (`src/chiplog`) и один test root (`tests`); отдельная monorepo-матрица не нужна

## Точка крепления (шаг 2)

- ветка: свободная точка; `.githooks/pre-commit` активирован после успешного ручного прогона
- команда активации для клона: `git config core.hooksPath .githooks`
- полный gate: 2,86 секунды (`/usr/bin/time -p env UV_CACHE_DIR=<temporary> sh .harness/scripts/gate.sh`)

## Адаптеры и область линтера (шаги 3–4)

- lint adapter: `uv run ruff check --force-exclude .`, `uv run ruff format --check --force-exclude .`, `uv run mypy`; ручной прогон прошёл
- test adapter: `uv run pytest` плюс self-test и локальные reproducer runners; ручной прогон прошёл
- Ruff исключает `.harness` через `[tool.ruff].extend-exclude`; явный путь с `--force-exclude` проверен и не анализируется
- Python и shell внутри `.harness` остаются под собственным AST/POSIX-разбором адаптера

## Применимость проверок (шаг 5)

Встроенный self-test прошёл 8 bad/good пар для семи checks.

- `rules_have_reproducers.py`: взят; защищает локальные `.harness/rules` и `.harness/reproducers`
- `retro_due.py`: взят; защищает локальный cadence и записи `.harness/retro`
- `commit_trail.py`: взят; связывает будущие локальные правила с входами в истории Chiplog
- `messages_are_actionable.py`: взят; проверяет сообщения всех локальных harness checks
- `checks_are_wired.py`: взят; не даёт локальному check выпасть из `gate.sh` и self-test
- `polish_artifacts.py`: взят; не даёт коммитить временные polish-артефакты в Chiplog
- `handoff_pending.py`: взят; держит локальные непересказанные решения и отложенные хвосты
- `slugify.py`: взят как библиотечная зависимость scripts, не отдельный check/run

## Состояние и словарь (шаг 6)

- собственный свод практик отсутствовал, поэтому взяты `AGENTS.md` и `HARNESS_MODEL.md`
- таблица «Текущие долги» очищена; долги upstream не перенесены
- observations, rules, reproducers и retro созданы пустыми либо только с README/TEMPLATE
- локальная история: медиана 9 файлов, p90 9 файлов, `n=1`; upstream `HC_FILES_SOFT=10` отличается менее чем вдвое и оставлен до накопления истории

## Что взято

- переносимая машинерия `.harness/scripts`, `.harness/skills` и `.githooks/pre-commit`
- пустые проектные реестры, модель harness и инструкции агенту
- hook-ветка: свободная точка через `core.hooksPath=.githooks`

## Что не взято

- два случайных upstream `.pyc`/`__pycache__` артефакта; удалены в source commit `7b394ee`
- index lookup verifier исправлен в source commit `2541295`: отсутствие staged path теперь проверяется через `git rev-parse --verify`; cadence-retro может быть закрыта самим staged-коммитом
- чужие observations, правила, reproducer cases, retro entries и текущие долги
- незакоммиченные изменения локальной рабочей копии источника

## Что осталось открытым

- пороги размера коммита нужно перекалибровать после появления достаточной истории Chiplog
- контрольная проба после установки: ПРОШЛА
- сравнение контрольной пробы: MATCHED — прошла до и после на том же пути и с той же спецификацией
- финальный verification tuple будет дописан после сборки окончательного staged tree
- upstream verifier прошёл 14 холодных polish-проходов; последний известный P0/P1 исправлен,
  но владелец остановил цикл до чистого regression sweep после финального ремонта

## Финальный verification tuple

- `core.hooksPath`: origin `.git/config`, value `.githooks`
- resolved hook: `.githooks/pre-commit`; POSIX shell script; mode `755`; SHA-256 `a53bea0ad7a22cbfa4e7e97aa21cfb4c93c0c551d2e13873ec99bbea8ef9e326`
- `gate.sh`: SHA-256 `17fddfdbb376a04bc0652bbf2f3e279e41df0d33dfe49ee198fcb3a1b6ba11dc`
- `lint.sh`: SHA-256 `f8cb8809e3eb3b5822ae681254e916f7fdccb3ea704bfd01ec71cb0cb7fc5c90`
- `test.sh`: SHA-256 `996b6a0f861b61c04ef3ac8930266c10e1b0dc41a7beaf5a08ebd51e042b609b`
- staged added-lines secret review: совпадений по credential/token/private-key/credential-URL/absolute-user-path patterns нет; `.env` не tracked
