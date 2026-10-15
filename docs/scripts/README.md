# Documentation scripts

## generate_ui_tabs.py

Generates the "Frigate UI" tab of the `<ConfigTabs>` blocks in the configuration docs from the YAML examples already on the page, so UI instructions do not have to be written by hand for every block. It reads three sources:

1. The JSON schema built from the Pydantic config models: field names, types, defaults
2. The i18n translation files: the exact labels the Settings UI shows
3. The section configs and navigation map from the web app: config key to UI path

Run it from the repository root; it imports Frigate's config models, so `frigate` must be importable.

```bash
python3 docs/scripts/generate_ui_tabs.py --help
```

### Modes

| Mode | Effect |
| ---- | ------ |
| none (default) | Print what would be generated for each bare YAML block, changing nothing |
| `--inject` | Wrap bare YAML blocks in `<ConfigTabs>`, insert the generated UI tab, and add the `ConfigTabs` / `TabItem` / `NavPath` imports if missing. Already-wrapped blocks are skipped |
| `--check` | Diff the existing UI tabs against what the current schema and i18n files produce; exit 1 on drift. This is what CI runs |
| `--regenerate` | Rewrite only the UI tab inside existing `<ConfigTabs>` blocks, leaving the YAML tab untouched |

Options: `--outdir DIR` writes a mirrored copy of the tree instead of editing in place (works with `--inject` and `--regenerate`), `--dry-run` prints the `--regenerate` diffs without writing, and `-v` explains every skipped and unchanged block.

```bash
# Preview across all config docs without touching anything
python3 docs/scripts/generate_ui_tabs.py --inject --outdir /tmp/ui-preview docs/docs/configuration/
diff -ru docs/docs/configuration/ /tmp/ui-preview/

# Apply, then verify after schema or i18n changes
python3 docs/scripts/generate_ui_tabs.py --inject docs/docs/configuration/
python3 docs/scripts/generate_ui_tabs.py --check docs/docs/configuration/

# Re-sync drifted tabs
python3 docs/scripts/generate_ui_tabs.py --regenerate docs/docs/configuration/
```

### What it generates

- **Field table**, when the YAML carries inline `# <- description` comments: a `Navigate to <NavPath ... />` line plus a table of field names and descriptions.
- **Set instructions**, when the YAML has concrete values and no comments: a numbered list of `Set **Field** to \`value\`` steps.
- **Camera-level config** is detected from a block nested under `cameras:` and addressed generically ("select your camera") rather than by the example camera's name.

Blocks are skipped when they are already inside `<ConfigTabs>`, when their top-level key is not a known config section (`go2rtc`, `docker-compose`, `scrape_configs`), and for the fields the section configs list as hidden.

### Data sources

| Source | Path | Provides |
| ------ | ---- | -------- |
| Pydantic models | `frigate/config/` | Field names, types, defaults, nesting, via a JSON schema built at runtime |
| i18n (global) | `web/public/locales/en/config/global.json` | Labels for global settings |
| i18n (cameras) | `web/public/locales/en/config/cameras.json` | Labels for camera settings |
| i18n (menu) | `web/public/locales/en/views/settings.json` | Sidebar menu labels |
| Section configs | `web/src/components/config-form/section-configs/*.ts` | Hidden and advanced fields, field order |
| Navigation map | `lib/nav_map.py`, transcribed from `web/src/pages/Settings.tsx` | Config section to UI path |

### Layout

```
docs/scripts/
├── generate_ui_tabs.py          # CLI entry point
├── README.md                    # This file
└── lib/
    ├── schema_loader.py         # JSON schema from the Pydantic models
    ├── i18n_loader.py           # i18n translation JSON files
    ├── section_config_parser.py # TS section configs (hiddenFields, ...)
    ├── yaml_extractor.py        # YAML blocks and ConfigTabs in markdown
    ├── ui_generator.py          # UI tab markdown
    └── nav_map.py               # Config section to Settings UI path
```
