#!/usr/bin/env python3

import sys
import time
import argparse

import filament_detect as fd


RFID_VENDOR    = "Generic"
RFID_TYPE      = "PLA"
RFID_SUBTYPE   = "Basic"
RFID_COLOR     = "FF0000"
RFID_CARD_UID  = [0xA1, 0xB2, 0xC3, 0xD4]

USER_VENDOR  = "Overture"
USER_TYPE    = "PETG"
USER_SUBTYPE = "Rapid"
USER_COLOR   = "00FF00"

RFID2_VENDOR  = "Snapmaker"
RFID2_TYPE    = "ABS"
RFID2_SUBTYPE = "Basic"
RFID2_COLOR   = "0000FF"

_passes   = 0
_failures = 0


def check(desc, actual, expected):
    global _passes, _failures
    if actual == expected:
        print(f"  PASS  {desc}")
        _passes += 1
    else:
        print(f"  FAIL  {desc}  (expected {expected!r}, got {actual!r})")
        _failures += 1


def reset(host):
    fd.post(host, "gcode/script", {"script": "FILAMENT_DT_CLEAR CHANNEL=0"})
    time.sleep(1)


def section(title):
    print(f"\n=== {title} ===")


def op(desc):
    print(f"  >> {desc}")


def expect(desc):
    print(f"  .. expect: {desc}")


def get_state(host, ch=0):
    data = fd.get_objects(host, "filament_detect", "print_task_config")
    s    = data["result"]["status"]
    info = s.get("filament_detect", {}).get("info", [{}])
    ptc  = s.get("print_task_config", {})
    return info[ch], {
        "vendor":   ptc.get("filament_vendor",   [])[ch],
        "type":     ptc.get("filament_type",     [])[ch],
        "subtype":  ptc.get("filament_sub_type", [])[ch],
        "official": ptc.get("filament_official", [])[ch],
        "color":    ptc.get("filament_color_rgba", [])[ch],
    }


def check_rfid(rfid, vendor, ftype, subtype, official, color=None, card_uid=None):
    check("rfid.VENDOR",    rfid.get("VENDOR"),    vendor)
    check("rfid.MAIN_TYPE", rfid.get("MAIN_TYPE"), ftype)
    check("rfid.SUB_TYPE",  rfid.get("SUB_TYPE"),  subtype)
    check("rfid.OFFICIAL",  rfid.get("OFFICIAL"),  official)
    if color is not None:
        check("rfid.RGB_1", rfid.get("RGB_1"), int(color, 16))
    if card_uid is not None:
        check("rfid.CARD_UID", rfid.get("CARD_UID"), card_uid)


def check_ptc(ptc, vendor, ftype, subtype, official, color=None):
    check("ptc.vendor",   ptc["vendor"],   vendor)
    check("ptc.type",     ptc["type"],     ftype)
    check("ptc.subtype",  ptc["subtype"],  subtype)
    check("ptc.official", ptc["official"], official)
    if color is not None:
        check("ptc.color", ptc["color"], color + "FF")


def main():
    parser = argparse.ArgumentParser(prog="filament_detect_test.py")
    parser.add_argument("host")
    args = parser.parse_args()
    host = args.host

    section("Setup: pristine state")
    op("FILAMENT_DT_CLEAR channel=0 (wait 1s)")
    reset(host)
    rfid, ptc = get_state(host)
    expect("RFID and config both NONE, unofficial")
    check_rfid(rfid, "NONE", "NONE", "NONE", False)
    check_ptc(ptc,   "NONE", "NONE", "NONE", False)

    section("Step 1: set RFID — verify RFID and user state are set")
    reset(host)
    op(f"set-rfid channel=0  {RFID_VENDOR} {RFID_TYPE} {RFID_SUBTYPE}  #{RFID_COLOR}")
    fd.cmd_set_rfid(host, 0, RFID_VENDOR, RFID_TYPE, RFID_SUBTYPE, RFID_COLOR)
    rfid, ptc = get_state(host)
    expect("RFID state reflects new filament, official=True")
    check_rfid(rfid, RFID_VENDOR, RFID_TYPE, RFID_SUBTYPE, True, RFID_COLOR)
    expect("print_task_config updated from RFID callback, official=True")
    check_ptc(ptc,   RFID_VENDOR, RFID_TYPE, RFID_SUBTYPE, True, RFID_COLOR)

    section("Step 2: clear RFID — verify both RFID and user state are cleared")
    reset(host)
    op(f"set-rfid channel=0  {RFID_VENDOR} {RFID_TYPE} {RFID_SUBTYPE}")
    fd.cmd_set_rfid(host, 0, RFID_VENDOR, RFID_TYPE, RFID_SUBTYPE, RFID_COLOR)
    op("clear-rfid channel=0")
    fd.cmd_clear_rfid(host, 0)
    rfid, ptc = get_state(host)
    expect("RFID state reset to NONE, unofficial")
    check_rfid(rfid, "NONE", "NONE", "NONE", False)
    expect("print_task_config also cleared to NONE, unofficial")
    check_ptc(ptc,   "NONE", "NONE", "NONE", False)

    section("Step 3: set user state — verify RFID unchanged, user state set")
    reset(host)
    op(f"set-user channel=0  {USER_VENDOR} {USER_TYPE} {USER_SUBTYPE}  #{USER_COLOR}")
    fd.cmd_set_user(host, 0, USER_VENDOR, USER_TYPE, USER_SUBTYPE, USER_COLOR)
    rfid, ptc = get_state(host)
    expect("RFID state untouched (still NONE)")
    check_rfid(rfid, "NONE",      "NONE",    "NONE",       False)
    expect("print_task_config updated with user values, official=False")
    check_ptc(ptc,   USER_VENDOR, USER_TYPE, USER_SUBTYPE, False, USER_COLOR)

    section("Step 4: clear RFID — verify user state is preserved")
    reset(host)
    op(f"set-user channel=0  {USER_VENDOR} {USER_TYPE} {USER_SUBTYPE}")
    fd.cmd_set_user(host, 0, USER_VENDOR, USER_TYPE, USER_SUBTYPE, USER_COLOR)
    op("clear-rfid channel=0")
    fd.cmd_clear_rfid(host, 0)
    rfid, ptc = get_state(host)
    expect("RFID state reset to NONE")
    check_rfid(rfid, "NONE",      "NONE",    "NONE",       False)
    expect("print_task_config preserved — clear-rfid must not overwrite user-set filament")
    check_ptc(ptc,   USER_VENDOR, USER_TYPE, USER_SUBTYPE, False, USER_COLOR)

    section("Step 5: set RFID — verify user state is overwritten")
    reset(host)
    op(f"set-user channel=0  {USER_VENDOR} {USER_TYPE} {USER_SUBTYPE}")
    fd.cmd_set_user(host, 0, USER_VENDOR, USER_TYPE, USER_SUBTYPE, USER_COLOR)
    op(f"set-rfid channel=0  {RFID2_VENDOR} {RFID2_TYPE} {RFID2_SUBTYPE}  #{RFID2_COLOR}")
    fd.cmd_set_rfid(host, 0, RFID2_VENDOR, RFID2_TYPE, RFID2_SUBTYPE, RFID2_COLOR)
    rfid, ptc = get_state(host)
    expect("RFID state reflects new filament, official=True")
    check_rfid(rfid, RFID2_VENDOR, RFID2_TYPE, RFID2_SUBTYPE, True, RFID2_COLOR)
    expect("print_task_config overwritten by RFID even though user had set it")
    check_ptc(ptc,   RFID2_VENDOR, RFID2_TYPE, RFID2_SUBTYPE, True, RFID2_COLOR)

    section("Step 6: set RFID with CARD_UID — verify stored and cleared")
    reset(host)
    op(f"set-rfid channel=0  {RFID_VENDOR} {RFID_TYPE} {RFID_SUBTYPE}  CARD_UID={RFID_CARD_UID}")
    fd.cmd_set_rfid(host, 0, RFID_VENDOR, RFID_TYPE, RFID_SUBTYPE, RFID_COLOR, card_uid=RFID_CARD_UID)
    rfid, ptc = get_state(host)
    expect("RFID CARD_UID matches written value")
    check_rfid(rfid, RFID_VENDOR, RFID_TYPE, RFID_SUBTYPE, True, card_uid=RFID_CARD_UID)
    op("clear-rfid channel=0")
    fd.cmd_clear_rfid(host, 0)
    rfid, ptc = get_state(host)
    expect("RFID CARD_UID reset to struct default (0)")
    check("rfid.CARD_UID", rfid.get("CARD_UID"), 0)

    print(f"\n{'OK' if _failures == 0 else 'FAILED'}  {_passes} passed, {_failures} failed")
    sys.exit(0 if _failures == 0 else 1)


if __name__ == "__main__":
    main()
