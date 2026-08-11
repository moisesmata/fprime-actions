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
Only the flags that must be namespaced per deployment are injected on the GDS
`ExecStart` line: `--no-app`, `--logs`, `--dictionary`, and `--zmq-transport`.
**Communication selection is owned by the deployment's `fprime-gds.yml`** — the
action no longer hardcodes comm per platform, so whatever the deployment ships
(UART, IP, radio framing, file-uplink pacing, …) is what the GDS uses.

> [!WARNING]
> **This action runs privileged commands and has security implications —
> examine it carefully before use.** It installs and manages `systemd` services
> and grants Linux capabilities. The only commands invoked via `sudo` are
> `systemctl`, `journalctl`, `setcap`, and `tee` pinned to the service-file
> path `/etc/systemd/system/fprime-soak-*.service`. Passwordless `sudo` should be
> granted for exactly these commands — and nothing else — on the target
> hardware (the runner, and the remote Pi for `linux-remote`).
> Only enable this on hardware you control and are willing to grant that access.

## Platforms

Selected by the `platform` input, handled inline in the two scripts:

| `platform`     | FSW location                                    | GDS communication          |
|----------------|-------------------------------------------------|----------------------------|
| `linux`        | local runner                                    | per deployment `fprime-gds.yml` |
| `linux-remote` | remote Pi over SSH (user `fprime`)              | per deployment `fprime-gds.yml` |
| `pico2`        | board flashed by `fprime-ci`; serial verified   | per deployment `fprime-gds.yml` |

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
fprime-gds.yml                       # optional; the deployment's own GDS config
```

## Inputs

| Input             | Default        | Description                                                                          |
|-------------------|----------------|--------------------------------------------------------------------------------------|
| `deployment-name` | *(required)*   | Namespaces the install dir, systemd services, and ZMQ sockets.                       |
| `platform`        | *(required)*   | `linux` \| `linux-remote` \| `pico2`.                                                |
| `fsw-ip`          | `""`           | Remote FSW IP (`linux-remote` only). SSH user is `fprime`.                            |
| `fsw-args`        | `""`           | Launch args for the remote FSW (`linux-remote` only). Deployment-specific: a TCP FSW wants e.g. `-a <addr> -p <port>`, a radio-only FSW takes none. |
| `fsw-device`      | `/dev/pico2`   | FSW serial device (`pico2` only).                                                    |
| `gds-args`        | `""`           | Extra flags appended to `fprime-gds`. Communication selection is driven by the deployment's `fprime-gds.yml`; only the namespaced `--zmq-transport` flag is added automatically. |

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

> Requires a self-hosted runner with `systemd` and passwordless `sudo` limited
> to `systemctl`, `journalctl`, `setcap`, and `tee` pinned to
> `/etc/systemd/system/fprime-soak-*.service`. Meant to run once (setup); the
> periodic [`soak-test`](../soak-test/) action runs on a schedule against the
> services it leaves running.
