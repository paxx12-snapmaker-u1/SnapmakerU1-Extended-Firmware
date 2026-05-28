#!/usr/bin/env python3

import sys
import os
import re
import configparser

def get_value(cfg_file, section, key, default = None):
    """Get a value from the config file."""
    cfg = configparser.ConfigParser()
    cfg.read(cfg_file)

    if default is None:
        if not cfg.has_section(section):
            raise KeyError(f"Section '{section}' not found in config")
        if not cfg.has_option(section, key):
            raise KeyError(f"Key '{key}' not found in section '{section}'")

    print(cfg.get(section, key, fallback=default).strip())

def set_value(cfg_file, section, key, value, create_section=True, create_key=True):
    """Comment out a section header and all its keys."""
    if not os.path.exists(cfg_file):
        print(f"ERROR: Config file '{cfg_file}' does not exist", file=sys.stderr)
        sys.exit(1)

    with open(cfg_file, 'r') as f:
        lines = f.readlines()

    any_section_re = re.compile(r"^\s*\[.*\]\s*$")
    section_re = re.compile(rf"\[\s*{re.escape(section)}\s*\]\s*$")
    key_re = re.compile(rf"^\s*{re.escape(key)}\s*[=:]")

    section_found = False
    key_found = False
    i = 0

    while i < len(lines):
        line = lines[i]
        if any_section_re.match(line) and section_found:
            break
        if section_re.match(line):
            section_found = True
        elif section_found and line.strip() == "":
            break
        elif section_found and key_re.match(line):
            if key_found:
                print(f"ERROR: Duplicate key '{key}' found in section '{section}'", file=sys.stderr)
                sys.exit(1)
            lines[i] = f"{key}: {value}\n"
            key_found = True
        i += 1

    if not section_found:
        if not create_section:
            print(f"ERROR: Section '{section}' does not exist for update", file=sys.stderr)
            sys.exit(1)
        if i > 0:
            lines.insert(i, "\n")
            i += 1
        lines.insert(i, f"[{section}]\n")
        i += 1

    if not key_found:
        if not create_key:
            print(f"ERROR: Key '{key}' does not exist in section '{section}' for update", file=sys.stderr)
            sys.exit(1)
        lines.insert(i, f"{key}: {value}\n")

    with open(cfg_file, 'w') as f:
        f.writelines(lines)

    print(f"[{section}] {key}: {value}")

def comment_section(cfg_file, section_name):
    """Comment out a section header and all its keys."""
    if not os.path.exists(cfg_file):
        print(f"ERROR: Config file '{cfg_file}' does not exist", file=sys.stderr)
        sys.exit(1)

    with open(cfg_file, 'r') as f:
        lines = f.readlines()

    section_re = re.compile(rf"\[\s*{re.escape(section_name)}\s*\]\s*$")
    comment_re = re.compile(r"^\s*#")
    comment = False
    changed = []

    for i, line in enumerate(lines):
        if section_re.match(line):
            comment = True
        if comment and line.strip() == "":
            break
        elif comment and not comment_re.match(line):
            lines[i] = "# " + line
            changed.append(lines[i])

    if comment:
        with open(cfg_file, 'w') as f:
            f.writelines(lines)
        print("OK")
        print("".join(changed).rstrip())
    else:
        print(f"No section found to comment")

def uncomment_section(cfg_file, section_name):
    """Uncomment a section header and all its keys."""
    if not os.path.exists(cfg_file):
        print(f"ERROR: Config file '{cfg_file}' does not exist", file=sys.stderr)
        sys.exit(1)

    with open(cfg_file, 'r') as f:
        lines = f.readlines()

    uncomment = False
    section_re = re.compile(rf"#\s*\[{re.escape(section_name)}\]\s*$")
    comment_re = re.compile(r"^#\s*")
    changed = []

    for i, line in enumerate(lines):
        if section_re.match(line):
            uncomment = True
        if uncomment and comment_re.match(line):
            lines[i] = comment_re.sub("", line)
            changed.append(lines[i])
        elif uncomment and line.strip() == "":
            break

    if uncomment:
        with open(cfg_file, 'w') as f:
            f.writelines(lines)
        print("OK")
        print("".join(changed).rstrip())
    else:
        print(f"No section found to uncomment")

def main():
    if len(sys.argv) < 2:
        print("Usage: extended-config.py <operation> <cfg_file> <section> [key] [value]", file=sys.stderr)
        print("  get <cfg_file> <section> <key> [default]  - Get a config value", file=sys.stderr)
        print("  add <cfg_file> <section> <key> <value>    - Set a config value (creates if missing)", file=sys.stderr)
        print("  update <cfg_file> <section> <key> <value> - Update existing config value only", file=sys.stderr)
        print("  comment <cfg_file> <section>              - Comment section header", file=sys.stderr)
        print("  uncomment <cfg_file> <section>            - Uncomment section header", file=sys.stderr)
        sys.exit(1)

    if sys.argv[1] == 'get' and len(sys.argv) == 5:
        _, cfg_file, section, key = sys.argv[1:5]
        get_value(cfg_file, section, key)
    elif sys.argv[1] == 'get' and len(sys.argv) == 6:
        _, cfg_file, section, key, default = sys.argv[1:6]
        get_value(cfg_file, section, key, default)
    elif sys.argv[1] == 'add' and len(sys.argv) == 6:
        _, cfg_file, section, key, value = sys.argv[1:6]
        set_value(cfg_file, section, key, value, create_section=True, create_key=True)
    elif sys.argv[1] == 'update' and len(sys.argv) == 6:
        _, cfg_file, section, key, value = sys.argv[1:6]
        set_value(cfg_file, section, key, value, create_section=False, create_key=True)
    elif sys.argv[1] == 'comment' and len(sys.argv) == 4:
        _, cfg_file, section = sys.argv[1:4]
        comment_section(cfg_file, section)
    elif sys.argv[1] == 'uncomment' and len(sys.argv) == 4:
        _, cfg_file, section = sys.argv[1:4]
        uncomment_section(cfg_file, section)
    else:
        print("ERROR: Invalid arguments", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
