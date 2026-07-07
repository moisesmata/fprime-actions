# nasa/fprime-actions/soak-setup

The `soak-setup` action installs a previously-built F´ deployment for
long-duration soak testing. One script ([`scripts/setup.sh`](scripts/setup.sh))
does everything: stage artifacts into `$HOME/fprime-soak-<deployment-name>`,
build a soak virtualenv, start the flight software, and leave two persistent
systemd services running on the runner:

- `fprime-soak-gds-<deployment-name>` - the GDS, connected to the FSW.
- `fprime-soak-rotation-<deployment-name>` - the flight-ops rotation worker
  (see below).

Everything is namespaced by `deployment-name` (install dir, services, ZMQ
sockets), so multiple deployments soak side by side on one runner. Re-running
setup for a deployment stops its old services, wipes its install area, and
starts fresh. Requires `sudo` for `systemctl`/`journalctl`/unit installs; unit
files are rendered inline by `setup.sh`.

## Platforms

Platform details are fixed conventions, not inputs:

| Platform       | Flight software                                                                    | GDS link                 |
|----------------|------------------------------------------------------------------------------------|--------------------------|
| `linux`        | Run locally on the runner as a systemd service (`fprime-soak-fsw-<deployment-name>`) | TCP 127.0.0.1, port 50000 |
| `linux-remote` | Shipped over SSH to a Pi (user `fprime`, passwordless sudo) and run as a systemd service (`fprime-soak-fsw-<deployment-name>`) | TCP to `fsw-ip`, port 50000 |
| `pico2`        | Already built and flashed by fprime-ci; setup just waits for the serial device       | UART `/dev/pico2` @ 115200 |

## Flight-ops rotation (two-tier soak)

The rotation service runs
[`scripts/flight-ops-rotation.sh`](scripts/flight-ops-rotation.sh) (copied
into the install area). It cycles "normal flight operations" forever between
the cron-driven [`soak-test`](../soak-test/) runs: a no-op command, a
telemetry channel read, a `PRM_SAVE_FILE`, and - on the Linux platforms
only - an occasional small file downlink. Commands use the standard
`Svc/Subtopologies` instance names (`CdhCore.cmdDisp.*`,
`FileHandling.{prmDb,fileDownlink}.*`), identical across soak deployments,
sent with `fprime-cli` from the soak venv over the deployment's namespaced
ZMQ transport.

The same script has a one-shot `spike` mode that `soak-test` invokes for its
short high-intensity phase, so both tiers share a single command-sending
mechanism. The only coordination file is `$INSTALL_DIR/.cron-active`, created
by `soak-test` for the duration of a cron run; the rotation loop pauses while
it exists so cron-phase load is the only load.

## Artifact contract

`linux` / `linux-remote`: the calling workflow downloads the soak artifact to
`./artifacts/` before running the action. The action looks for:

```
artifacts/build-artifacts/<arch>/<deployment>/bin/<binary>
artifacts/build-artifacts/<arch>/<deployment>/dict/*TopologyDictionary.json
artifacts/int/                  integration tests
artifacts/fprime-gds.yml        deployment-owned GDS config (optional)
```

`pico2`: fprime-ci has already extracted `archive.tar.gz` to
`./build-artifacts/` and the target repository (with its `int/` tests) is
checked out at `./`.

Soak-specific GDS flags (`--no-app`, `--logs`, `--dictionary`, communication
and ZMQ selection) are appended on the systemd ExecStart line, so
`fprime-gds.yml` stays owned by the deployment.

## Inputs

| Input             | Default      | Description                                                        |
|-------------------|--------------|--------------------------------------------------------------------|
| `deployment-name` | *(required)* | Unique name; namespaces install dir, systemd services, ZMQ sockets. |
| `platform`        | *(required)* | `linux`, `linux-remote`, or `pico2`.                                |
| `fsw-ip`          | `""`         | Remote FSW Pi IP (`linux-remote` only).                             |

## Usage

```yaml
- uses: actions/download-artifact@v4
  with:
    name: my-soak-artifact
    path: artifacts
- uses: nasa/fprime-actions/soak-setup@devel
  with:
    deployment-name: my-deployment
    platform: linux-remote
    fsw-ip: "192.168.10.2"
```

> Meant to run once per soak (on release); the scheduled
> [`soak-test`](../soak-test/) action then runs against the services this
> leaves running, and [`soak-summary`](../soak-summary/) reports on demand.
