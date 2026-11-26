# Contributing

Thanks for helping to improve Integration Log. Bug reports, ideas and pull requests are all welcome.
For bugs and feature requests, please use the
[issue templates](https://github.com/hotfix/frappe-integration-log/issues/new/choose). For larger changes,
open an issue first so we can agree on the approach before you spend time on it.

## Development setup

You need a Frappe v16 bench (see the
[Frappe installation guide](https://docs.frappe.io/framework/user/en/installation)). ERPNext is not required.

```bash
cd frappe-bench
bench get-app https://github.com/hotfix/frappe-integration-log
bench new-site dev.localhost
bench --site dev.localhost install-app integration_log
bench --site dev.localhost set-config allow_tests true
bench build --app integration_log
```

To work on your fork, clone it into `apps/integration_log` instead (or use `bench get-app` with the URL of
your fork).

Use a separate development site. The tests create and delete Integration Log entries and ToDo records.

## Running the tests

```bash
bench --site dev.localhost run-tests --app integration_log
```

A single module:

```bash
bench --site dev.localhost run-tests --module integration_log.tests.test_mirror
```

CI runs the same suite on every push and pull request (see `.github/workflows/ci.yml`).

## Code style

The code is checked with [ruff](https://docs.astral.sh/ruff/) 0.17.6, the version used in CI:

```bash
pip install ruff==0.17.6
ruff check .
ruff format --check .
```

Python files are indented with tabs (see `pyproject.toml`).

## Translations

German translations live in `integration_log/locale/de.po`.

- Wrap user-facing strings in `_()` (Python) or `__()` (JavaScript).
- **Never override core msgids.** Do not add a msgid to `de.po` that Frappe or ERPNext (when installed)
  already translate; that would change the wording in every other app on the site. Use a different source
  string or a context instead. `tests/test_translations.py` checks this.
- After changing strings:

  ```bash
  bench --site dev.localhost generate-pot-file --app integration_log
  bench --site dev.localhost update-po-files --app integration_log
  bench compile-po-to-mo --app integration_log
  ```

## Documentation

If you change behaviour, hook keys or settings, update `README.md` and add a line to the "Unreleased"
section of `CHANGELOG.md`.

## Commits and pull requests

- Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/): `feat: ...`,
  `fix: ...`, `docs: ...`, `test: ...`, `ci: ...`, `chore: ...`, optionally with a scope such as
  `fix(mirror): ...`. Use the imperative and keep the first line short.
- One logical change per pull request, with tests for new behaviour and bug fixes.
- Never commit credentials or personal data, also not in tests or fixtures.

By contributing you agree that your contribution is licensed under the GPLv3, like the rest of the project.
