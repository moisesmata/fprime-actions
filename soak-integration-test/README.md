# nasa/fprime-actions/soak-integration-test

The `soak-integration-test` action runs integration tests on the FSW deployed by
[`soak-deploy`](../soak-deploy/). The flight software and GDS are already running
as persistent services with telemetry flowing steadily, so pytest simply connects
and tests.

## Inputs

| Input              | Default | Description                                                                                                          |
|--------------------|---------|----------------------------------------------------------------------------------------------------------------------|
| `dictionary`       | (req.)  | Path (glob allowed) to the deployment dictionary JSON passed to pytest.                                              |
| `test-path`        | (req.)  | Path to the integration test file or directory to run.                                                              |
| `fsw-service`      | `""`    | Optional systemd service name to verify is active before running tests (e.g. `fprime-soak-fsw`).                    |
| `working-directory`| `.`     | Directory to run pytest from.                                                                                        |
| `venv`             | `""`    | Virtualenv to activate before running pytest (use the persistent soak venv so it shares `fprime-gds`).              |
| `pytest-args`      | `""`    | Extra arguments passed to pytest (e.g. `--deployment-config path`).                                                 |
| `timeout`          | `"300"` | Timeout in seconds for the pytest run.                                                                              |

## Usage

```yaml
- uses: nasa/fprime-actions/soak-integration-test@devel
  with:
    venv: /opt/fprime-soak/venv
    dictionary: "/opt/fprime-soak/dict/*TopologyDictionary.json"
    test-path: /opt/fprime-soak/test
    working-directory: /opt/fprime-soak
    fsw-service: fprime-soak-fsw
```

This action does **not** start a GDS or flight software; it expects the
persistent services from [`soak-deploy`](../soak-deploy/) to already be running.
