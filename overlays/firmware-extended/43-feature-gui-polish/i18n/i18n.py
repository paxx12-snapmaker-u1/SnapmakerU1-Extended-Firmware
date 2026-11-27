#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: Copyright (c) 2026 @xtronets
"""Keep the Polish translation of the stock U1 GUI in sync with new firmware.

Files (relative to this script):
  ../root/usr/local/share/gui-polish/pl.json            Polish UI texts (shown as tr-TR)
  ../root/usr/local/share/gui-polish/exception_pl.json  Polish error messages (shown as tr-TR)
  source/en-GB.json                English texts the translation was made from
  source/exception_en-GB.json      English error messages the translation was made from
  exception_templates.json         English error template -> Polish template
                                   ({N} = toolhead/feeder number, {A} = axis X/Y/Z)
  todo.json                        written by `update`: what still needs translating

Commands (see README.md for the full workflow):
  extract <upgrade.bin> <dir>      unpack firmware, copy the stock English files to <dir>
  status  <dir>                    show what changed between source/ and <dir>
  update  <dir>                    merge new firmware texts, write todo.json
  apply                            merge a translated todo.json, refresh source/
  check                            validate the Polish files
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
FILES = HERE.parent / "root/usr/local/share/gui-polish"
SRC = HERE / "source"
REPO = HERE.parents[3]

PL_UI = FILES / "pl.json"
PL_EXC = FILES / "exception_pl.json"
EN_UI = SRC / "en-GB.json"
EN_EXC = SRC / "exception_en-GB.json"
TEMPLATES = HERE / "exception_templates.json"
TODO = HERE / "todo.json"

UI_REL = "home/lava/resource/locales/en-GB.json"
EXC_REL = "home/lava/resource/text/exception_en-GB.json"
TR_UI_REL = "home/lava/resource/locales/tr-TR.json"
GUI_REL = "usr/bin/gui"

RN = re.compile(r"(?<![A-Za-z])(Toolhead|toolhead|Feeder|feeder|Extruder|extruder|Channel|channel) ([1-4])(?![0-9])")
RA = re.compile(r"(?<![A-Za-z])([XYZ])(?=(-axis| axis\b))")
PH = re.compile(r"%[-0-9.]*l*[sdfuxc]|\{[^}]*\}")


def load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def save(p, data):
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def template(s):
    """English error message -> (template, numbers, axes)."""
    nums = [m.group(2) for m in RN.finditer(s)]
    t = RN.sub(r"\1 {N}", s)
    axes = [m.group(1) for m in RA.finditer(t)]
    t = RA.sub("{A}", t)
    return t, nums, axes


def fill(tpl, nums, axes):
    for v in nums:
        tpl = tpl.replace("{N}", v, 1)
    for v in axes:
        tpl = tpl.replace("{A}", v, 1)
    return tpl


def stock_files(d):
    d = Path(d)
    ui = d / "en-GB.json"
    exc = d / "exception_en-GB.json"
    if not ui.exists() or not exc.exists():
        sys.exit(f"!! {d} must contain en-GB.json and exception_en-GB.json (run `extract` first)")
    return load(ui), load(exc)


# ---------------------------------------------------------------- extract
def cmd_extract(fw, out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="u1-i18n-")) / "x"
    try:
        subprocess.run([str(REPO / "scripts/extract_squashfs.sh"), str(Path(fw).resolve()), str(tmp)], check=True,
                       stdout=subprocess.DEVNULL)
        root = tmp / "rootfs"
        shutil.copy(root / UI_REL, out / "en-GB.json")
        shutil.copy(root / EXC_REL, out / "exception_en-GB.json")
        shutil.copy(root / TR_UI_REL, out / "tr-TR.json")
        gui = (root / GUI_REL).read_bytes()
        n = gui.count("Türkçe".encode() + b"\0")
        print(f">> Copied stock English texts to {out}")
        print(f">> 'Türkçe' label in /usr/bin/gui: found {n}x " + ("(OK)" if n == 1 else "(!! the menu label will not be renamed)"))
        if b"tr-TR" not in gui:
            print("!! 'tr-TR' locale code not found in /usr/bin/gui - the Turkish slot may be gone")
    finally:
        shutil.rmtree(tmp.parent, ignore_errors=True)


# ---------------------------------------------------------------- status / update
def diff(old, new):
    added = [k for k in new if k not in old]
    removed = [k for k in old if k not in new]
    changed = [k for k in new if k in old and old[k] != new[k]]
    return added, removed, changed


def cmd_status(d):
    new_ui, new_exc = stock_files(d)
    for name, old, new in (("UI", load(EN_UI), new_ui), ("Errors", load(EN_EXC), new_exc)):
        a, r, c = diff(old, new)
        print(f"== {name}: {len(new)} keys | new {len(a)} | removed {len(r)} | changed English {len(c)}")
        for k in a:
            print(f"   + {k}: {json.dumps(new[k], ensure_ascii=False)[:110]}")
        for k in r:
            print(f"   - {k}")
        for k in c:
            print(f"   ~ {k}: {json.dumps(new[k], ensure_ascii=False)[:110]}")
    tpl = load(TEMPLATES)
    missing = sorted({template(v)[0] for v in new_exc.values()} - set(tpl))
    print(f"== Error templates without translation: {len(missing)}")


def cmd_update(d):
    new_ui, new_exc = stock_files(d)
    old_ui, old_exc = load(EN_UI), load(EN_EXC)
    pl_ui, pl_exc = load(PL_UI), load(PL_EXC)
    tpl = load(TEMPLATES)

    # UI: keep translations whose English source did not change, in the new key order
    todo_ui = {}
    out_ui = {}
    for k, v in new_ui.items():
        if k == "_meta":
            out_ui[k] = v
        elif k in pl_ui and old_ui.get(k) == v:
            out_ui[k] = pl_ui[k]
        else:
            todo_ui[k] = {"en": v, "pl": "", "old_en": old_ui.get(k), "old_pl": pl_ui.get(k)}
            out_ui[k] = pl_ui.get(k, v)  # placeholder until translated (old Polish or English)
    out_ui["language_name"] = "Polski"
    todo_ui.pop("language_name", None)

    # Errors: translate through templates, the rest goes to the todo list
    todo_tpl = {}
    out_exc = {}
    for k, v in new_exc.items():
        t, nums, axes = template(v)
        if t in tpl:
            out_exc[k] = fill(tpl[t], nums, axes)
        else:
            todo_tpl.setdefault(t, {"en": t, "pl": "", "codes": []})["codes"].append(k)
            out_exc[k] = pl_exc.get(k, v)

    save(PL_UI, out_ui)
    save(PL_EXC, out_exc)
    save(TODO, {"_source_dir": str(Path(d).resolve()), "ui": todo_ui, "error_templates": todo_tpl})
    print(f">> Merged. To translate: {len(todo_ui)} UI texts, {len(todo_tpl)} error templates")
    print(f">> Fill in the \"pl\" fields in {TODO.relative_to(REPO)} and run: i18n.py apply")
    if not todo_ui and not todo_tpl:
        cmd_apply()


def cmd_apply():
    todo = load(TODO)
    d = todo["_source_dir"]
    new_ui, new_exc = stock_files(d)
    pl_ui, tpl = load(PL_UI), load(TEMPLATES)
    left = 0
    for k, e in todo["ui"].items():
        if e["pl"]:
            pl_ui[k] = e["pl"]
        else:
            left += 1
    for t, e in todo["error_templates"].items():
        if e["pl"]:
            tpl[t] = e["pl"]
        else:
            left += 1
    if left:
        sys.exit(f"!! {left} entries in todo.json still have an empty \"pl\" - nothing written")
    pl_exc = {}
    for k, v in new_exc.items():
        t, nums, axes = template(v)
        pl_exc[k] = fill(tpl[t], nums, axes)
    save(PL_UI, {k: pl_ui[k] for k in new_ui})
    save(PL_EXC, pl_exc)
    save(TEMPLATES, dict(sorted(tpl.items())))
    shutil.copy(Path(d) / "en-GB.json", EN_UI)
    shutil.copy(Path(d) / "exception_en-GB.json", EN_EXC)
    TODO.unlink()
    print(">> Applied translations and refreshed source/ snapshot")
    cmd_check()


# ---------------------------------------------------------------- check
def cmd_check():
    en_ui, en_exc = load(EN_UI), load(EN_EXC)
    pl_ui, pl_exc, tpl = load(PL_UI), load(PL_EXC), load(TEMPLATES)
    errors = []
    a, r, _ = diff(en_ui, pl_ui)
    errors += [f"UI key missing in pl.json: {k}" for k in a] + [f"UI key not in English: {k}" for k in r]
    a, r, _ = diff(en_exc, pl_exc)
    errors += [f"error code missing in exception_pl.json: {k}" for k in a] + [f"error code not in English: {k}" for k in r]
    for k, v in en_ui.items():
        if k == "_meta" or k not in pl_ui:
            continue
        if sorted(PH.findall(v)) != sorted(PH.findall(pl_ui[k])):
            errors.append(f"UI placeholders differ in {k}: {v!r} -> {pl_ui[k]!r}")
        if pl_ui[k] == v and re.search(r"[A-Za-z]{4}", v) and k not in ("language_name",):
            print(f"   note: {k} is identical to English: {v!r}")
    for t, p in tpl.items():
        for ph in ("{N}", "{A}"):
            if t.count(ph) != p.count(ph):
                errors.append(f"template {ph} count differs: {t!r}")
        if t.count("_") != p.count("_"):
            errors.append(f"template title separator '_' differs: {t!r}")
    if pl_ui.get("language_name") != "Polski":
        errors.append("language_name must be 'Polski'")
    for e in errors:
        print("!! " + e)
    print(f">> check: {'OK' if not errors else str(len(errors)) + ' problem(s)'}")
    return not errors


def main(argv):
    cmds = {"extract": (cmd_extract, 2), "status": (cmd_status, 1), "update": (cmd_update, 1),
            "apply": (cmd_apply, 0), "check": (cmd_check, 0)}
    if len(argv) < 1 or argv[0] not in cmds or len(argv) - 1 != cmds[argv[0]][1]:
        print(__doc__)
        return 2
    res = cmds[argv[0]][0](*argv[1:])
    return 1 if res is False else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
