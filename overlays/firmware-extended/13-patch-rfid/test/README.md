# RFID OpenSpool CLI Test Tool

This CLI tool allows you to test OpenSpool JSON payloads and NDEF binary data parsing.

## Usage

```bash
python3 -m app.cli <file>
```

The CLI automatically detects the file type:

- `.json` files are parsed as OpenSpool JSON payloads
- Other files are parsed as NDEF binary data

## Testing Examples

```bash
python3 -m app.cli openspool-pla-basic.json
python3 -m app.cli openspool-petg-rapid.json
python3 -m app.cli openspool-silk-multicolor.json
python3 -m app.cli openspool-silk-gradient.json
python3 -m app.cli openspool-abs-transparent.json
python3 -m app.cli openspool-tpu-flexible.json
```

## Snapmaker Orca Naming Convention

For proper recognition in Snapmaker Orca, filaments are named: `<brand> <type> <subtype>`

Examples:

- `Generic PLA Basic`
- `Elegoo PETG Rapid`
- `Overture PLA Silk`
