#!/bin/sh
# Доказательство интеграции. Две пробы плюс контроль. Контроль — безобидный коммит,
# он обязан ПРОЙТИ. Пробы нарушают харнесс и прежний процесс проекта, они обязаны
# быть отклонены.
#
# Зачем факт, а не отчёт. Установка, о которой отчитывается тот, кто её проводил, —
# известный класс провала: «вписал команду» не значит «команда видит код», а
# «хуки включены» не значит «чужие хуки живы».
#
# Зачем контроль. «Коммит не прошёл» — не доказательство: коммит не проходит
# и когда нечего коммитить, и когда не настроен GPG, и когда нет identity, и когда
# сломан любой посторонний хук. Все эти отказы неотличимы от срабатывания проверки.
# Поэтому первым идёт позитивный контроль: заведомо безобидный коммит обязан
# ПРОЙТИ. Прошёл — значит среда коммитить умеет, и следующий отказ вызван пробой,
# а не обстановкой.
#
# Почему проба чужого процесса — синтаксическая ошибка. Форматтер, вызванный из
# хука, чинит лишние пробелы и пропускает коммит; неразбираемый файл не чинит
# никто. И кладётся он рядом с настоящим исходником, а не в корень: корень многие
# линтеры не смотрят, и проба улетела бы в исключённый путь.
#
# Чего скрипт не делает никогда: не трогает файлы, которых не создавал. Ни
# `git add -A`, ни `git reset --hard` здесь нет и быть не может — цена ошибки
# в них выше всей пользы от проверки.
set -eu

ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || {
    echo "не git-репозиторий — проверять нечего" >&2
    echo "  → команда: git init" >&2
    exit 1
}
cd "$ROOT"

MARK=__verify_probe
RULE=".harness/rules/$MARK.md"
OKFILE="$MARK-ok.md"
BEFORE=
STATE=$(git rev-parse --git-path harness-verify-state)
LOCK_DIR=$(git rev-parse --git-path harness-verify-lock)
BAD=
STAMP=.harness/integration-verified.md
OWNED_HEAD=
RULE_OWNED=0
OKFILE_OWNED=0
BAD_OWNED=0
RECEIPT_OWNED=0
RECEIPT_OID=
RULE_OID=
OKFILE_OID=
BAD_OID=
STAMP_TEMP_OWNED=0
STAMP_TEMP_OID=
PRESERVE_STATE=0
LOCK_OWNED=0
NONCE="$$-$(date +%s)"
RECEIPT=$(git rev-parse --git-path "harness-gate-receipt-$NONCE")
STAMP_TEMP=".harness/.integration-verified.$NONCE.tmp"
OWNER_LINE="pid=$$ nonce=$NONCE"

# --- уборка -------------------------------------------------------------------
# Перечень ограничен путями, созданными этим прогоном. Обходить дерево по шаблону
# нельзя: это медленно, а чужой файл со схожим именем не принадлежит verifier'у.
safe_remove() {
    remove_path=$1
    remove_oid=$2
    staged_oid=$(git rev-parse --verify ":$remove_path" 2>/dev/null || true)
    if [ -n "$staged_oid" ] && [ "$staged_oid" != "$remove_oid" ]; then
        PRESERVE_STATE=1
        echo "СТОП: staged blob verifier-owned path был заменён: $remove_path" >&2
        echo "  → стоп: index и transaction state сохранены для разбора" >&2
        echo "  ✓ index OID отсутствует либо совпадает с OID, созданным verifier" >&2
        return 1
    fi
    if [ -e "$remove_path" ] || [ -L "$remove_path" ]; then
        if [ -z "$remove_oid" ] ||
           [ "$(git hash-object "$remove_path" 2>/dev/null || true)" != "$remove_oid" ]; then
            PRESERVE_STATE=1
            echo "СТОП: verifier-owned path был заменён конкурентно: $remove_path" >&2
            echo "  → стоп: файл и transaction state сохранены для разбора" >&2
            echo "  ✓ blob OID пути совпадает с OID, созданным verifier" >&2
            return 1
        fi
    elif [ -z "$staged_oid" ]; then
        return 0
    fi
    git rm -q --cached --ignore-unmatch -- "$remove_path" >/dev/null 2>&1 || true
    rm -f -- "$remove_path"
}
safe_remove_receipt() {
    if [ ! -e "$RECEIPT" ]; then return 0; fi
    if [ -z "$RECEIPT_OID" ] ||
       [ "$(git hash-object "$RECEIPT" 2>/dev/null || true)" != "$RECEIPT_OID" ]; then
        PRESERVE_STATE=1
        echo "СТОП: receipt verifier был заменён; чужой файл не удаляется" >&2
        echo "  → стоп: сохрани receipt и установи владельца подмены" >&2
        echo "  ✓ receipt OID совпадает с OID, записанным текущим verifier" >&2
        return 1
    fi
    rm -f -- "$RECEIPT"
    RECEIPT_OWNED=0
}
cleanup() {
    if [ "$RULE_OWNED" -eq 1 ]; then
        safe_remove "$RULE" "$RULE_OID" || return 1
    fi
    if [ "$OKFILE_OWNED" -eq 1 ]; then
        safe_remove "$OKFILE" "$OKFILE_OID" || return 1
    fi
    if [ "$BAD_OWNED" -eq 1 ]; then
        safe_remove "$BAD" "$BAD_OID" || return 1
    fi
    if [ "$STAMP_TEMP_OWNED" -eq 1 ]; then
        safe_remove "$STAMP_TEMP" "$STAMP_TEMP_OID" || return 1
    fi
    if [ "$RECEIPT_OWNED" -eq 1 ]; then safe_remove_receipt || return 1; fi
}
release_lock() {
    if [ "$LOCK_OWNED" -eq 1 ]; then
        if [ "$(cat "$LOCK_DIR/owner" 2>/dev/null || true)" != "$OWNER_LINE" ]; then
            echo "СТОП: owner verifier lock изменился; чужой lock не удаляется" >&2
            echo "  → стоп: сохрани lock и установи владельца конкурентного запуска" >&2
            echo "  ✓ on-disk owner token совпадает с nonce текущего verifier" >&2
            LOCK_OWNED=0
            return 1
        fi
        rm -f -- "$LOCK_DIR/owner"
        rmdir "$LOCK_DIR" 2>/dev/null || true
        LOCK_OWNED=0
    fi
}
rollback() {
    if [ "$PRESERVE_STATE" -eq 1 ]; then
        echo "СТОП: verifier оставил состояние без автоотката для ручного разбора" >&2
        echo "  → стоп: не удаляй $STATE; сравни HEAD, index и worktree с baseline" >&2
        echo "  ✓ посторонние изменения сохранены и владелец каждого пути установлен" >&2
        release_lock
        return 1
    fi
    # Пока verifier владеет эксклюзивной рабочей копией, любой новый HEAD создан
    # только его пробой. Перед reset это доказывается parent и областью diff.
    if [ -n "$BEFORE" ] && [ "$(git rev-parse HEAD 2>/dev/null || true)" != "$BEFORE" ]; then
        current_head=$(git rev-parse HEAD 2>/dev/null || true)
        [ -n "$OWNED_HEAD" ] && [ "$current_head" = "$OWNED_HEAD" ] || {
            PRESERVE_STATE=1; rollback; return 1
        }
        current_parent=$(git rev-parse HEAD^ 2>/dev/null || true)
        case "$(git log -1 --format=%s 2>/dev/null || true)" in
            "проба интеграции: "*) ;;
            *) PRESERVE_STATE=1; rollback; return 1 ;;
        esac
        [ "$current_parent" = "$BEFORE" ] || {
            PRESERVE_STATE=1; rollback; return 1
        }
        unexpected_commit=$(git diff-tree --no-commit-id --name-only -r "$current_head" |
            grep -vxF "$RULE" | grep -vxF "$OKFILE" |
            { [ -z "$BAD" ] && cat || grep -vxF "$BAD"; } || true)
        [ -z "$unexpected_commit" ] || {
            PRESERVE_STATE=1; rollback; return 1
        }
        git reset -q --soft "$BEFORE"
    fi
    cleanup
    [ "$PRESERVE_STATE" -eq 0 ] || { release_lock; return 1; }
    rm -f -- "$STATE"
    release_lock
}
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    echo "СТОП: verifier уже запущен либо оставил stale lock: $LOCK_DIR" >&2
    echo "  → стоп: не удаляй lock вслепую; проверь owner, процессы и recovery marker" >&2
    echo "  ✓ живой verifier завершён либо stale state восстановлен вручную" >&2
    exit 1
fi
LOCK_OWNED=1
printf '%s\n' "$OWNER_LINE" > "$LOCK_DIR/owner"
trap 'rollback; exit 130' HUP INT TERM
trap rollback EXIT
if [ -e "$STATE" ]; then
    BEFORE=$(sed -n '1p' "$STATE")
    BAD=$(sed -n '2p' "$STATE")
    git cat-file -e "$BEFORE^{commit}" 2>/dev/null || {
        echo "СТОП: повреждён recovery-маркер verifier: $STATE" >&2
        echo "  → стоп: не удаляй его; выясни исходный baseline по reflog" >&2
        echo "  ✓ SHA baseline найден, существует как commit и записан в marker" >&2
        exit 1
    }
    PRESERVE_STATE=1
    echo "СТОП: найден marker прерванного verifier: $STATE" >&2
    echo "  → стоп: не запускай автоочистку; сравни HEAD/index/worktree с $BEFORE" >&2
    echo "    и удали только доказанно пробные пути, затем сам marker" >&2
    echo "  ✓ HEAD возвращён к baseline, status чист и marker удалён вручную" >&2
    exit 1
fi

# --- гвард --------------------------------------------------------------------
git rev-parse HEAD >/dev/null 2>&1 || {
    echo "в репозитории нет ни одного коммита" >&2
    echo "  → сделай: закоммить что-нибудь, потом повтори. Пробы сравнивают HEAD" >&2
    echo "    до и после, а сравнивать пока не с чем" >&2
    echo "  ✓ git rev-parse HEAD отвечает хешем" >&2
    exit 1
}

for op in MERGE_HEAD REBASE_HEAD CHERRY_PICK_HEAD BISECT_LOG; do
    if [ -e "$(git rev-parse --git-dir)/$op" ]; then
        echo "репозиторий в середине операции ($op) — пробные коммиты запускать нельзя" >&2
        echo "  → сделай: доведи операцию до конца или отмени её, потом повтори" >&2
        echo "  ✓ git status не сообщает о незавершённой операции" >&2
        exit 1
    fi
done

# --untracked-files=all: без него каталог с новым файлом виден одной строкой,
# а настройка status.showUntrackedFiles=no прячет его целиком.
STATUS_FIRST=$(git status --porcelain --untracked-files=all | sed -n '1p')
if [ -n "$STATUS_FIRST" ]; then
    echo "дерево не чисто — пробные коммиты запускать нельзя" >&2
    echo "  → сделай: закоммить или отложи текущую работу, потом повтори. Проба" >&2
    echo "    поверх грязного дерева была бы отклонена по не той причине и" >&2
    echo "    показала бы ложный успех" >&2
    echo "  ✓ git status --porcelain --untracked-files=all пуст" >&2
    exit 1
fi

BEFORE=$(git rev-parse HEAD)
OWNED_HEAD=$BEFORE
[ ! -e "$RULE" ] && [ ! -L "$RULE" ] || {
    echo "СТОП: зарезервированный путь verifier занят: $RULE" >&2
    echo "  → стоп: не удаляй файл; измени MARK либо освободи путь осознанно" >&2
    echo "  ✓ зарезервированный rule path отсутствует" >&2
    exit 1
}
[ ! -e "$OKFILE" ] && [ ! -L "$OKFILE" ] || {
    echo "СТОП: зарезервированный путь verifier занят: $OKFILE" >&2
    echo "  → стоп: не удаляй файл; измени MARK либо освободи путь осознанно" >&2
    echo "  ✓ зарезервированный control path отсутствует" >&2
    exit 1
}
[ ! -e "$RECEIPT" ] && [ ! -L "$RECEIPT" ] || {
    echo "СТОП: служебный receipt verifier уже существует: $RECEIPT" >&2
    echo "  → стоп: проверь, не идёт ли другой verifier и кому принадлежит receipt" >&2
    echo "  ✓ receipt path отсутствует перед новым прогоном" >&2
    exit 1
}
[ ! -e "$STAMP" ] && [ ! -L "$STAMP" ] || {
    echo "СТОП: output path verifier уже существует: $STAMP" >&2
    echo "  → стоп: не перезаписывай stamp; выясни, к какой интеграции он относится" >&2
    echo "  ✓ integration-verified path отсутствует перед новым прогоном" >&2
    exit 1
}
[ ! -e "$STAMP_TEMP" ] && [ ! -L "$STAMP_TEMP" ] || {
    echo "СТОП: временный output path verifier занят: $STAMP_TEMP" >&2
    echo "  → стоп: не удаляй файл; выясни, какой запуск его создал" >&2
    echo "  ✓ временный stamp path отсутствует перед новым прогоном" >&2
    exit 1
}
printf '%s\n\n' "$BEFORE" > "$STATE"
HARNESS_FAILED=0
PROBE_BUILD_FAILED=0
# Умолчание — «не подтверждено», а не «жив»: любая ветка, не дошедшая до отказа
# при выключенном гейте, вывода о соседе не даёт.
NEIGHBOUR=unknown

# --- механика пробы -----------------------------------------------------------
# Отменяем только своё и только мягко: --soft оставляет рабочее дерево в покое,
# а из индекса уходит ровно наш путь.
undo() {
    expected_head=$2
    expected_oid=$3
    if [ "$(git rev-parse HEAD)" != "$BEFORE" ]; then
        [ "$(git rev-parse HEAD)" = "$expected_head" ] || {
            PRESERVE_STATE=1
            echo "СТОП: HEAD сменился после commit verifier; undo запрещён" >&2
            echo "  → стоп: сохрани state и установи владельца нового HEAD" >&2
            echo "  ✓ HEAD равен exact commit OID, созданному verifier" >&2
            exit 1
        }
        undo_parent=$(git rev-parse HEAD^ 2>/dev/null || true)
        undo_subject=$(git log -1 --format=%s 2>/dev/null || true)
        undo_unexpected=$(git diff-tree --no-commit-id --name-only -r HEAD |
            grep -vxF "$1" || true)
        case "$undo_subject" in "проба интеграции: "*) ;; *) PRESERVE_STATE=1; exit 1 ;; esac
        if [ "$undo_parent" != "$BEFORE" ] || [ -n "$undo_unexpected" ]; then
            PRESERVE_STATE=1
            echo "СТОП: HEAD перед undo не принадлежит verifier" >&2
            echo "  → стоп: состояние сохранено; проверь конкурентный Git commit" >&2
            echo "  ✓ parent, subject и changed path соответствуют текущей пробе" >&2
            exit 1
        fi
        git reset -q --soft "$BEFORE"
    fi
    safe_remove "$1" "$expected_oid" || exit 1
    OWNED_HEAD=$BEFORE
}

# Пробу считаем состоявшейся, только если её файл действительно попал в индекс.
# Иначе «коммит не прошёл» означает «нечего коммитить», и это не доказательство.
stage() {
    git add -- "$1" 2>/dev/null || true
    git diff --cached --name-only | grep -qxF "$1"
}

# В эксклюзивной cooperative working copy hook не вправе менять ничего кроме текущей пробы.
# Verifier не является sandbox против процесса с теми же правами, который игнорирует lock.
# Любая наблюдаемая посторонняя мутация
# замораживает состояние: автоматический rollback уничтожил бы доказательство.
assert_scope() {
    allowed=$1
    unexpected=$(
        {
            git diff --name-only
            git diff --cached --name-only
            git ls-files --others --exclude-standard
            if [ "$(git rev-parse HEAD)" != "$BEFORE" ]; then
                git diff-tree --no-commit-id --name-only -r HEAD
            fi
        } | sort -u | grep -vxF "$allowed" || true
    )
    if [ -n "$unexpected" ]; then
        PRESERVE_STATE=1
        echo "СТОП: hook изменил пути вне verifier probe:" >&2
        echo "  → стоп: состояние сохранено; установи владельца каждой мутации" >&2
        echo "  ✓ после ручного восстановления status совпадает с baseline" >&2
        printf '  %s\n' "$unexpected" >&2
        exit 1
    fi
}

echo "проверка интеграции: позитивный контроль и две пробы"
echo

# --- 0. позитивный контроль ---------------------------------------------------
printf 'Файл-проба. Создан verify-integration.sh и удалён им же.\n' > "$OKFILE"
OKFILE_OID=$(git hash-object "$OKFILE")
OKFILE_OWNED=1
if ! stage "$OKFILE"; then
    echo "  СТОП  позитивный контроль — пробный файл не встаёт в индекс"
    echo "        Вероятно, он попадает под .gitignore этого проекта."
    echo "        → сделай: убери маску, скрывающую $OKFILE, либо переименуй MARK"
    echo "          в этом скрипте. Пока файл невидим для git, ни одна проба ниже"
    echo "          ничего не доказывает: «коммит не прошёл» будет означать"
    echo "          «нечего коммитить»"
    echo "        ✓ git add -- $OKFILE ставит файл в индекс"
    exit 1
fi
git commit -qm "проба интеграции: позитивный контроль" >/dev/null 2>&1 || true
CONTROL_HEAD=$(git rev-parse HEAD)
OWNED_HEAD=$CONTROL_HEAD
assert_scope "$OKFILE"
if [ "$(git rev-parse HEAD)" = "$BEFORE" ]; then
    echo "  СТОП  позитивный контроль — безобидный коммит НЕ прошёл"
    echo "        Значит коммит в этом репозитории не проходит вообще, и отказ"
    echo "        любой пробы ниже ничего не доказал бы: он объяснялся бы средой."
    echo "        Частые причины: не задан user.email, включён commit.gpgsign без"
    echo "        рабочего ключа, посторонний хук отвергает всё подряд."
    echo "        → команда: git commit --allow-empty -m проба"
    echo "        ✓ эта команда создаёт коммит; после починки повтори проверку"
    undo "$OKFILE" "$CONTROL_HEAD" "$OKFILE_OID"
    exit 1
fi
undo "$OKFILE" "$CONTROL_HEAD" "$OKFILE_OID"
echo "  ok    позитивный контроль — коммит проходит, среда исправна"

# --- механика двух отрицательных проб -----------------------------------------
probe() {
    _path=$1; _name=$2; _why=$3
    if ! stage "$_path"; then
        echo "  ??    $_name — проба не построена: файл не встаёт в индекс"
        echo "        → сделай: проверь, не скрыт ли $_path масками .gitignore" >&2
        echo "        ✓ git diff --cached --name-only содержит $_path" >&2
        PROBE_BUILD_FAILED=1; rm -f "$_path"; return 0
    fi
    _probe_oid=$(git hash-object "$_path")
    HARNESS_VERIFY_RECEIPT=1 \
    HARNESS_VERIFY_NONCE=$NONCE \
    HARNESS_VERIFY_BASELINE=$BEFORE \
    HARNESS_VERIFY_PROBE=$_path \
    HARNESS_VERIFY_PROBE_OID=$_probe_oid \
        git commit -qm "проба интеграции: $_name" \
        >/dev/null 2>&1 || true
    _attempt_head=$(git rev-parse HEAD)
    OWNED_HEAD=$_attempt_head
    assert_scope "$_path"
    if [ "$(git rev-parse HEAD)" = "$BEFORE" ]; then
        expected_receipt=$(printf 'checker=%s\nnonce=%s\nbaseline=%s\nprobe=%s\noid=%s' \
            '.harness/scripts/checks/rules_have_reproducers.py' \
            "$NONCE" "$BEFORE" "$_path" "$_probe_oid")
        actual_receipt=$(cat "$RECEIPT" 2>/dev/null || true)
        if [ "$actual_receipt" != "$expected_receipt" ]; then
            echo "  СТОП  $_name — коммит отклонён, но гейт не оставил свидетельства"
            echo "        → сделай: исправь общий отказ commit и повтори ту же пробу"
            echo "        ✓ receipt совпадает по checker, nonce, baseline, probe и oid"
            HARNESS_FAILED=1
        else
            RECEIPT_OID=$(git hash-object "$RECEIPT")
            RECEIPT_OWNED=1
            echo "  ok    $_name — коммит отклонён проверкой rules_have_reproducers"
        fi
    else
        echo "  FAIL  $_name — коммит ПРОШЁЛ, а не должен был"
        echo "        $_why"
        HARNESS_FAILED=1
    fi
    undo "$_path" "$_attempt_head" "$_probe_oid"
    if [ "$RECEIPT_OWNED" -eq 1 ]; then safe_remove_receipt || exit 1; fi
}

# --- 1. проба харнесса --------------------------------------------------------
# Правило без каталога входа — провал, который ловит gate.sh и только он.
mkdir -p .harness/rules
printf -- '---\nid: %s\nrepro: 0/1\n---\nпроба верификатора: правило без входа\n' "$MARK" > "$RULE"
RULE_OID=$(git hash-object "$RULE")
RULE_OWNED=1
probe "$RULE" "гейт харнесса" \
    "гейт не бежит на коммите — вернись к развилке А скилла integrate-harness"

# --- 2. проба прежнего процесса -----------------------------------------------
# Путь выбирается на шаге 0 по конфигурации живого процесса и повторяется дословно.
# Автопоиск здесь запрещён: другой тип или каталог дали бы несравнимое «до/после».
if [ -n "${HARNESS_PROJECT_PROBE:-}" ]; then
    PROPOSED_BAD=$HARNESS_PROJECT_PROBE
    case "$PROPOSED_BAD" in
        ''|/*|../*|*/../*|./*|*/./*|*//*|.harness/*|*[!A-Za-z0-9._/-]*)
            echo "  СТОП  небезопасный HARNESS_PROJECT_PROBE: $PROPOSED_BAD" >&2
            echo "        → сделай: передай относительный путь внутри исходников проекта" >&2
            echo "        ✓ путь состоит из portable-компонентов и не выходит из репозитория" >&2
            exit 1
            ;;
    esac
    SRC=${PROPOSED_BAD##*.}
    DIR=${PROPOSED_BAD%/*}
    [ "$DIR" = "$PROPOSED_BAD" ] && DIR=.
    [ -d "$DIR" ] || {
        echo "  СТОП  каталога контрольной пробы нет: $DIR" >&2
        echo "        → сделай: возьми существующий каталог из снимка шага 0" >&2
        echo "        ✓ test -d возвращает ноль для каталога пробы" >&2
        exit 1
    }
    python3 -c 'import os,sys
root=os.path.realpath(sys.argv[1]); probe=sys.argv[2]
resolved=os.path.realpath(os.path.join(root, probe))
ok=(os.path.commonpath([root, resolved]) == root and
    not any(os.path.islink(os.path.join(root, *probe.split("/")[:i]))
            for i in range(1, len(probe.split("/")))))
raise SystemExit(0 if ok else 1)' "$ROOT" "$PROPOSED_BAD" || {
        echo "  СТОП  probe выходит из репозитория через symlink: $PROPOSED_BAD" >&2
        echo "        → стоп: выбери реальный каталог исходников внутри repository root" >&2
        echo "        ✓ realpath probe остаётся внутри root и компоненты пути не symlink" >&2
        exit 1
    }
    [ ! -e "$PROPOSED_BAD" ] && [ ! -L "$PROPOSED_BAD" ] || {
        echo "  СТОП  путь контрольной пробы уже занят: $PROPOSED_BAD" >&2
        echo "        → стоп: не удаляй файл; выясни владельца и заново выбери baseline-путь" >&2
        echo "        ✓ путь свободен и одинаков в снимках до/после" >&2
        exit 1
    }
    BAD=$PROPOSED_BAD
    # Синтаксическая ошибка, а не стиль: стиль форматтер молча починит и пропустит.
    printf 'this is not valid %s ((( \n' "$SRC" > "$BAD"
    BAD_OID=$(git hash-object "$BAD")
    BAD_OWNED=1
    printf '%s\n%s\n' "$BEFORE" "$BAD" > "$STATE"

    # Гейт на время этой пробы выключен, и без этого она ничего не значила бы.
    # Шаг 3 скилла предписывает звать линтер проекта прямо из lint.sh — значит
    # при работающем гейте битый файл ловит ОН, независимо от того, жив сосед
    # или обесточен. Отказ был бы получен и в самом плохом сценарии.
    if ! stage "$BAD"; then
        echo "  ??    прежний процесс — файл не встаёт в индекс"
        echo "        → сделай: проверь маски .gitignore для $BAD" >&2
        echo "        ✓ git diff --cached --name-only содержит $BAD" >&2
        NEIGHBOUR=probe_unbuildable; rm -f "$BAD"
    else
        HARNESS_GATE_SKIP=1 git commit -qm "проба интеграции: прежний процесс" \
            >/dev/null 2>&1 || true
        BAD_HEAD=$(git rev-parse HEAD)
        OWNED_HEAD=$BAD_HEAD
        assert_scope "$BAD"
        if [ "$(git rev-parse HEAD)" != "$BEFORE" ]; then
            echo "  ??    прежний процесс (.$SRC в $DIR) — коммит ПРОШЁЛ"
            echo "        При выключенном гейте битый файл не остановил никто."
            echo "        → реши: сравни с реакцией ТОГО ЖЕ пути из шага 0."
            echo "        ✓ реакция совпала либо прежний владелец точки восстановлен"
            NEIGHBOUR=unknown
        else
            # Второй безобидный commit отделяет path/content-policy от сбоя среды,
            # возникшего уже после начального позитивного контроля.
            safe_remove "$BAD" "$BAD_OID" || exit 1
            printf 'benign post-probe control\n' > "$OKFILE"
            OKFILE_OID=$(git hash-object "$OKFILE")
            OKFILE_OWNED=1
            if stage "$OKFILE"; then
                HARNESS_GATE_SKIP=1 git commit -qm \
                    "проба интеграции: контроль после прежнего процесса" \
                    >/dev/null 2>&1 || true
                POST_HEAD=$(git rev-parse HEAD)
                OWNED_HEAD=$POST_HEAD
                assert_scope "$OKFILE"
            fi
            if [ "$(git rev-parse HEAD)" = "$BEFORE" ]; then
                echo "  СТОП  прежний процесс — probe отклонён, но повторный контроль тоже упал"
                echo "        → сделай: исправь инфраструктуру commit и повтори verifier"
                echo "        ✓ схема control pass → probe reject → control pass завершена"
                NEIGHBOUR=infrastructure_failed
            else
                undo "$OKFILE" "$POST_HEAD" "$OKFILE_OID"
                echo "  ok    наблюдаемая реакция (.$SRC в $DIR) — коммит отклонён"
                echo "        Контроль после него прошёл; сравнение с шагом 0 делает процедура"
                NEIGHBOUR=alive
            fi
        fi
        undo "$BAD" "$BAD_HEAD" "$BAD_OID"
    fi
else
    echo "  ??    прежний процесс — не передан HARNESS_PROJECT_PROBE"
    echo "        → сделай: повтори с путём контрольной пробы из отчёта шага 0"
    echo "        ✓ HARNESS_PROJECT_PROBE совпадает с записанным путём"
    NEIGHBOUR=probe_unbuildable
fi

# --- итог ---------------------------------------------------------------------
# Формулировка держится ровно на том, что установлено. «Интеграция доказана» было
# шире установленного примерно на порядок: пробы видят одну развилку скилла из
# шести, а остальные держатся на послушании агента, и это надо называть.
echo
if [ "$PROBE_BUILD_FAILED" -eq 1 ]; then
    echo "проба харнесса не была построена; вывод о подключении гейта невозможен." >&2
    echo "  → сделай: исправь staging/index/.gitignore и повтори ту же пробу" >&2
    echo "  ✓ пробный путь появляется в git diff --cached --name-only" >&2
    exit 4
fi
if [ "$HARNESS_FAILED" -eq 1 ]; then
    echo "гейт харнесса на коммите НЕ бежит." >&2
    echo "  → сделай: вернись к развилке А скилла integrate-harness и повтори эту" >&2
    echo "    команду. Вывод самого gate.sh тут ничего не значит: он бывает зелёным," >&2
    echo "    не проверив в этом проекте ничего" >&2
    echo "  ✓ эта команда печатает «гейт харнесса бежит на коммите»" >&2
    exit 1
fi
if [ "$NEIGHBOUR" = probe_unbuildable ]; then
    echo "контрольная проба проекта не построена." >&2
    echo "  → сделай: исправь путь/index/.gitignore и повтори verifier" >&2
    echo "  ✓ probe появляется в git diff --cached --name-only" >&2
    exit 3
fi
if [ "$NEIGHBOUR" = infrastructure_failed ]; then
    echo "инфраструктура commit отказала после контрольной пробы." >&2
    echo "  → сделай: исправь общий отказ commit и повтори verifier" >&2
    echo "  ✓ control pass → probe → control pass завершается полностью" >&2
    exit 5
fi

# --- след прогона -------------------------------------------------------------
# Без него фраза «отчитаться словами нельзя» была бы ложной: скрипт возвращает
# репозиторий в исходное состояние, и его вывод пересказывается по памяти дословно.
{
    printf '# Прогон verify-integration.sh\n\n'
    printf -- '- дата: %s\n' "$(date +%Y-%m-%d)"
    printf -- '- коммит: %s\n' "$(git rev-parse --short HEAD)"
    printf -- '- гейт харнесса на коммите: бежит (receipt проверки получен)\n'
    case "$NEIGHBOUR" in
        alive)   printf -- '- наблюдаемая реакция контрольной пробы: отклонена при выключенном гейте\n' ;;
        unknown) printf -- '- проверки проекта на коммите: НЕ ПОДТВЕРЖДЕНЫ — сверь со снимком шага 0\n' ;;
    esac
    printf '\nЧего этот прогон НЕ проверял: заполнены ли адаптеры под стек проекта,\n'
    printf 'исключён ли `.harness/` из чужого линтера, полностью ли сняты проверки,\n'
    printf 'не уехало ли сюда чужое состояние боилерплейта, откалиброваны ли пороги.\n'
    printf 'Всё это держится на шагах 3–6 скилла, и следа в диффе у них нет.\n'
} > "$STAMP_TEMP"
STAMP_TEMP_OID=$(git hash-object "$STAMP_TEMP")
STAMP_TEMP_OWNED=1
if ! ln "$STAMP_TEMP" "$STAMP" 2>/dev/null; then
    echo "СТОП: output stamp появился конкурентно; существующий файл не перезаписан" >&2
    echo "  → стоп: выясни владельца $STAMP и повтори на эксклюзивной working copy" >&2
    echo "  ✓ target stamp отсутствует до атомарной публикации" >&2
    exit 1
fi
safe_remove "$STAMP_TEMP" "$STAMP_TEMP_OID" || exit 1
STAMP_TEMP_OWNED=0

echo "гейт харнесса бежит на коммите — проба отклонена."
case "$NEIGHBOUR" in
    alive)   echo "Наблюдаемая реакция контрольной пробы: отклонена при выключенном гейте." ;;
    unknown) echo "Про проверки проекта вывод НЕ сделан — сверь со снимком шага 0." ;;
esac
echo
echo "Это одна развилка скилла из шести. Не проверено ничем: адаптеры под стек,"
echo "исключение .harness из чужого линтера, полнота снятия проверок, чужое"
echo "состояние боилерплейта, пороги. След прогона записан в $STAMP —"
echo "включи его в коммит и в отчёт человеку (шаг 7)."
[ "$NEIGHBOUR" = alive ] && exit 0
[ "$NEIGHBOUR" = unknown ] && exit 2
exit 5
