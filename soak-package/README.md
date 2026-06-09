# nasa/fprime-actions/soak-package

The `soak-package` action assembles everything the soak deploy job needs into a
single directory ready for `actions/upload-artifact`: the cross-compiled
`build-artifacts/`, the deployment's integration tests (as `int/`), and a
`requirements.txt` used by [`soak-deploy`](../soak-deploy/) to build the soak
virtualenv on the target runner.

## Inputs

| Input             | Default                          | Description                                                       |
|-------------------|----------------------------------|-------------------------------------------------------------------|
| `build-artifacts` | `./build-artifacts`              | Path to the build-artifacts directory produced by the build.      |
| `test-source`     | `""`                             | Directory of integration tests to include. Empty to skip.        |
| `requirements`    | `./lib/fprime/requirements.txt`  | `requirements.txt` to include for the soak virtualenv.           |
| `output`          | `soak-artifact`                  | Directory to assemble the soak artifact into.                    |

## Outputs

| Output | Description                                                          |
|--------|-----------------------------------------------------------------------|
| `path` | The assembled directory (pass to `actions/upload-artifact`'s `path`). |

## Usage

```yaml
- id: package
  uses: nasa/fprime-actions/soak-package@devel
  with:
    test-source: "MyProject/MyDeployment/test/int"
- uses: actions/upload-artifact@v4
  with:
    name: soak-artifact
    path: ${{ steps.package.outputs.path }}
    retention-days: 5
```
