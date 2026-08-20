#!/bin/sh
set -eu
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
cd "$ROOT"

chmod +x .harness/scripts/*.sh .harness/scripts/checks/*.py .githooks/* 2>/dev/null || true
git config core.hooksPath .githooks
echo "хуки включены: core.hooksPath=.githooks"

sh .harness/scripts/install-skills.sh

echo
echo "прогоняю гейт первый раз — он обязан упасть на неподключённых линтере и тестах:"
sh .harness/scripts/gate.sh || true
echo
echo "Это не поломка. Пока .harness/scripts/lint.sh и .harness/scripts/test.sh пустые, дыра открыта, и гейт про это честно говорит."
echo
echo "дашборд — так харнесс выглядит на входе в сессию:"
echo
sh .harness/scripts/dashboard.sh
