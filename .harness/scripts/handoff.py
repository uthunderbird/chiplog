#!/usr/bin/env python3
"""Хвосты сессии — то, что человек обязан узнать до разрешения на коммит.

Две вещи с одним жизненным циклом и разным исходом:

  решение — выбор, сделанный за человека. Пересказан — закрыт.
  хвост   — работа, замеченная и сознательно отложенная. У него есть «когда»:
            сейчас     — закрыть в этом коммите, даже если гейт не требует;
                         это и есть чистота дерева;
            следующим  — переживёт коммит, всплывёт снова.

Формат хранит одно место, поэтому вся правка файла живёт здесь, а decide.sh,
defer.sh и handoff.sh — тонкие обёртки. Иначе формат разъедется по трём скриптам.

Файл живёт до разрешения на коммит и в репозиторий не едет.
"""

import datetime as dt
import pathlib
import re
import sys

HEAD = re.compile(r"^## (\d+) · (.+)$", re.M)
KINDS = ("решение", "хвост")
WHENS = ("сейчас", "следующим")
HEADER = """# Хвосты сессии

То, что человек обязан узнать до того, как разрешит коммит: что агент решил
за него и что сознательно отложил.

Пока здесь есть непересказанные записи, гейт закрыт. Пересказал, получил
разрешение — `./.harness/scripts/handoff.sh --ack`.

Хвост со сроком `сейчас` закрывает коммит до тех пор, пока не будет закрыт
работой: `./.harness/scripts/handoff.sh --close <N>`. В этом и смысл срока.

Запись, попавшая сюда второй раз с той же формулировкой, — кандидат
в `.harness/observations.md`.

"""


def path(root):
    return pathlib.Path(root) / ".harness" / "handoff.md"


def parse(root):
    """[{id, kind, when, status, fields{}}] — порядок как в файле."""
    p = path(root)
    if not p.is_file():
        return []
    text = p.read_text(encoding="utf-8")
    out = []
    marks = list(HEAD.finditer(text))
    for i, m in enumerate(marks):
        parts = [s.strip() for s in m.group(2).split("·")]
        kind = parts[0] if parts else ""
        when = next((s for s in parts if s in WHENS), "")
        status = parts[-1] if parts else "new"
        body = text[m.end(): marks[i + 1].start() if i + 1 < len(marks) else len(text)]
        fields = {}
        for line in body.splitlines():
            if line.startswith("- ") and ":" in line:
                k, _, v = line[2:].partition(":")
                fields[k.strip()] = v.strip()
        out.append({
            "id": int(m.group(1)), "kind": kind, "when": when,
            "status": status, "fields": fields, "raw": m.group(0) + body,
        })
    return out


def write(root, entries):
    p = path(root)
    if not entries:
        p.unlink(missing_ok=True)
        return
    chunks = [HEADER]
    for e in entries:
        head = " · ".join(x for x in (e["kind"], e["when"], e["status"]) if x)
        chunks.append(f"## {e['id']} · {head}\n")
        for k, v in e["fields"].items():
            chunks.append(f"- {k}: {v}\n")
        chunks.append("\n")
    p.write_text("".join(chunks), encoding="utf-8")


def add(root, kind, when, fields):
    entries = parse(root)
    nid = max((e["id"] for e in entries), default=0) + 1
    fields = dict(fields)
    fields["когда записано"] = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    entries.append({"id": nid, "kind": kind, "when": when,
                    "status": "new", "fields": fields})
    write(root, entries)
    return nid


def close(root, target):
    entries = parse(root)
    kept = [e for e in entries if e["id"] != target]
    if len(kept) == len(entries):
        return False
    write(root, kept)
    return True


def ack(root):
    """Пересказано и разрешено: решения закрыты, хвосты переходят в carried.

    Хвосты со сроком «сейчас» переводом не закрываются — их закрывает работа.
    Гейт продолжит их держать, и это намеренно."""
    entries = parse(root)
    kept = []
    for e in entries:
        if e["kind"] == "решение":
            continue
        e["status"] = "carried"
        kept.append(e)
    write(root, kept)
    return len(entries) - len(kept), len(kept)


def counts(root):
    entries = parse(root)
    return {
        "total": len(entries),
        "new": sum(1 for e in entries if e["status"] == "new"),
        "decisions": sum(1 for e in entries if e["kind"] == "решение"),
        "now": sum(1 for e in entries if e["kind"] == "хвост" and e["when"] == "сейчас"),
        "next": sum(1 for e in entries if e["kind"] == "хвост" and e["when"] == "следующим"),
    }


def main(argv):
    if len(argv) < 3:
        return 2
    root, cmd = argv[1], argv[2]
    a = argv[3:]
    if cmd == "add-decision":
        n = add(root, "решение", "", {
            "что": a[0], "на чём основано": a[1],
            **({"отклонено": a[2]} if len(a) > 2 and a[2] else {}),
        })
    elif cmd == "add-defer":
        when = a[0]
        if when not in WHENS:
            return 2
        n = add(root, "хвост", when, {"что": a[1], "почему не сейчас": a[2]})
    elif cmd == "close":
        return 0 if close(root, int(a[0])) else 1
    elif cmd == "ack":
        dropped, kept = ack(root)
        print(f"{dropped}|{kept}")
        return 0
    elif cmd == "status":
        for k, v in counts(root).items():
            print(f"handoff_{k}={v}")
        return 0
    else:
        return 2
    print(n)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
