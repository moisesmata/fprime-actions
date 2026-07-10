# nasa/fprime-actions/soak-setup

The `soak-setup` action stages a previously-built F´ deployment onto a
self-hosted runner and starts the persistent GDS + FSW services a soak runs
against. It runs in two steps:

1. **Setup** ([`scripts/setup.sh`](scripts/setup.sh)) — stages artifacts into
   `$HOME/fprime-soak-<deployment-name>/`, builds the soak venv (installs
   `fprime-gds`), and starts the persistent GDS systemd service
   `fprime-soak-gds-<deployment-name>`.
2. **Deploy** ([`scripts/deploy.sh`](scripts/deploy.sh)) — brings up the FSW for
   the target platform.

Everything is namespaced by `deployment-name` (install dir, systemd services,
and ZMQ sockets) so multiple deployments can soak on one runner.

The systemd unit files are rendered from templates in
[`templates/`](templates/) (`gds.service.template`, `fsw.service.template`).
Soak-specific GDS flags (`--no-app`, `--logs`, `--dictionary`, and the
communication / `--zmq-transport` flags) are set on the GDS `ExecStart` line;
the deployment's own `fprime-gds.yml`, if supplied, stays owned by the
deployment.

Uses `sudo` for systemd operations (`systemctl`, `journalctl`, writing unit
files into `/etc/systemd/system/`) and `setcap`.

## Platforms

Selected by the `platform` input, handled inline in the two scripts:

| `platform`     | FSW location                                    | GDS communication |
|----------------|-------------------------------------------------|-------------------|
| `linux`        | local runner                                    | ZMQ (IPC)         |
| `linux-remote` | remote Pi over SSH (user `fprime`), IP/port set | IP client         |
| `pico2`        | board flashed by `fprime-ci`; serial verified   | UART              |

## Artifact contract

The calling workflow uploads a soak artifact; the action expects it staged at
the runner's working directory before it runs.

For `linux` / `linux-remote`, artifacts are downloaded into `./artifacts/`:

```
artifacts/build-artifacts/<arch>/<deployment>/bin/<binary>
artifacts/build-artifacts/<arch>/<deployment>/dict/*TopologyDictionary.json
artifacts/int/
artifacts/fprime-gds.yml        # optional; the deployment's own GDS config
```

For `pico2`, `fprime-ci` produces an `archive.tar.gz` at the working directory
which the action extracts in place, plus the target repo checked out at `./`:

```
build-artifacts/<arch>/<deployment>/dict/*TopologyDictionary.json
<project>/<deployment>/test/int/     # integration tests from the checkout
```

## Inputs

| Input             | Default        | Description                                                                          |
|-------------------|----------------|--------------------------------------------------------------------------------------|
| `deployment-name` | *(required)*   | Namespaces the install dir, systemd services, and ZMQ sockets.                       |
| `platform`        | *(required)*   | `linux` \| `linux-remote` \| `pico2`.                                                |
| `fsw-ip`          | `""`           | Remote FSW IP (`linux-remote` only). SSH user is `fprime`.                            |
| `fsw-port`        | `50000`        | FSW TCP port (`linux-remote` only).                                                  |
| `fsw-device`      | `/dev/pico2`   | FSW serial device (`pico2` only).                                                    |
| `gds-args`        | `""`           | Extra flags appended to `fprime-gds`. Communication and `--zmq-transport` flags are added automatically. |

## Usage

```yaml
# linux / linux-remote: download the built artifact first
- uses: actions/download-artifact@v4
  with:
    name: my-soak-artifact
    path: artifacts
- uses: nasa/fprime-actions/soak-setup@devel
  with:
    deployment-name: my-deployment
    platform: linux
```

> Requires a self-hosted runner with `systemd` and `sudo` (for `systemctl`,
> `journalctl`, unit-file writes, and `setcap`). Meant to run once (setup); the
> periodic [`soak-test`](../soak-test/) action runs on a schedule against the
> services it leaves running.
