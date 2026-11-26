## What and why

<!-- What does this change, and which problem does it solve? Link the issue, e.g. "Fixes #12". -->

## How it was tested

<!-- Tests added or changed, and manual checks (site, steps). -->

## Checklist

- [ ] `ruff check .` and `ruff format --check .` pass (ruff 0.17.6).
- [ ] `bench --site <test-site> run-tests --app integration_log` passes.
- [ ] README and CHANGELOG are updated if behaviour, hook keys or settings changed.
- [ ] New user-facing strings are translated in `locale/de.po` without redefining Frappe msgids.
- [ ] No credentials or personal data in code, tests or fixtures.
