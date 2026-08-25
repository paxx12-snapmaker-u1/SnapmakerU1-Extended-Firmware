#!/usr/bin/env python3

import sys
import json
import argparse
import urllib.request


def post(host, endpoint, payload):
    url = f"http://{host}/printer/{endpoint}"
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def get_objects(host, *objects):
    query = "&".join(objects)
    url = f"http://{host}/printer/objects/query?{query}"
    with urllib.request.urlopen(url) as resp:
        return json.loads(resp.read())


def cmd_get(host):
    data = get_objects(host, "filament_detect", "print_task_config")
    status = data["result"]["status"]

    fd = status.get("filament_detect", {})
    ptc = status.get("print_task_config", {})

    fd_info      = fd.get("info", [])
    ptc_vendor   = ptc.get("filament_vendor", [])
    ptc_type     = ptc.get("filament_type", [])
    ptc_subtype  = ptc.get("filament_sub_type", [])
    ptc_official = ptc.get("filament_official", [])
    ptc_edit     = ptc.get("filament_edit", [])
    ptc_color    = ptc.get("filament_color_rgba", [])

    channels = max(len(fd_info), len(ptc_vendor))
    for ch in range(channels):
        print(f"Channel {ch}:")

        if ch < len(fd_info):
            i = fd_info[ch]
            official = i.get("OFFICIAL", False)
            print(f"  RFID:   {i.get('VENDOR','')} {i.get('MAIN_TYPE','')} {i.get('SUB_TYPE','')}  [official: {'yes' if official else 'no'}]")
        else:
            print("  RFID:   (no data)")

        if ch < len(ptc_vendor):
            vendor   = ptc_vendor[ch]
            ftype    = ptc_type[ch]     if ch < len(ptc_type)     else ""
            subtype  = ptc_subtype[ch]  if ch < len(ptc_subtype)  else ""
            official = ptc_official[ch] if ch < len(ptc_official) else False
            edit     = ptc_edit[ch]     if ch < len(ptc_edit)     else False
            color    = ptc_color[ch]    if ch < len(ptc_color)    else ""
            print(f"  Config: {vendor} {ftype} {subtype}  [official: {'yes' if official else 'no'}, edit: {'yes' if edit else 'no'}, color: #{color}]")

            if not official and ch < len(fd_info):
                fd_v = fd_info[ch].get("VENDOR", "")
                fd_t = fd_info[ch].get("MAIN_TYPE", "")
                fd_s = fd_info[ch].get("SUB_TYPE", "")
                if (vendor, ftype, subtype) != (fd_v, fd_t, fd_s):
                    print("  ↳ WARN: config differs from RFID (user-edited)")
        else:
            print("  Config: (no data)")


def cmd_set_rfid(host, channel, vendor, ftype, subtype, color, card_uid=None):
    info = {"VENDOR": vendor, "MAIN_TYPE": ftype, "SUB_TYPE": subtype, "RGB_1": int(color, 16)}
    if card_uid is not None:
        info["CARD_UID"] = card_uid
    result = post(host, "filament_detect/set", {"channel": channel, "info": info})
    print(json.dumps(result, indent=2))


def cmd_clear_rfid(host, channel):
    result = post(host, "filament_detect/set", {"channel": channel})
    print(json.dumps(result, indent=2))


def cmd_set_user(host, channel, vendor, ftype, subtype, color):
    script = (
        f"SET_PRINT_FILAMENT_CONFIG"
        f" CONFIG_EXTRUDER={channel}"
        f" VENDOR={vendor}"
        f" FILAMENT_TYPE={ftype}"
        f" FILAMENT_SUBTYPE={subtype}"
        f" FILAMENT_COLOR_RGBA={color}FF"
    )
    result = post(host, "gcode/script", {"script": script})
    print(json.dumps(result, indent=2))


def add_set_args(p):
    p.add_argument("channel", type=int)
    p.add_argument("vendor")
    p.add_argument("type", nargs="?", default="PLA")
    p.add_argument("subtype", nargs="?", default="Basic")
    p.add_argument("--color", metavar="RRGGBB", default="FFFFFF")


def main():
    parser = argparse.ArgumentParser(prog="filament_detect.py")
    parser.add_argument("host")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("get")

    add_set_args(sub.add_parser("set-rfid"))
    add_set_args(sub.add_parser("set-user"))

    p_clear = sub.add_parser("clear-rfid")
    p_clear.add_argument("channel", type=int)

    args = parser.parse_args()

    if args.command == "get":
        cmd_get(args.host)
    elif args.command == "set-rfid":
        cmd_set_rfid(args.host, args.channel, args.vendor, args.type, args.subtype, args.color)
    elif args.command == "set-user":
        cmd_set_user(args.host, args.channel, args.vendor, args.type, args.subtype, args.color)
    elif args.command == "clear-rfid":
        cmd_clear_rfid(args.host, args.channel)


if __name__ == "__main__":
    main()
