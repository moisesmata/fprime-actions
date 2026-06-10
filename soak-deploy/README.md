# nasa/fprime-actions/soak-deploy

The `soak-deploy` action installs a previously-built F´ deployment onto a
self-hosted runner as two **persistent** `systemd` services:

* `<service-prefix>-fsw` &mdash; the flight-software binary.
* `<service-prefix>-gds` &mdash; a headless `fprime-gds` client that stays
  connected to the flight software for the entire soak.

The install tree is owned by the runner user (default `$HOME/fprime-soak`); the
action only uses `sudo` for systemd operations (`systemctl`, `journalctl`, and
writing unit files into `/etc/systemd/system/`).

The systemd unit files are rendered from readable templates
([`templates/fsw.service`](templates/fsw.service),
[`templates/gds.service`](templates/gds.service)). The GDS unit's `ExecStart`
is intentionally minimal: `fprime-gds` reads its arguments from
[`templates/fprime-gds.yml`](templates/fprime-gds.yml), which is rendered into
`<install-dir>/fprime-gds.yml` and picked up because the service runs from
`<install-dir>` as its working directory.

## Inputs

| Input                  | Default            | Description                                                                                          |
|------------------------|--------------------|------------------------------------------------------------------------------------------------------|
| `install-dir`          | `$HOME/fprime-soak`| Directory to install the soak deployment into (must be writable by the runner user).                |
| `binary`               | (required)         | Path (glob allowed) to the built flight-software binary.                                             |
| `install-binary-name`  | `fsw`              | Name to install the binary as (keeps the service ExecStart stable across renames).                  |
| `dictionary`           | (required)         | Path (glob allowed) to the deployment topology dictionary JSON.                                      |
| `requirements`         | (required)         | `requirements.txt` used to build the soak virtualenv (must install `fprime-gds`).                   |
| `extra-pip-packages`   | `""`               | Extra pip packages for the virtualenv (e.g. `psutil`).                                               |
| `test-source`          | `""`               | Optional directory of integration tests to install into `<install-dir>/test`.                       |
| `service-prefix`       | `fprime-soak`      | Prefix for the service names (`<prefix>-fsw`, `<prefix>-gds`).                                       |
| `service-user`         | current user       | Linux user to run the services as.                                                                   |
| `fsw-bind-address`     | `0.0.0.0`          | Address the flight software binds for the GDS TCP connection.                                        |
| `fsw-port`             | `50000`            | TCP port the flight software listens on.                                                             |
| `fsw-extra-args`       | `""`               | Extra arguments appended to the flight-software ExecStart.                                           |
| `gds-connect-address`  | `127.0.0.1`        | Address the persistent GDS connects to.                                                              |
| `framing-selection`    | `""`               | `fprime-gds --framing-selection` value (e.g. `space-packet-space-data-link`). Empty omits the option. |
| `com-logger-subdir`    | `ComLoggerFiles`   | Subdir under `<install-dir>` for `Svc::ComLogger` `.com` files (created/cleared). Empty to skip.    |

## Outputs

| Output        | Description                                               |
|---------------|-----------------------------------------------------------|
| `install-dir` | Absolute install directory.                               |
| `venv`        | Path to the soak virtualenv.                              |
| `dictionary`  | Absolute path to the installed dictionary JSON.           |
| `com-logs`    | Absolute path to the ComLogger directory (empty if off).  |
| `fsw-service` | Name of the flight-software systemd service.              |
| `gds-service` | Name of the persistent GDS systemd service.               |

## Usage

```yaml
- uses: actions/download-artifact@v4
  with:
    name: soak-build-artifacts
    path: artifacts
- id: soak
  uses: nasa/fprime-actions/soak-deploy@devel
  with:
    binary: "artifacts/*/MyDeployment/bin/MyDeployment"
    dictionary: "artifacts/*/MyDeployment/dict/*TopologyDictionary.json"
    requirements: "./lib/fprime/requirements.txt"
    test-source: "MyProject/MyDeployment/test/int"
    framing-selection: "space-packet-space-data-link"
```

> This action requires a self-hosted runner with `sudo` (for `systemctl` only)
> and `systemd`. It is meant to run once (setup); the periodic
> [`soak-monitor`](../soak-monitor/) action then runs on a schedule against the
> services it leaves running. Integration tests can be run directly from the
> calling workflow against `<install-dir>/venv` and `<install-dir>/test`.
