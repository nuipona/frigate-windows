# Contributing to Frigate

Thank you for your interest in contributing. Read this before submitting a pull request.

All participation in this project, including pull requests, issues, and discussions, is covered by our [AI policy](AI_POLICY.md).

This repository (`nuipona/frigate-windows`) is a fork of `blakeblackshear/frigate` that adds native Windows support. Issues and discussions are turned off here, so report bugs and propose features [upstream](https://github.com/blakeblackshear/frigate/issues), and say so when they only affect the native Windows install. Pull requests for the Windows port go to this repository, against `dev`.

## Before you start

### Bugfixes

If you have found a bug and want to fix it, go for it. Link the relevant issue in your PR if one exists, or describe the bug in the PR description.

### New features

A pull request is more than just code: it is a request for the maintainers to review, integrate, and support the change long term. We are selective, and prioritize changes that fit the project's direction and can be maintained responsibly.

**Large or highly-requested features** raise the bar further. Popularity signals demand, but it does not pre-approve an implementation. The bigger the change, the higher the long-term cost, and the more important it is that we agree on scope and approach before code is written. A large PR that lands without prior discussion is unlikely to be merged as-is, however well it is implemented.

Before writing code for a new feature:

1. **Check for existing discussion.** Search upstream [feature requests](https://github.com/blakeblackshear/frigate/issues) and [discussions](https://github.com/blakeblackshear/frigate/discussions), and the open pull requests here. Feature requests tagged "planned" are on our radar, but we publish no roadmap or timeline.
2. **Start a discussion or feature request first.** That tells you whether the idea fits before you invest time building it.

## AI usage policy

AI tools are a reality of modern development and we are not opposed to their use. But we need to understand your relationship with the code you submit, and we need to hear from you rather than from your AI assistant.

**Read the [AI policy](AI_POLICY.md) before you open a pull request.** It is short, and it applies to everything you post here. The parts that most often catch people out:

- A person has to be in the loop. Do not wire a bot or agent up to open pull requests, issues, or discussions on your behalf.
- Disclose how AI was used, in the `## AI disclosure` section of the PR description. Be honest; it will not automatically disqualify your PR.
- Review and test everything you submit, and be prepared to explain every line when asked.
- Do not use AI to write your PR description or your replies to maintainers.

Pull requests that appear to be unreviewed AI output will be closed without review.

## Pull request guidelines

### Before submitting

- **Search for existing PRs** to avoid duplicating effort.
- **Structure the PR description** with the four sections `## Proposed change`, `## Type of change`, `## AI disclosure` and `## Checklist`, and tick at least one box in each of the last three. `pr_template_check.yml` closes a PR that is missing them.
- **Test your changes locally.** Your PR cannot be merged unless the checks pass.
- **Format your code.** `ruff format frigate migrations docker *.py`, and `npm run prettier:write` from `web/`.
- **Run the linter.** `ruff check frigate migrations docker *.py`, and `npm run lint` from `web/`.
- **Regenerate what is generated.** `python3 generate_api_auth_spec.py` after an API change, `python3 generate_config_translations.py` after a config schema change, `npm run i18n:extract` after new `t()` calls. CI fails when these are out of date.
- **Keep upstream parity recorded.** If your change makes this tree differ from upstream in a new place, run `python testing-scripts/check_upstream_parity.py --update` and write a reason for the new entry.
- **One concern per PR.** Do not combine unrelated changes.

### What we look for in review

- **Does it work?** Tested locally, checks pass, no regressions.
- **Is it maintainable?** Clear code, appropriate complexity, good separation of concerns.
- **Does it fit?** Consistent with Frigate's architecture and design philosophy, and with the Windows port's rules in [AGENTS.md](AGENTS.md#native-windows).
- **Is it scoped well?** Solves the stated problem without unnecessary additions.

### After submitting

- Be responsive to review feedback. We may ask for changes.
- Expect honest, direct feedback. We try to be respectful and efficient.
- If your PR goes stale, rebase it on the latest `dev` branch.

## Coding standards

[AGENTS.md](AGENTS.md) has the full conventions for both halves of the codebase. The short version:

- **Python**: modern language features, Ruff for formatting and linting, `python3 -u -m unittest` for tests, module-level `logger = logging.getLogger(__name__)` with lazy formatting, async for all external I/O, specific exception types, American English.
- **TypeScript/React**: ESLint, Prettier, strict TypeScript with no `any`, all user-facing strings through `react-i18next`, Radix UI/shadcn primitives with TailwindCSS and the `cn()` utility.
- **Generated files**: never edit `docs/static/frigate-api.yaml` or the `config` locale JSON by hand. Regenerate them.

## Project structure

```
frigate/           # Python backend
  api/             # FastAPI route handlers
  config/          # Configuration parsing and validation
  detectors/       # Object detection backends
  events/          # Event management and storage
  test/            # Backend tests
  util/            # Shared utilities, including platform.py (the Windows port)
web/               # React/TypeScript frontend
  src/
    api/           # API client functions
    components/    # Reusable components
    hooks/         # Custom React hooks
    pages/         # Route components
    types/         # TypeScript type definitions
    views/         # Complex view components
docker/            # Docker build files and requirements-windows.txt
docs/              # Documentation site
migrations/        # Database migrations
scripts/           # Windows service installer
testing-scripts/   # Parity, requirements, and benchmark helpers
```

## Translations

Frigate uses [Weblate](https://hosted.weblate.org/projects/frigate-nvr/) for language translations:

1. Visit the [Frigate project on Weblate](https://hosted.weblate.org/projects/frigate-nvr/).
2. Create an account or log in.
3. Select a language, or request a new one.
4. Translate in the Weblate interface. No code changes or pull requests needed.

Translations sync to the repository automatically. Please do not submit pull requests for translation changes.

## Resources

- [Documentation](https://docs.frigate.video)
- [Windows install guide](docs/docs/frigate/installation.md#windows)
- [Windows port audit](WINDOWS_COMPATIBILITY.md)
- [Agent and contributor conventions](AGENTS.md)
- [Upstream issues](https://github.com/blakeblackshear/frigate/issues) and [discussions](https://github.com/blakeblackshear/frigate/discussions)
