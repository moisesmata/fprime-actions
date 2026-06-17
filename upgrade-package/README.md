# nasa/fprime-actions/upgrade-package

Composite action that upgrades a pip package from a GitHub repository, automatically selecting a PR-matching branch when testing cross-repository changes.

## What it does

1. Uses [`get-pr-branch`](../get-pr-branch/) to determine the target branch in the specified repository (checks for `pr-{number}` or matching branch name).
2. If a matching branch is found, upgrades the pip package from that branch using `pip install --upgrade`.
3. If no matching branch exists, the package remains on its current version (no upgrade is performed).

This enables testing coordinated changes across multiple repositories: when a PR in repo A depends on changes in repo B, create a branch with the same name (or `pr-{number}`) in repo B, and this action will automatically pull those changes during CI.

## Inputs

| Input            | Default  | Description                                                                      |
|------------------|----------|----------------------------------------------------------------------------------|
| `repository`     | required | GitHub repository (owner/repo) containing the pip package to upgrade            |
| `default-branch` | `devel`  | Default branch to check if no PR branch is found (used for existence check only)|

## Outputs

| Output          | Description                                                   |
|-----------------|---------------------------------------------------------------|
| `target-branch` | The branch name that was used for the upgrade (or default if no upgrade occurred) |

## Usage

Typical use case: a PR in `fprime` needs to test against changes in `fprime-tools`:

```yaml
steps:
  - uses: nasa/fprime-actions/upgrade-package@devel
    with:
      repository: nasa/fprime-tools
      default-branch: devel
```

If the current PR is #123, this action will:
- Check if `nasa/fprime-tools` has a branch named `pr-123`, or a branch matching the PR branch name
- If found, run `pip install --upgrade "git+https://github.com/nasa/fprime-tools.git@pr-123"`
- If not found, upgrade to `default-branch` (`devel` by default)

## Example workflow

```yaml
name: Test with Development Dependencies

on: pull_request

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      
      - name: Install base dependencies
        run: pip install fprime-tools fprime-gds
      
      - name: Upgrade to PR branch if available
        uses: nasa/fprime-actions/upgrade-package@devel
        with:
          repository: nasa/fprime-gds
      
      - name: Run tests
        run: pytest
```

## Why this pattern

When developing features that span multiple repositories, you often need to test changes together before merging either side. The branch-matching convention (`pr-{number}` or same-name branches) allows CI to automatically test the right combination without manual intervention or hardcoded refs.
See the [F Prime CONTRIBUTING.md](https://github.com/nasa/fprime/blob/devel/CONTRIBUTING.md#automated-checks-on-reference-repositories) for more information on this process.
