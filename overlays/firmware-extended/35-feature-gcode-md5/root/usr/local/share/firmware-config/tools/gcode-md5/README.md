# gcode-md5 helper scripts

Slicer-host helper scripts for stamping MD5 checksums onto g-code files.

These scripts are installed on the printer at
`/usr/local/share/firmware-config/tools/gcode-md5/` and are available for
direct download from this directory in the repository.

## Scripts

| Script | Platform |
|---|---|
| `add_md5.sh` | Linux, macOS |
| `add_md5.bat` | Windows |

## How it works

Each script prepends a single comment line to the g-code file:

```
; MD5:<hash>
```

The hash covers **all content after that line**.

## Usage

```sh
# Linux / macOS
./tools/gcode-md5/add_md5.sh MyPrint.gcode

# Windows
tools\gcode-md5\add_md5.bat MyPrint.gcode
```

## Slicer post-processing integration

### Snapmaker Orca / OrcaSlicer / BambuStudio
*Process → Others → Post-processing Scripts:*
```
/full/path/to/add_md5.sh;
```

### PrusaSlicer
*Print Settings → Output options → Post-processing scripts:*
```
/full/path/to/add_md5.sh;
```

The slicer passes the output file as the first argument automatically.

## Requirements

| Platform | Requirement |
|---|---|
| Linux | `md5sum` (standard on all distros) |
| macOS | `md5` (built-in) |
| Windows | `CertUtil` (built into Windows Vista+) |
