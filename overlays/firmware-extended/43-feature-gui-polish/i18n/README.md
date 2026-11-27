# Polish touchscreen translation – maintenance tools

`i18n.py` keeps the Polish translation of the stock touchscreen GUI in sync
with new stock firmware. It compares the English texts of the new firmware
with the ones the translation was made from, carries over everything that did
not change and lists only new or changed texts.

Maintainer: @xtronets. User documentation: [docs/touchscreen_language.md](../../../../docs/touchscreen_language.md).

## Files

| File | Description |
|---|---|
| `../root/usr/local/share/gui-polish/pl.json` | Polish UI texts (bind-mounted over `locales/tr-TR.json`) |
| `../root/usr/local/share/gui-polish/exception_pl.json` | Polish error messages (bind-mounted over `text/exception_tr-TR.json`) |
| `source/en-GB.json`, `source/exception_en-GB.json` | English texts the translation was made from |
| `exception_templates.json` | English → Polish error templates; `{N}` = toolhead/feeder number, `{A}` = axis X/Y/Z |
| `todo.json` | written by `update`: what still needs translating |

Error messages are translated through templates, so a new error code that
reuses a known sentence (e.g. for another toolhead) is translated
automatically.

## After a stock firmware update

Run inside `./dev.sh bash` (needs `make tools` and `unsquashfs`):

```bash
I18N=overlays/firmware-extended/43-feature-gui-polish/i18n/i18n.py
$I18N extract firmware/U1_<version>_upgrade.bin tmp/i18n-stock   # stock English texts + label check
$I18N status tmp/i18n-stock                                     # what changed
$I18N update tmp/i18n-stock                                     # merge, write todo.json
# fill in the "pl" fields in todo.json (keep %d, %s, {N}, {A}, \n and the single "_"
# that separates an error's title from its description)
$I18N apply                                                     # merge todo.json, refresh source/, run check
$I18N check                                                     # validation only
```

The build prints `!! WARNING` when the translation is older than the English
texts of the firmware being built; missing texts then fall back to English.
If the "Türkçe" label is no longer found in `/usr/bin/gui`, the relabelled
binary is not built and Polish is listed as "Türkçe" in the language menu.
