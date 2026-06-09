# nasa/fprime-actions/soak-integration-test

The `soak-integration-test` action runs `pytest` integration tests against the
**persistent** soak GDS deployed by [`soak-deploy`](../soak-deploy/). Unlike a
normal integration-test run (where the GDS launches the flight software itself),
the soak GDS and flight software are already running as long-lived services, so
pytest connects a fresh ZeroMQ subscriber to a system that is already producing
telemetry.

That introduces a ZeroMQ "slow joiner" race: pytest's subscription is briefly
not live after it connects, so the first command's response events can be
dropped and the first assertion fails with `0` events. This action ships a small
pytest plugin ([`soak_settle_plugin.py`](soak_settle_plugin.py)) that waits until
telemetry is actually flowing (proving the subscription is live) and then settles
briefly before any test sends a command. The plugin is loaded via `pytest -p`
(it is placed on `PYTHONPATH`), so it never clobbers a deployment's own
`conftest.py`.

## Inputs

| Input              | Default | Description                                                                                                          |
|--------------------|---------|----------------------------------------------------------------------------------------------------------------------|
| `dictionary`       | (req.)  | Path to the deployment dictionary JSON passed to pytest.                                                             |
| `test-path`        | (req.)  | Path to the integration test file or directory to run.                                                              |
| `working-directory`| `.`     | Directory to run pytest from.                                                                                        |
| `venv`             | `""`    | Virtualenv to activate before running pytest (use the persistent soak venv so it shares `fprime-gds`).              |
| `pytest-args`      | `""`    | Extra arguments passed to pytest (e.g. `--deployment-config path`).                                                 |
| `timeout`          | `"300"` | Timeout in seconds for the pytest run.                                                                              |
| `settle-timeout`   | `"30"`  | Max seconds to wait for live telemetry before running tests.                                                       |
| `settle-seconds`   | `"5"`   | Extra settle seconds after live telemetry is confirmed, so any downlink backlog flushes.                           |

## Usage

```yaml
- uses: nasa/fprime-actions/soak-integration-test@devel
  with:
    venv: /opt/fprime-soak/venv
    dictionary: /opt/fprime-soak/dict/MyDeploymentTopologyDictionary.json
    test-path: /opt/fprime-soak/test/int
    working-directory: /opt/fprime-soak
```

This action does **not** start a GDS or flight software; it expects the
persistent services from [`soak-deploy`](../soak-deploy/) to already be running.
