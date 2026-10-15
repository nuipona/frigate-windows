# Frigate documentation site

The documentation published at https://docs.frigate.video, built with [Docusaurus 3](https://docusaurus.io/).

## Development

Requires Node.js 20.

```console
cd docs
npm install
npm run start
```

`start` and `build` first run `build:config` (generates the Docker Compose generator's TypeScript from `src/components/DockerComposeGenerator/config/config.yaml`) and `regen-docs` (regenerates the HTTP API reference from `static/frigate-api.yaml`), so both are needed before the site compiles.

Other useful scripts:

| Script | What it does |
| ------ | ------------ |
| `npm run build` | Static build into `build/` |
| `npm run serve` | Serve the last build |
| `npm run gen-api-docs` | Regenerate only the API reference |

## Content

Pages live in `docs/`, and the sidebar order in `sidebars.ts`. Two things are generated and must not be hand-edited:

- `static/frigate-api.yaml`, from the FastAPI app. Regenerate with `python3 generate_api_auth_spec.py` from the repository root.
- The "Frigate UI" tabs inside configuration pages. See [scripts/README.md](scripts/README.md); `python3 docs/scripts/generate_ui_tabs.py --check docs/docs/configuration/` reports drift.

For the wider contribution guide, see [development/contributing.md](docs/development/contributing.md).
