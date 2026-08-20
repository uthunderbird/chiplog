---
name: integrate-harness
description: Встроить харнесс из harness-boilerplate в существующий проект, у которого уже есть свой процесс. Ведёт отчёт-файл с шага 0, разбирает три развилки — точка монтирования гейта, какие проверки не берём, что из состояния боилерплейта не едет — и заканчивается проверкой: гейт отклоняет нарушение харнесса, а реакция проекта на контрольную пробу сравнена с состоянием до установки. Использовать, когда человек дал ссылку на этот файл и просит поставить харнесс в свой репозиторий.
---

# Встроить харнесс в существующий проект

Ты в **целевом** проекте. Харнесс лежит в другом репозитории. Твоя задача — забрать оттуда
то, что здесь работает, и не сломать то, что здесь уже работает.

Второе важнее первого. Установка, погасившая чужой линтер, хуже неустановки: без харнесса
проект защищён как раньше, с погашенным линтером — хуже, чем был, и молча.

**Локальный словарь.** Харнесс — переносимый набор проверок, процедур и hook-файлов в
`.harness/` и `.githooks/`. Гейт — `.harness/scripts/gate.sh`, единая команда, которая
запускает проверки харнесса и адаптеры проекта и возвращает ненулевой код при любом
провале. Точка крепления — выбранный Git entry point для `pre-commit`: `.git/hooks` либо
каталог из `core.hooksPath`. Адаптеры — `.harness/scripts/lint.sh` и `test.sh`, тонкие
shell-обёртки над каноническими командами линтера и тестов целевого проекта.

## Как устроена эта процедура

**Отчёт заводится в шаге 0, а не в конце.** Сначала выполняется обратимая контрольная
проба «до»; затем `.harness/integration-report.md` становится первым постоянным артефактом,
и каждый следующий шаг в него дописывает. Причина не в аккуратности:
половина решений ниже опирается на то, **как было до тебя**, а точка крепления хуков живёт
в `.git/config` — вне версии, без истории. Не записав состояние заранее, ты его не
восстановишь, и на шаге 7 выберешь удобный ответ вместо верного.

Шаг считается сделанным, когда в отчёте заполнены все названные для него поля и приложены
команды проверки. Наличие заголовка или пустой строки-заглушки завершением не считается.

## Стоимость и предусловия

Полчаса чтения чужого репозитория и десяток правок. Проверка на шаге 7 делает пробные
коммиты, поэтому дерево должно быть чистым. Скажи это человеку **до начала и дождись
ответа** — это первое обязательное блокирующее место. Второе возможно на шаге 6, если у
проекта уже есть собственный свод практик и человек должен выбрать главный словарь.
До первой commit-пробы человек отдельно подтверждает, что доверяет существующим Git config,
hooks и manager entry points целевого проекта. Этот скилл не является sandbox для анализа
враждебного репозитория: нет подтверждения — `СТОП`, hook-код не исполняй.
Рабочая копия на всё время интеграции эксклюзивна: другие агенты, форматтеры, фоновые Git-
операции и параллельные правки остановлены. Обнаружил внешнюю мутацию — текущие hashes и
verify недействительны, остановись и начни проверки состояния заново.

---

## 0. Предусловия, снимок «до», отчёт

Выполни целиком, до всего остального.

```sh
set -eu

# 1. дерево обязано быть чистым — иначе шаг 7 не запустится, а правок уже будет много
STATUS_FIRST=$(git status --porcelain --untracked-files=all | sed -n '1p')
if [ -n "$STATUS_FIRST" ]
then
  echo 'СТОП: дерево не чисто — закоммить или отложи работу и начни заново' >&2
  exit 1
fi

command -v python3 >/dev/null 2>&1 || {
  echo 'СТОП: python3 обязателен для проверок и самотеста харнесса' >&2
  exit 1
}
for tool in git tar awk grep cmp file sed tail find date cp
do command -v "$tool" >/dev/null 2>&1 || {
  echo "СТОП: отсутствует обязательный инструмент: $tool" >&2
  exit 1
}
done

git rev-parse HEAD >/dev/null 2>&1 || {
  echo 'СТОП: в репозитории нет baseline-коммита' >&2
  exit 1
}

# Destructive recovery не умеет доказывать сохранность данных внутри вложенного Git.
# Такая топология требует отдельного интегратора, а не более смелого reset.
if [ -n "$(git submodule status --recursive 2>/dev/null || true)" ] ||
   [ -n "$(find . -mindepth 2 -name .git -print -quit 2>/dev/null)" ]
then
  echo 'СТОП: nested Git repository/submodule не поддерживается этой процедурой' >&2
  exit 1
fi

# Маркер вне рабочего дерева не даёт принять временный HEAD прерванного запуска
# за новую исходную точку.
TXN_LOCK=$(git rev-parse --git-path harness-integration-lock)
TXN_CANDIDATE="$TXN_LOCK.$$"
test ! -e "$TXN_CANDIDATE" || { echo 'СТОП: временный lock path занят' >&2; exit 1; }
mkdir "$TXN_CANDIDATE"
TXN_STATE="$TXN_CANDIDATE/state"
BASELINE=$(git rev-parse HEAD)
printf 'pid=%s\n' "$$" > "$TXN_CANDIDATE/owner"
printf 'baseline=%s\ncore_hooks_changed=0\nphase=step-0\n' "$BASELINE" > "$TXN_STATE"
if ! mv "$TXN_CANDIDATE" "$TXN_LOCK" 2>/dev/null
then
  rm -f -- "$TXN_CANDIDATE/state" "$TXN_CANDIDATE/owner"
  rmdir "$TXN_CANDIDATE"
  echo "СТОП: интеграция уже идёт либо оставила stale lock: $TXN_LOCK" >&2
  echo '→ сделай: выполни протокол «Восстановление после прерывания» ниже' >&2
  exit 1
fi
TXN_STATE="$TXN_LOCK/state"

# Поверх существующего .harness этот скилл не запускается: точечное обновление — другая задача
if [ -e .harness ]
then
  echo 'СТОП: .harness уже существует — не раскатывай боилерплейт поверх него' >&2
  ABORTED_LOCK="$TXN_LOCK.aborted.$$"
  mv "$TXN_LOCK" "$ABORTED_LOCK"
  rm -f -- "$ABORTED_LOCK/state" "$ABORTED_LOCK/owner"
  rmdir "$ABORTED_LOCK"
  exit 1
fi

# 2. снимок точки крепления ДО правок. После них он невосстановим
git config core.hooksPath || echo '(core.hooksPath не задан)'
HOOKS_DIR=$(git rev-parse --git-path hooks)
HOOK_BEFORE=$(git rev-parse --git-path hooks/pre-commit)
ls -l "$HOOKS_DIR" | grep -v '\.sample' || true
if [ -f "$HOOK_BEFORE" ]
then
  file "$HOOK_BEFORE"
  git hash-object "$HOOK_BEFORE"
else
  echo '(своего pre-commit нет)'
fi

# 3. Выбери путь пробы по конфигурации ЖИВОГО прежнего процесса: тип файла и каталог
#    обязаны попадать в его область. Тот же путь шаг 7 получит через HARNESS_PROJECT_PROBE.
#    Не угадывай по расширению: если область не выводится из конфигурации, остановись.
#    Откат условный: HEAD~ вслепую снёс бы настоящий коммит, если проба отклонена
B=$(git rev-parse HEAD)
remove_step0_owned() {
  owned_path=$1
  owned_oid=$2
  staged_oid=$(git rev-parse --verify ":$owned_path" 2>/dev/null || true)
  worktree_oid=$(git hash-object "$owned_path" 2>/dev/null || true)
  [ -z "$staged_oid" ] || [ "$staged_oid" = "$owned_oid" ] || {
    echo "СТОП: staged probe заменён hook: $owned_path" >&2; exit 1;
  }
  [ -z "$worktree_oid" ] || [ "$worktree_oid" = "$owned_oid" ] || {
    echo "СТОП: worktree probe заменён hook: $owned_path" >&2; exit 1;
  }
  git rm -q --cached --ignore-unmatch -- "$owned_path"
  rm -f -- "$owned_path"
}
CONTROL=__harness_process_control.md
test ! -e "$CONTROL" || { echo "СТОП: путь позитивного контроля занят: $CONTROL" >&2; exit 1; }
printf 'benign harness integration control\n' > "$CONTROL"
CONTROL_OID=$(git hash-object "$CONTROL")
git add -- "$CONTROL"
git commit -m 'проба: позитивный контроль до харнесса' >/dev/null 2>&1 || true
CONTROL_HEAD=$(git rev-parse HEAD)
if [ "$CONTROL_HEAD" = "$B" ]
then
  remove_step0_owned "$CONTROL" "$CONTROL_OID"
  echo 'СТОП: среда не создаёт безобидный commit; отрицательная проба ничего не докажет' >&2
  echo '→ сделай: исправь identity/signing/hooks/права и повтори тот же контроль' >&2
  exit 1
fi
[ "$(git rev-parse "$CONTROL_HEAD^")" = "$B" ] || {
  echo 'СТОП: HEAD позитивного контроля не является дочерним от baseline' >&2; exit 1;
}
[ "$(git rev-parse HEAD)" = "$CONTROL_HEAD" ] || {
  echo 'СТОП: HEAD изменился конкурентно до rollback позитивного контроля' >&2; exit 1;
}
git reset -q --soft "$B"
remove_step0_owned "$CONTROL" "$CONTROL_OID"
PROBE='<каталог настоящих исходников>/__harness_process_probe.<их расширение>'
ROOT=$(git rev-parse --show-toplevel)
python3 -c 'import os,sys
root=os.path.realpath(sys.argv[1]); probe=sys.argv[2]
parts=probe.split("/")
ok=(probe and not os.path.isabs(probe) and all(x not in ("", ".", "..") for x in parts)
    and os.path.commonpath([root, os.path.realpath(os.path.join(root, probe))]) == root
    and os.path.commonpath([root, os.path.realpath(os.path.join(root, os.path.dirname(probe)))]) == root)
raise SystemExit(0 if ok else 1)' "$ROOT" "$PROBE" || {
  echo 'СТОП: путь контрольной пробы выходит из репозитория или содержит ./../' >&2
  exit 1
}
if [ -e "$PROBE" ] || [ -L "$PROBE" ]
then
  echo "СТОП: путь пробы уже занят: $PROBE" >&2
  exit 1
fi
printf 'this is not valid %s ((( \n' "${PROBE##*.}" > "$PROBE"
PROBE_OID=$(git hash-object "$PROBE")
git add -- "$PROBE"
git commit -m 'проба: реакция процесса до харнесса' >/dev/null 2>&1 || true
PROBE_HEAD=$(git rev-parse HEAD)
if [ "$PROBE_HEAD" = "$B" ]
then BEFORE_RESULT=REJECTED; echo 'ОТКЛОНЁН — контрольная проба до установки не проходила'
else
  [ "$(git rev-parse "$PROBE_HEAD^")" = "$B" ] && [ "$(git rev-parse HEAD)" = "$PROBE_HEAD" ] || {
    echo 'СТОП: HEAD project probe не принадлежит текущей транзакции' >&2; exit 1;
  }
  BEFORE_RESULT=PASSED
  echo 'ПРОШЁЛ — эта контрольная проба до установки не отклонялась'
  git reset -q --soft "$B"
fi
remove_step0_owned "$PROBE" "$PROBE_OID"

# Повторный позитивный контроль отличает policy-reject от сбоя commit-инфраструктуры,
# который возник между первым control и пробой.
printf 'benign harness integration post-probe control\n' > "$CONTROL"
CONTROL_OID=$(git hash-object "$CONTROL")
git add -- "$CONTROL"
git commit -m 'проба: контроль после реакции процесса' >/dev/null 2>&1 || true
POST_CONTROL_HEAD=$(git rev-parse HEAD)
if [ "$POST_CONTROL_HEAD" = "$B" ]
then
  remove_step0_owned "$CONTROL" "$CONTROL_OID"
  echo 'СТОП: контроль после project probe не прошёл; baseline-реакция недействительна' >&2
  exit 1
fi
[ "$(git rev-parse "$POST_CONTROL_HEAD^")" = "$B" ] &&
  [ "$(git rev-parse HEAD)" = "$POST_CONTROL_HEAD" ] || {
  echo 'СТОП: HEAD post-probe control не принадлежит текущей транзакции' >&2; exit 1;
}
git reset -q --soft "$B"
remove_step0_owned "$CONTROL" "$CONTROL_OID"
printf 'baseline=%s probe=%s spec=invalid-syntax-v1 result=%s\n' \
  "$B" "$PROBE" "$BEFORE_RESULT"

# 4. получить ровно ту ревизию, которую человек передал через доверенный канал
: "${HARNESS_SOURCE_COMMIT:?СТОП: человек должен передать полный commit SHA}"
: "${HARNESS_LICENSE_BASIS:?СТОП: человек должен записать основание права/совместимости}"
[ "${HARNESS_LICENSE_APPROVED:-}" = 1 ] || {
  echo 'СТОП: лицензионная совместимость не подтверждена человеком' >&2
  exit 1
}
[ "${#HARNESS_SOURCE_COMMIT}" -eq 40 ] &&
  ! printf '%s' "$HARNESS_SOURCE_COMMIT" | grep -Eq '[^0-9a-f]' || {
    echo 'СТОП: HARNESS_SOURCE_COMMIT обязан быть полным 40-символьным SHA-1' >&2
    exit 1
  }

SOURCE_URL=https://github.com/uthunderbird/harness-boilerplate
HB=/tmp/harness-boilerplate-integration
test ! -e "$HB" || {
  echo "СТОП: временный путь уже занят: $HB" >&2
  exit 1
}
git clone --no-checkout "$SOURCE_URL" "$HB"
git -C "$HB" cat-file -e "$HARNESS_SOURCE_COMMIT^{commit}" 2>/dev/null || {
  echo 'СТОП: переданный commit отсутствует в доверенном source URL' >&2
  exit 1
}
RESOLVED=$(git -C "$HB" rev-parse "$HARNESS_SOURCE_COMMIT^{commit}")
[ "$RESOLVED" = "$HARNESS_SOURCE_COMMIT" ] || {
  echo 'СТОП: ref разрешился не в переданный commit' >&2
  exit 1
}

# Allowlist и modes проверяются ДО извлечения или исполнения
TREE_MANIFEST="$HB/upstream-manifest.txt"
# Процедура поддерживает только portable ASCII paths. Это намеренная граница формата:
# whitespace/newline/Unicode и case-fold collisions запрещены до tar extraction.
git -C "$HB" ls-tree -rz --name-only "$RESOLVED" -- \
  .harness/scripts .harness/skills .githooks \
  .harness/rules/README.md .harness/reproducers/README.md \
  .harness/retro/README.md .harness/retro/TEMPLATE.md |
python3 -c 'import re,sys
p=[x for x in sys.stdin.buffer.read().split(b"\0") if x]
bad=[]
for x in p:
 parts=x.split(b"/")
 if (not re.fullmatch(rb"[A-Za-z0-9._/-]+",x) or
     any(q in (b"",b".",b"..") for q in parts)):
  bad.append(x)
k={}; dup=[]
for x in p:
 q=x.decode("ascii").casefold()
 if q in k and k[q] != x: dup += [k[q],x]
 k[q]=x
if bad or dup:
 print("non-portable upstream paths:", *(bad+dup), file=sys.stderr); raise SystemExit(1)' || {
  echo 'СТОП: allowlist содержит небезопасный путь' >&2
  exit 1
}
git -C "$HB" ls-tree -r "$RESOLVED" -- \
  .harness/scripts .harness/skills .githooks \
  .harness/rules/README.md .harness/reproducers/README.md \
  .harness/retro/README.md .harness/retro/TEMPLATE.md > "$TREE_MANIFEST"
[ -s "$TREE_MANIFEST" ] || { echo 'СТОП: allowlist пуст' >&2; exit 1; }
if awk '$1 != "100644" && $1 != "100755" {print; bad=1} END {exit bad}' "$TREE_MANIFEST"
then :
else
  echo 'СТОП: в allowlist есть symlink или необычный file mode' >&2
  exit 1
fi
git -C "$HB" ls-tree -r "$RESOLVED" -- \
  .harness/scripts .harness/skills .githooks \
  .harness/rules/README.md .harness/reproducers/README.md \
  .harness/retro/README.md .harness/retro/TEMPLATE.md | cmp - "$TREE_MANIFEST" || {
    echo 'СТОП: manifest изменился между построением и использованием' >&2
    exit 1
  }
MANIFEST_OID=$(git hash-object "$TREE_MANIFEST")

# Allowlist экспортируется из pinned commit один раз; позднее clone больше не источник байт.
mkdir "$HB/export"
ARCHIVE="$HB/allowlist.tar"
git -C "$HB" archive "$RESOLVED" \
  .harness/scripts .harness/skills .githooks \
  .harness/rules/README.md .harness/reproducers/README.md \
  .harness/retro/README.md .harness/retro/TEMPLATE.md > "$ARCHIVE"
tar -xf "$ARCHIVE" -C "$HB/export"

# Лицензия — решение человека, не догадка агента. Неизвестность/несовместимость = STOP.
NOTICE_PATHS=$(git -C "$HB" ls-tree -r --name-only "$RESOLVED" \
  | grep -E '^(LICENSE|COPYING|NOTICE)([A-Za-z0-9._-]*)$' || true)
printf 'source: %s\ncommit: %s\nnotice files:\n%s\n' \
  "$SOURCE_URL" "$RESOLVED" "${NOTICE_PATHS:-(нет)}"

# 5. первый постоянный артефакт — отчёт; затем только машинерия и пустые каркасы состояния
mkdir .harness
printf '# Интеграция харнесса\n\n' > .harness/integration-report.md
# В target попадают байты из единственного проверенного export, не из mutable clone
cp -R "$HB/export/.harness/scripts" .harness/
cp -R "$HB/export/.harness/skills" .harness/
mkdir .harness/rules .harness/reproducers .harness/retro
git -C "$HB" show "$RESOLVED:.harness/rules/README.md" > .harness/rules/README.md
git -C "$HB" show "$RESOLVED:.harness/reproducers/README.md" > .harness/reproducers/README.md
git -C "$HB" show "$RESOLVED:.harness/retro/README.md" > .harness/retro/README.md
git -C "$HB" show "$RESOLVED:.harness/retro/TEMPLATE.md" > .harness/retro/TEMPLATE.md
printf '# Наблюдения\n' > .harness/observations.md
{
  printf '# Upstream manifest\nsource %s\ncommit %s\nmanifest-oid %s\nlicense-basis %s\n' \
    "$SOURCE_URL" "$RESOLVED" "$MANIFEST_OID" "$HARNESS_LICENSE_BASIS"
  cat "$TREE_MANIFEST"
} > .harness/upstream-manifest.txt

if [ -n "$NOTICE_PATHS" ]; then
  mkdir .harness/upstream-notices
  for n in $NOTICE_PATHS; do
    git -C "$HB" show "$RESOLVED:$n" > ".harness/upstream-notices/$n"
  done
fi
```

Пункт 3 фиксирует только наблюдаемое: конкретная проба прошла или была отклонена. Он не
доказывает, что hook-процесс отсутствует или исправен целиком: hook может не смотреть этот
путь или тип файла. На шаге 7 сравнивается **та же реакция**, а устройство прежнего процесса
выясняется чтением его точки и конфигурации на шаге 1.

Допиши в уже созданный `.harness/integration-report.md` безопасное доказательство каждого
пункта. Сырые конфиги и выводы не копируй: команды записывай с заменой значений секретов на
`<REDACTED>`, переменные — только именами, пути — относительно корня, вывод — exit status и
минимальная санитизированная строка. Если безопасная цитата невозможна, запиши путь
источника, его хеш и результат проверки без содержимого.

```markdown
# Интеграция харнесса

## Состояние до (шаг 0)
- source URL: <канонический URL>
- source commit: <полный SHA, переданный человеком; совпал с resolved>
- upstream manifest: <путь и sha256>
- license basis: <санитизированное подтверждение человека>
- preserved notices: <список или «в source отсутствуют»>
- core.hooksPath: <вывод или «не задан»>
- pre-commit path (`git rev-parse --git-path`): <путь, тип, mode, hash, владелец или «нет»>
- baseline commit: <полный SHA из шага 0>
- project probe path: <относительный путь; тот же передать шагу 7>
- project probe spec: invalid-syntax-v1
- project probe before result: <REJECTED / PASSED; ровно одна строка>
```

Строка о реакции на контрольную пробу — самая ценная во всём отчёте. Без неё шаг 7 не
отличит сохранённое поведение от регрессии.

---

## 1. Прочитать проект

Не формальность: развилки ниже решаются тем, что ты здесь увидишь. Каждый пункт —
строка в отчёте; без всех пяти строк к шагу 2 не переходи.

| Что выяснить | Где смотреть | Строка отчёта |
|---|---|---|
| менеджер хуков | `.pre-commit-config.yaml`, `.husky/`, `lefthook.yml` + снимок шага 0 | какой, и **занята ли им точка** |
| команда линтера | фактический CI → task runner / build manifest → документированная локальная команда → подтверждённый ручной запуск | санитизированная команда, источник, exit status |
| команда тестов | тот же порядок источников | санитизированная команда, источник, exit status |
| инструкции агенту | `AGENTS.md`, `CLAUDE.md`, `.cursor/rules` | какие есть (их не заменяют, в них дописывают) |
| свой свод практик | `docs/`, `design/`, `adr/`, `bible/` | есть или нет — от этого зависят шаги 5 и 6 |

**Занята ли точка** — отдельный вопрос, и по наличию конфига он не решается: если
`pre-commit install` не запускался, точка свободна при живом `.pre-commit-config.yaml`.
Читай снимок шага 0:

| Снимок | Ветка шага 2 |
|---|---|
| `core.hooksPath` задан | точку **уже** кто-то забрал — разберись кто, прежде чем трогать |
| путь из `git rev-parse --git-path hooks/pre-commit` есть и написан менеджером | **менеджер** |
| тот же путь есть и написан руками | **голый скрипт** |
| ни того ни другого | **свободна** |

Если `core.hooksPath` уже задан, но его владельцем является самописный raw hook, а не
поддерживаемый manager, процедура останавливается. Она не умеет одновременно сохранить
неверсионированную точку raw hook и доказать её точное восстановление; не переклассифицируй
такую топологию в «голый скрипт» или «свободна».

Источник команды выбирается по первому существующему уровню: CI может быть GitHub Actions,
GitLab CI, Jenkins, Buildkite или другим реально работающим конфигом; затем идут `Makefile`,
`justfile`, package scripts и build manifests; затем документация. Команда-кандидат без
успешного ручного прогона не считается найденной.

**Монорепо.** Продолжай только если найден одна корневая команда-агрегатор для lint и одна
для test, которые реально обходят все workspace/package roots. Запиши перечень корней и
вывод агрегаторов. Если хотя бы для одного компонента нужен отдельный working directory,
runtime или сервис и общего проверенного агрегатора нет — `СТОП` до активации hook: этот
скилл не обещает собрать матрицу монорепо без отдельного адаптера.

До активации измерь время полного `gate.sh`. Бюджет pre-commit задаёт человек; если полный
lint/test-агрегатор в него не укладывается, вынеси медленную часть в CI/manual gate и получи
отдельное решение человека о суженном commit-gate. Молчаливо превращать каждый commit в
полный многоминутный прогон нельзя. Запиши измеренную команду, wall time и согласованный
бюджет в отчёт.

---

## 2. Развилка А — куда вешать гейт

Точка крепления в git **одна**, цепочки нет. Старшинство не по порядку записи: если задан
`core.hooksPath`, каталог `.git/hooks` не смотрится **вовсе**, что бы туда ни положили.

**Ветка «менеджер».** Гейт становится его записью. Не забирай `core.hooksPath` — это
выключит менеджер целиком, без единого слова, и его проверки перестанут бежать. Для
`pre-commit`:

Ниже проверенный рецепт только для `pre-commit`. Для Husky и Lefthook здесь заданы
инварианты — сохранить их точку и встроить гейт в существующую цепь, — но конфигурационный
рецепт не проверялся. Для любого другого менеджера контракт адаптера один: существующий
pre-commit entry point остаётся владельцем точки; он последовательно вызывает прежние
проверки и `sh .harness/scripts/gate.sh`; ненулевой код любого вызова сохраняется. До
активации отдельно докажи нормальный коммит, отказ гейта и прежнюю контрольную реакцию.
Не можешь построить и прогнать такой адаптер — `СТОП`, конфигурацию hook не меняй и запиши
неподдерживаемый менеджер в отчёт.

Recovery-контракт разрешает manager-ветке менять только отслеживаемый конфиг менеджера:
`reset --hard` возвращает его baseline. Generated hook и другое состояние в `.git/`
обязаны остаться byte-for-byte равны снимку шага 0. Если менеджер требует менять состояние
вне worktree, `СТОП`: сначала нужен отдельно проверенный rollback-адаптер, а его команда и
baseline hash записываются в transaction marker.

Выбрав manager-ветку, до её правки допиши в marker `branch=manager`,
`baseline_hook_oid=<hash из шага 0>` и `baseline_core_hooks_path=<точное значение>`;
для остальных веток — `branch=raw-or-free`. Значение `core.hooksPath` с переводом строки
или пустое значение manager-веткой не поддерживается: сериализовать его построчно небезопасно.

```yaml
  - repo: local
    hooks:
      - id: harness-gate
        name: harness gate
        entry: sh .harness/scripts/gate.sh
        language: system
        pass_filenames: false
        always_run: true
```

Фильтры типов сюда **не копируй**. `types_or: [python, pyi]`, взятый у соседей, заглушит
гейт на коммитах, которые трогают только `.harness/`, документацию и правила — то есть на
половине тех, ради которых он и заводится. `always_run: true` держи как страховку: он
нейтрализует такой фильтр, если его скопируют потом.

Для веток «голый скрипт» и «свободна» только теперь материализуй allowlisted hook:

```sh
test ! -e .githooks || {
  echo 'СТОП: .githooks уже существует — не перезаписывай чужую точку' >&2
  exit 1
}
cp -R /tmp/harness-boilerplate-integration/export/.githooks ./.githooks
```

В ветке «менеджер» `.githooks` не копируется: владельцем остаётся менеджер.

**Ветка «голый скрипт».** Подготовь сохранение прежнего владельца и его вызов первой
строкой нового hook, но `core.hooksPath` пока не меняй. Путь копии не переиспользуется:

```sh
test ! -e .harness/previous-pre-commit.sh || {
  echo 'СТОП: .harness/previous-pre-commit.sh уже существует' >&2
  exit 1
}
HOOK_BEFORE=$(git rev-parse --git-path hooks/pre-commit)
EXPECTED_HOOK_OID='<hash из шага 0>'
[ "$(git hash-object "$HOOK_BEFORE")" = "$EXPECTED_HOOK_OID" ] || {
  echo 'СТОП: прежний hook изменился после снимка шага 0' >&2
  exit 1
}
cp "$HOOK_BEFORE" .harness/previous-pre-commit.sh
[ "$(git hash-object .harness/previous-pre-commit.sh)" = "$EXPECTED_HOOK_OID" ] || {
  echo 'СТОП: копия прежнего hook не совпала со снимком' >&2
  exit 1
}
```
и в `.githooks/pre-commit` перед вызовом гейта — вызов `.harness/previous-pre-commit.sh`.

**Ветка «свободна».** Подготовь `.githooks/pre-commit`; конфигурацию пока не меняй.

**Активацию делай последней — после шагов 3 и 4.** Команды выше только готовят файлы.
Между «гейт включён» и «адаптеры
заполнены» любой твой коммит ловится гейтом, натравленным на чужой стек, а до шага 4 чужой
форматтер успеет переписать вендоренный питон.

`git config` пишет в `.git/config`, файл вне версии. Файлы `.githooks/*` в диффе видны —
невидима **строка активации**, и она же не приезжает с клоном. У соседа харнесс будет
выглядеть установленным и не работать. Строка отчёта: какая ветка, и какой командой сосед
включает у себя.

Гейт во всех ветках вызывается через `.harness/scripts/gate.sh`, не мимо. Не разворачивай
его содержимое в хук: `gate.sh` понимает `HARNESS_GATE_SKIP=1`, которым проверка шага 7
глушит гейт, чтобы отличить живого соседа от подмены. Хук, зовущий проверки напрямую, этой
переменной не увидит, и вторая проба тихо станет ложно-зелёной.

**Pre-execution checkpoint.** До первого запуска любого привезённого `sh`/Python-файла
сверь каждый материализованный blob с manifest:

```sh
awk '$2 == "blob" {print $3, $4}' .harness/upstream-manifest.txt |
while read -r oid rel; do
  if [ ! -e "$rel" ]; then
    case "$rel" in .githooks/*) continue ;; esac
    echo "СТОП: allowlisted blob не материализован: $rel" >&2
    exit 1
  fi
  [ "$(git hash-object "$rel")" = "$oid" ] || {
    echo "СТОП: fetched blob не совпал с manifest: $rel" >&2
    exit 1
  }
done || {
  echo 'СТОП: materialized blobs не прошли manifest checkpoint' >&2
  exit 1
}
```

Покажи человеку список manifest, license basis/notices и полный import diff. Продолжай
только после явного подтверждения состава; это отдельное блокирующее место. Проверка SHA
без просмотра состава доказывает идентичность доверенному commit, но не уместность каждого
его исполняемого файла в целевом проекте.

Все файлы, запускаемые через `sh`, обязаны быть POSIX `sh`, а не Bash/Zsh-скриптами.
Перед первым запуском выполни `sh -n` для `.harness/scripts/*.sh` и
`.githooks/pre-commit` (если он материализован). Bash-only адаптер не передавай в `sh`:
либо перепиши его в POSIX, либо вынеси прямой вызов его инструмента в POSIX-обёртку.

---

## 3. Адаптеры — вписать команды инструментов

`.harness/scripts/lint.sh` и `test.sh` приезжают заполненными **чужим стеком** — тем, что
у боилерплейта. Они не пусты, поэтому не жалуются, и гейт от них зелёный, ничего в твоём
проекте не проверив. Это не гипотеза: в первой интеграции гейт печатал `ok разбор
исходников`, проверив 9 файлов внутри `.harness/` при 93 файлах проекта, не увиденных вовсе.

Контракт адаптера: это исполняемый shell-файл, который переходит в корень репозитория,
запускает каноническую команду инструмента напрямую и возвращает её код. Замени команды
боилерплейта, сохранив `set -eu`, переход в корень и передачу ненулевого exit status. Не
зашивай успешный код и не проверяй только наличие команды в чужом конфиге.

CI/task-runner конфиг здесь недоверенный **код**, а не строка для копирования. Построй
минимальный argv-вызов известного lint/test-инструмента вручную. Без отдельного аудита и
подтверждения запрещены `eval`, `sh -c`, command substitution, sourcing, конвейеры загрузки,
редиректы и побочные команды. В отчёт идёт санитизированное argv-представление, не строка,
которую можно replay как shell.

Если стек проекта совпадает со стеком боилерплейта (python), соблазн оставить как есть
особенно велик — команды выглядят осмысленно. Не оставляй: впиши семантически ту же
команду, что
выписал на шаге 1 из канонического источника. Если источник — CI, расхождение означает
«прошло локально, упало в CI».

В отчёт — источник, найденная команда, команда адаптера и результат ручного прогона.
При наличии канонической CI-команды адаптер обязан сохранять её исполняемую семантику;
секретные значения в отчёте при этом всегда редактируются.

**Зови инструменты, а не менеджер хуков.** `uv run ruff check .` — да.
`uv run pre-commit run --all-files` — никогда: если гейт при этом висит внутри `pre-commit`,
получится неограниченная рекурсия (проверено: семь уровней и дальше). Правило безусловное,
оно верно при любом исходе развилки А.

Инструменты зовутся напрямую даже когда те же команды уже стоят в менеджере хуков, и
двойной прогон — приемлемая цена: `gate.sh` запускают руками, и адаптер, лишь проверяющий
наличие делегата в конфиге, в ручном прогоне ответит «всё на месте» при сломанном коде.

**Если линтера или тестов в проекте нет** — не выдумывай их и не оставляй заглушку молчащей.
Запиши отсутствующий класс проверки в отчёт и останови интеграцию **до шага 4а**. Не
активируй постоянно падающий гейт и не выдавай отсутствие инструмента за завершённую
установку. Возобновление требует либо появления канонической команды, либо отдельного
решения человека изменить обязательный набор проверок.

---

## 4. Развести харнесс и чужой линтер

Сначала установи фактическую область линтера проекта. Если он не рассматривает Python,
запиши в отчёт `не применимо` и команду/конфигурацию, которой это проверено. Если
рассматривает, он увидит питон харнесса и может начать судить его по здешним правилам, а
форматтер — переписывать. Приводить вендоренный код к местному стилю нельзя: каждое
обновление сверху станет конфликтом. Для Ruff исключи каталог — и проверь **две** вещи,
каждая из которых молча не срабатывает.

**Таблица.** В `pyproject.toml` ключ обязан быть под `[tool.ruff]`; на верхнем уровне ruff
его **молча игнорирует**. В отдельном `ruff.toml` / `.ruff.toml` — тот же ключ без заголовка.

```toml
[tool.ruff]
extend-exclude = [".harness"]
```

**Явные имена файлов.** Менеджеры хуков передают линтеру пути списком, а на явно названные
пути `exclude` у ruff **не действует** — нужен `--force-exclude`. Официальный хук
`ruff-pre-commit` его содержит, самописный `entry: ruff check` — нет.

```sh
<команда линтера из шага 1> .harness/scripts/checks/*.py
```

Молчит — исключение работает. Ругается — добавь `--force-exclude` в `entry`.

Затем верни харнессу собственный разбор его питона — в `lint.sh`, рядом с командами
проекта. Код, который чужой линтер намеренно не смотрит, обязан смотреть кто-то другой.

Строка отчёта: где стоит исключение и чем проверено, что оно действует.

### 4а. Активировать подготовленный hook

Только теперь, когда адаптеры заполнены и разведение с чужим линтером проверено. Команда
зависит от ветки шага 2 — общей активации у них нет.

**Ветка «менеджер».** `core.hooksPath` не меняй. Убедись по снимку шага 0, что менеджер
по-прежнему владеет той же точкой, а его конфиг содержит запись `harness-gate`; фактический
запуск на коммите докажет шаг 7.

**Ветки «голый скрипт» и «свободна».** Активируй подготовленный hook:

```sh
test -x .githooks/pre-commit || {
  echo 'СТОП: .githooks/pre-commit отсутствует или не исполняемый' >&2
  exit 1
}
sh .githooks/pre-commit
printf 'core_hooks_changed=1\nphase=activating-hook\n' >> "$TXN_STATE"
git config core.hooksPath .githooks
test "$(git config --get core.hooksPath)" = .githooks || {
  echo 'СТОП: core.hooksPath не равен .githooks; выполни recovery' >&2
  exit 1
}
printf 'phase=hook-active\n' >> "$TXN_STATE"
```

Ручной запуск обязан пройти **до** изменения конфигурации. Если он упал, `core.hooksPath`
остаётся прежним. После записи сразу проверь `git config --get core.hooksPath`: вывод обязан
быть ровно `.githooks`; иной вывод — остановка, а не повод продолжать установку.

**Откат установки.** До финального коммита ничего не стирай: перемести новые `.harness/`
и `.githooks/` в `mktemp -d`, а отслеживаемые файлы восстанови только по поимённому списку
из integration diff. В ветке менеджера удали добавленную запись его штатным способом;
`core.hooksPath` не менялся. В ветках «голый скрипт»/«свободна» выполни
`git config --unset-all core.hooksPath`: по снимку шага 0 он там не был задан, а прежний
hook по пути из `git rev-parse --git-path hooks/pre-commit` остался на месте. После коммита файловый откат — отдельный
`git revert <integration-commit>`, после него локальный `core.hooksPath` всё равно снимается
явно той же командой. Любой откат заверши сравнением со снимком шага 0 и чистым status.

**Восстановление после прерывания.** Не возобновляй частичный прогон и не удаляй маркер
вручную. Исходное дерево было чистым и рабочая копия эксклюзивна, поэтому все изменения
после записанного baseline принадлежат этой интеграции. Сначала сохрани их в отдельной
ветке восстановления, затем верни baseline:

```sh
set -eu
TXN_LOCK=$(git rev-parse --git-path harness-integration-lock)
TXN_STATE="$TXN_LOCK/state"
test -f "$TXN_STATE" || { echo 'СТОП: маркера транзакции нет' >&2; exit 1; }
if [ -n "$(git submodule status --recursive 2>/dev/null || true)" ] ||
   [ -n "$(find . -mindepth 2 -name .git -print -quit 2>/dev/null)" ]
then
  echo 'СТОП: recovery при nested Git repository/submodule не поддерживается' >&2
  exit 1
fi
BASELINE=$(sed -n 's/^baseline=//p' "$TXN_STATE" | tail -1)
git cat-file -e "$BASELINE^{commit}" || { echo 'СТОП: baseline повреждён' >&2; exit 1; }
EXPECTED_TREE=$(sed -n 's/^expected_tree=//p' "$TXN_STATE" | tail -1)
MANAGER_STATE_OK=1
if grep -qx 'branch=manager' "$TXN_STATE"
then
  BASELINE_HOOK_OID=$(sed -n 's/^baseline_hook_oid=//p' "$TXN_STATE" | tail -1)
  BASELINE_CORE_HOOKS_PATH=$(sed -n 's/^baseline_core_hooks_path=//p' "$TXN_STATE" | tail -1)
  CURRENT_HOOK_OID=$(git hash-object "$(git rev-parse --git-path hooks/pre-commit)" 2>/dev/null || true)
  CURRENT_CORE_HOOKS_PATH=$(git config --get core.hooksPath || true)
  [ "$CURRENT_HOOK_OID" = "$BASELINE_HOOK_OID" ] &&
    [ "$CURRENT_CORE_HOOKS_PATH" = "$BASELINE_CORE_HOOKS_PATH" ] || MANAGER_STATE_OK=0
fi
if [ -n "$EXPECTED_TREE" ] &&
   [ "$(git rev-parse HEAD^ 2>/dev/null || true)" = "$BASELINE" ] &&
   [ "$(git rev-parse HEAD^{tree} 2>/dev/null || true)" = "$EXPECTED_TREE" ] &&
   [ "$MANAGER_STATE_OK" -eq 1 ]
then
  FINISHED_LOCK="$TXN_LOCK.finished.$$"
  mv "$TXN_LOCK" "$FINISHED_LOCK"
  rm -f -- "$FINISHED_LOCK/state" "$FINISHED_LOCK/owner"
  rmdir "$FINISHED_LOCK"
  echo 'Финальный integration commit уже создан; удалён только завершённый маркер.'
  exit 0
fi
RECOVERY_BRANCH="harness-integration-recovery-$(date +%s)"
git add -A
git -c commit.gpgSign=false commit --no-verify --allow-empty \
  -m 'recovery: interrupted harness integration'
RECOVERY_HEAD=$(git rev-parse HEAD)
test "$RECOVERY_HEAD" != "$BASELINE" || {
  echo 'СТОП: recovery commit не создан; reset запрещён' >&2
  exit 1
}
git branch "$RECOVERY_BRANCH" HEAD
test "$(git rev-parse "$RECOVERY_BRANCH")" = "$RECOVERY_HEAD" || {
  echo 'СТОП: recovery-ветка не указывает на сохранённую работу; reset запрещён' >&2
  exit 1
}
git status --short
printf 'СТОП: частичная работа сохранена в %s; автоматический reset запрещён.\n' \
  "$RECOVERY_BRANCH" >&2
printf '→ стоп: проверь provenance recovery commit и внешнее hook/config state; только затем\n' >&2
printf '  вручную верни baseline %s, восстанови core.hooksPath по снимку и сними lock.\n' \
  "$BASELINE" >&2
exit 1
```

Команда с `--no-verify` допустима только здесь: повреждённый частичный hook иначе может
запереть сам путь восстановления. Recovery-ветка только сохраняет снимок; процедура
намеренно не выполняет `reset --hard`/`git clean`, потому что cooperative lock не доказывает
отсутствие постороннего Git-актора. Lock снимается вручную только после проверки provenance.

---

## 5. Развилка Б — какие проверки не брать

Проверки харнесса написаны под провалы **того** проекта. Здесь часть из них не про что.

```sh
ls .harness/scripts/checks/
```

Пройди список **поимённо** и по каждой вынеси вердикт с одной строкой обоснования.
Проверка едет только когда выполнены все четыре условия:

1. назван локальный инвариант целевого проекта, который она защищает;
2. назван локальный артефакт, к которому инвариант применим;
3. построен воспроизводимый плохой вход, на котором **эта** проверка падает ожидаемым текстом;
4. после исправления того же входа проверка проходит.

Нет любого пункта — применимость не доказана, проверка не едет. Для каждого файла запиши
в отчёт `инвариант · артефакт · команда плохого/хорошего прогона · вердикт`; пропущенное
имя читается как «не решал». Исходник проверки и связанный парный блок самотеста показывают
механизм, но не доказывают, что его класс существует в целевом проекте.

Ветка не теоретическая. В первой же интеграции `polish_artifacts.py` напечатал
`→ команда: rm` по двум **отслеживаемым** файлам целевого проекта, один — 229 строк
в истории третий месяц. Там таких файлов четырнадцать, и они не леса, а документы: проект
хранит ledger'ы полировки намеренно. Агент, послушный императиву, удалил бы закоммиченную
работу.

Отсюда правило без исключений: **прежде чем выполнить императив проверки, посмотри на
файлы, которые он называет.** Отслеживаются — останавливайся и думай, чей это класс.

Снятие проверки — три правки, все обязательны:

```sh
# 1. строка run из диспетчера .harness/scripts/gate.sh (нижний блок после echo "gate:")
grep -n '<имя>' .harness/scripts/gate.sh
# 2. файл проверки
rm .harness/scripts/checks/<имя>.py
# 3. парный блок этой проверки внутри ветки SELFTEST того же gate.sh
#    Удали его и поправь литерал в строке «самотест пройден: N пар входов на M проверках»:
#    N — число вызовов pair, M — число разных файлов checks/*.py в этих вызовах.
grep -n 'pair \|самотест пройден:' .harness/scripts/gate.sh
sh .harness/scripts/gate.sh --self-test | tail -2
```

После правок `N` обязан совпасть с `grep -Ec '^[[:space:]]*pair ' .harness/scripts/gate.sh`.
`M` пересчитай по уникальным вторым аргументам вызовов `pair`; адаптеры
`lint.sh`/`test.sh` в это число не входят.

---

## 6. Развилка В — что не едет никогда

В боилерплейте есть файлы, которые выглядят частью инструмента, а являются состоянием
**того** проекта.

Здесь `observations.md` — накопитель ещё не созревших повторов; `rules/` — нормы, рождённые
из провалов проекта; `reproducers/` — их плохие входы и кейсы; `retro/` — записи кадансной
ревизии; «Текущие долги» — проектные незакрытые механизмы в `HARNESS_MODEL.md`;
`thresholds.sh` — числовые пороги предупреждений перед ходом. Это состояние исходного
проекта, не переносимая машинерия.

| Не бери | Возьми вместо |
|---|---|
| `.harness/observations.md` — чужая копилка | пустой файл с заголовком |
| `.harness/retro/*.md` | только `TEMPLATE.md` и `README.md` |
| таблицу «Текущие долги» в `HARNESS_MODEL.md` | раздел с пустой таблицей |
| `.harness/rules/*.md` | ничего: правила рождаются из провалов **этого** проекта |

Пустой каталог правил здесь нормален и долгом не является.

**Пороги.** `.harness/scripts/thresholds.sh` откалиброван по чужой истории. Померь свою:

```sh
git log --format=%h -100 | while read -r c; do git show --name-only --format= "$c" | grep -c . ; done \
  | sort -n | awk '{a[NR]=$1} END {print "медиана:", a[int(NR/2)+1], "· p90:", a[int(NR*0.9)+1]}'
```

`HC_FILES_SOFT` в `thresholds.sh` стоит на p90. Расходится с твоим больше чем вдвое —
поставь свой p90 и запиши оба числа в отчёт. Иначе хайлайт будет либо молчать всегда,
либо кричать на каждом ходу, и его перестанут читать.

**Если у проекта есть свой свод практик** (шаг 1), не сливай его с харнессом молча.
Понятия часто совпадают почти дословно — «практика без опоры = гипотеза» и «правило без
входа — суеверие» это одно и то же под двумя именами. Вынеси человеку вопрос, кто здесь
главный, и не решай за него. До ответа берётся только **машинерия**: `.harness/scripts/`,
`.harness/skills/`, `.githooks/`, пустые каркасы `.harness/{rules,reproducers,retro}/`.
Словарь — `HARNESS_MODEL.md`, `AGENTS.md` — ждёт ответа.

Если собственного свода нет, перенеси `HARNESS_MODEL.md` как словарь харнесса. `AGENTS.md`
не перезаписывай: отсутствующий файл можно взять целиком, а в существующий добавь правила
харнесса отдельным разделом, сохранив прежние инструкции. В отчёте назови оба исхода и
проверь diff — исчезновение прежнего текста означает ошибку слияния.

---

## 7. Доказать и дописать отчёт

```sh
set -eu
# Verifier требует чистого дерева. Зафиксируй просмотренный integration diff во
# временном снимке; baseline хранится в маркере транзакции.
TXN_LOCK=$(git rev-parse --git-path harness-integration-lock)
TXN_STATE="$TXN_LOCK/state"
BASELINE=$(sed -n 's/^baseline=//p' "$TXN_STATE" | tail -1)
test "$(git rev-parse HEAD)" = "$BASELINE" || {
  echo 'СТОП: HEAD ушёл от baseline — выполни восстановление после прерывания' >&2
  exit 1
}
git status --short
git add -A
git diff --cached --check
git commit -m 'temporary: verify harness integration'
TEMP_VERIFY_HEAD=$(git rev-parse HEAD)
[ "$(git rev-parse "$TEMP_VERIFY_HEAD^")" = "$BASELINE" ] || {
  echo 'СТОП: temporary verifier commit не является дочерним от baseline' >&2; exit 1;
}
printf 'phase=verifying\n' >> "$TXN_STATE"

VERIFY_RC=0
PROJECT_PROBE='<путь из шага 0>'
HARNESS_PROJECT_PROBE=$PROJECT_PROBE sh .harness/scripts/verify-integration.sh || VERIFY_RC=$?
[ "$(git rev-parse HEAD)" = "$TEMP_VERIFY_HEAD" ] || {
  echo 'СТОП: HEAD изменился конкурентно; temporary commit не откатывается' >&2; exit 1;
}
git reset --soft "$BASELINE"
test "$(grep -Ec '^- project probe before result: (REJECTED|PASSED)$' \
  .harness/integration-report.md)" -eq 1 || {
  echo 'СТОП: baseline report содержит ноль либо несколько результатов project probe' >&2; exit 1;
}
grep -qxF -- "- baseline commit: $BASELINE" .harness/integration-report.md || {
  echo 'СТОП: baseline commit отчёта не совпадает с transaction marker' >&2; exit 1;
}
grep -qxF -- "- project probe path: $PROJECT_PROBE" .harness/integration-report.md || {
  echo 'СТОП: путь project probe не совпадает со снимком шага 0' >&2; exit 1;
}
grep -qxF -- '- project probe spec: invalid-syntax-v1' .harness/integration-report.md || {
  echo 'СТОП: неизвестная спецификация baseline project probe' >&2; exit 1;
}
VERIFICATION_OUTCOME=matched
case "$VERIFY_RC" in
  0)
     printf '%s\n' '- контрольная проба после установки: ОТКЛОНЕНА' \
       >> .harness/integration-report.md
     grep -qxF -- '- project probe before result: REJECTED' \
       .harness/integration-report.md || {
       printf '%s\n' '- сравнение контрольной пробы: REGRESSION — до установки прошла, после отклонена' \
         >> .harness/integration-report.md
       VERIFICATION_OUTCOME=regression
     }
     if [ "$VERIFICATION_OUTCOME" = matched ]; then
       printf '%s\n' '- сравнение контрольной пробы: MATCHED — отклонена до и после' \
         >> .harness/integration-report.md
       printf '%s\n' '- сравнение со снимком шага 0: MATCHED — отклонена до и после' \
         >> .harness/integration-verified.md
     fi
     ;;
  1) echo 'СТОП: нарушение харнесса не было доказано отклонено; вернись к развилке А' >&2; exit 1 ;;
  2)
     printf '%s\n' '- контрольная проба после установки: ПРОШЛА' \
       >> .harness/integration-report.md
     grep -qxF -- '- project probe before result: PASSED' \
       .harness/integration-report.md || {
       printf '%s\n' '- сравнение контрольной пробы: REGRESSION — до установки отклонена, после прошла' \
         >> .harness/integration-report.md
       VERIFICATION_OUTCOME=regression
     }
     if [ "$VERIFICATION_OUTCOME" = matched ]; then
       printf '%s\n' '- сравнение контрольной пробы: MATCHED — прошла до и после' \
         >> .harness/integration-report.md
       printf '%s\n' '- сравнение со снимком шага 0: MATCHED — прошла до и после' \
         >> .harness/integration-verified.md
     fi
     ;;
  3) echo 'СТОП: контрольная проба проекта не была построена; исправь путь и повтори' >&2; exit 1 ;;
  4) echo 'СТОП: проба харнесса не была staged; исправь index/ignore и повтори' >&2; exit 1 ;;
  5) echo 'СТОП: инфраструктура commit отказала; исправь её и повтори' >&2; exit 1 ;;
  *) echo "СТОП: инфраструктурный сбой verifier, exit $VERIFY_RC" >&2; exit 1 ;;
esac
test -f .harness/integration-verified.md || {
  echo 'СТОП: verifier не оставил обязательный stamp' >&2
  exit 1
}
git add -- .harness/integration-report.md .harness/integration-verified.md
if [ "$VERIFICATION_OUTCOME" = regression ]; then
  printf 'phase=regression\n' >> "$TXN_STATE"
  echo 'СТОП: реакция контрольной пробы изменилась; факт записан и staged' >&2
  exit 1
fi
printf 'phase=verified\n' >> "$TXN_STATE"
test "$(git rev-parse HEAD)" = "$BASELINE" || {
  echo 'СТОП: временный verifier commit не откатился' >&2; exit 1;
}
git diff --cached --name-only | grep -qxF .harness/integration-verified.md || {
  echo 'СТОП: integration-verified.md не находится в финальном staged diff' >&2; exit 1;
}
git diff --cached --name-only | grep -qxF .harness/integration-report.md || {
  echo 'СТОП: обновлённый integration-report.md не находится в staged diff' >&2; exit 1;
}
test "$(tail -1 "$TXN_STATE")" = 'phase=verified' || {
  echo 'СТОП: фаза verified не записалась в transaction marker' >&2; exit 1;
}
```

Требует чистого дерева, откатывает свои пробы, оставляет `.harness/integration-verified.md`.
Выход шестисостоячный: `0` — гейт держит и контрольная проба отклонена; `1` — гейт не
держит; `2` — гейт держит, но контрольная проба прошла, поэтому нужен снимок «до»;
`3` — контрольная проба проекта не построена; `4` — не построена проба харнесса.
`5` — построенная проба отклонена, но повторный позитивный контроль тоже не прошёл:
это инфраструктурный отказ, сравнение недействительно.
Отвечает на два вопроса:

1. **коммит, нарушающий харнесс, отклонён?** Нет — гейт не подключён, вернись к развилке А.
2. **контрольная проба отклонена при выключенном гейте?** Нет — сравни с шагом 0. Если та
   же проба и до установки проходила, наблюдаемой регрессии нет, но это не доказательство
   исправности всего прежнего процесса. Если раньше она отклонялась, интеграция погасила
   прежнюю защиту: восстанавливай владельца точки.

Эта развилка решается **только** записью шага 0. Восстановить ответ задним числом нельзя:
точка крепления вне версии, истории у неё нет.

### Техническая готовность

Интеграция готова запросить разрешение на финальный коммит только когда одновременно:

- все обязательные поля отчёта шагов 0–7 заполнены фактическими выводами и командами;
- source commit совпал с переданным SHA; manifest/modes/blob hashes проверены до исполнения;
- право и совместимость переноса подтверждены человеком, применимые notices сохранены;
- найденные канонические lint/test-команды стоят в адаптерах и проходят из корня;
- для монорепо доказано покрытие всех компонентов одним агрегатором, иначе работа остановлена;
- выбранная ветка hook активна, нормальный коммит проходит, а нарушение харнесса отклоняется;
- контрольная проба имеет один путь и содержимое на шагах 0 и 7, а её реакции сопоставлены;
- по каждой перенесённой проверке записаны инвариант и плохой/хороший прогон;
- состояния шага 6 не уехали из боилерплейта, пороги измерены, словарный конфликт разрешён;
- `integration-verified.md` создан, полный diff просмотрен, незнакомых изменений нет;
- веточный откат файлов и `core.hooksPath` записан и проверяем по снимку шага 0.

Перед запросом разрешения отдельным проходом проверь staged diff на токены, credential URL,
inline-секреты, абсолютные пользовательские пути и приватный вывод. Если в проекте есть
secret scanner, прогони и его, но не подменяй им ручную проверку. Найденное санитизируй и
повтори обе проверки; любой остаток — `СТОП`.

До финального verifier допиши отчёт тремя разделами, которые войдут в тот же integration commit:

- **что взято** и куда повешено — какая ветка развилки А и почему именно она;
- **что не взято** и почему — поимённый список со шага 5, всё со шага 6;
- **что осталось открытым** — дыры со шага 3, вопрос про словарь со шага 6.

Раздел «что не взято» пропускать нельзя: без него частичная установка неотличима от полной,
а через месяц никто не вспомнит, проверка отсутствует по решению или по недосмотру.

Финальный `verify-integration.sh` запускается **после** всех ремонтов, отчёта и secret-review.
Сразу после него повторно сними `git config --show-origin --get-all core.hooksPath`, resolved
hook path/type/mode/hash и hashes `gate.sh`, `lint.sh`, `test.sh`; занеси tuple в отчёт и
больше не меняй код/конфигурацию. Любая последующая правка или внешняя мутация инвалидирует
`integration-verified.md`: повтори secret-review, verifier и tuple перед разрешением.

Невыполненный пункт означает не «частично готово», а `СТОП` до его закрытия или отдельного
решения человека сузить обязательство. После готовности перескажи отчёт и запроси разрешение;
сам финальный commit до ответа не создавай.

После разрешения непосредственно перед финальным commit проверь, что HEAD всё ещё равен
baseline, staged diff совпадает с просмотренным и tuple не изменился. Запиши
`expected_tree=$(git write-tree)` в `$TXN_STATE`, создай ровно один финальный commit и
проверь, что его parent равен baseline, а tree — expected tree. Только затем удали
`$TXN_STATE`. Если процесс оборвётся после commit, recovery узнает завершённый commit по
этой паре и снимет маркер без отката; до успешного commit маркер является частью отката.

```sh
set -eu
TXN_LOCK=$(git rev-parse --git-path harness-integration-lock)
TXN_STATE="$TXN_LOCK/state"
BASELINE=$(sed -n 's/^baseline=//p' "$TXN_STATE" | tail -1)
test "$(git rev-parse HEAD)" = "$BASELINE" || {
  echo 'СТОП: HEAD не равен baseline перед финальным commit' >&2; exit 1;
}
EXPECTED_TREE=$(git write-tree)
printf 'expected_tree=%s\nphase=committing\n' "$EXPECTED_TREE" >> "$TXN_STATE"
git commit -m 'feat: integrate project harness'
test "$(git rev-parse HEAD^)" = "$BASELINE" || {
  echo 'СТОП: parent финального commit не равен baseline; marker оставлен' >&2; exit 1;
}
test "$(git rev-parse HEAD^{tree})" = "$EXPECTED_TREE" || {
  echo 'СТОП: tree финального commit не совпал с просмотренным; marker оставлен' >&2; exit 1;
}
if grep -qx 'branch=manager' "$TXN_STATE"
then
  BASELINE_HOOK_OID=$(sed -n 's/^baseline_hook_oid=//p' "$TXN_STATE" | tail -1)
  BASELINE_CORE_HOOKS_PATH=$(sed -n 's/^baseline_core_hooks_path=//p' "$TXN_STATE" | tail -1)
  CURRENT_HOOK_OID=$(git hash-object "$(git rev-parse --git-path hooks/pre-commit)" 2>/dev/null || true)
  CURRENT_CORE_HOOKS_PATH=$(git config --get core.hooksPath || true)
  [ "$CURRENT_HOOK_OID" = "$BASELINE_HOOK_OID" ] &&
    [ "$CURRENT_CORE_HOOKS_PATH" = "$BASELINE_CORE_HOOKS_PATH" ] || {
    echo 'СТОП: manager external state изменился; marker оставлен' >&2; exit 1;
  }
fi
FINISHED_LOCK="$TXN_LOCK.finished.$$"
mv "$TXN_LOCK" "$FINISHED_LOCK"
rm -f -- "$FINISHED_LOCK/state" "$FINISHED_LOCK/owner"
rmdir "$FINISHED_LOCK"
```

Зелёная проверка не равна сделанной интеграции. Она видит одну развилку из шести — шаги
3–6 держатся на том, что ты их выполнил, и следа в диффе, кроме отчёта, у них нет. Повторяй
проверку после каждой правки адаптеров или конфига хуков.

---

## Чего этот скилл не делает

Не создаёт финальный коммит интеграции без отдельного разрешения человека. Технические
пробные коммиты шага 0 и верификатора всегда откатываются.

Не переписывает чужой процесс. Если харнесс и здешние практики противоречат друг другу,
это вопрос человеку, а не задача на слияние.

Не рассчитан на повторный прогон поверх существующего `.harness/`. Он уже есть — не
раскатывай сверху: затрёшь снятые проверки и откалиброванные пороги. Сверяйся с отчётом
и правь точечно.

Не проверялся на `husky` и `lefthook`. Развилка А написана по механике git, которая для них
та же, но прогонов не было — скажи об этом, если ставишь в такой проект.
