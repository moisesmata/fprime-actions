# nasa/fprime-actions/soak-deploy

The `soak-deploy` action installs a previously-built F´ deployment onto a
self-hosted runner as two **persistent** `systemd` services:

* `fprime-soak-fsw` &mdash; the flight-software binary.
* `fprime-soak-gds` &mdash; a headless `fprime-gds` client that stays
  connected to the flight software for the entire soak.

The install tree (`$HOME/fprime-soak`) is owned by the runner user; the action
only uses `sudo` for systemd operations (`systemctl`, `journalctl`, and writing
unit files into `/etc/systemd/system/`).

The systemd unit files are rendered from readable templates
([`templates/fsw.service`](templates/fsw.service),
[`templates/gds.service`](templates/gds.service)). The GDS unit's `ExecStart`
is intentionally minimal: `fprime-gds` reads its arguments from
[`templates/fprime-gds.yml`](templates/fprime-gds.yml), which is rendered into
`$HOME/fprime-soak/fprime-gds.yml` and picked up because the service runs from
that directory as its working directory.

## Artifact contract

The calling workflow uploads a soak artifact and the deploy job downloads it
into `./artifacts/`. The action looks for:

```
artifacts/build-artifacts/<arch>/<deployment>/bin/<binary>
artifacts/build-artifacts/<arch>/<deployment>/dict/*TopologyDictionary.json
artifacts/lib/fprime/requirements.txt
artifacts/int/                                # integration tests (optional)
```

The `build-artifacts` and `lib/fprime/requirements.txt` paths are produced by
existing actions (`build-with-aarch64-toolchain`, `external-repository-setup`).
The calling workflow is responsible for staging `int/` from the
deployment-specific test directory before upload.

## Inputs

| Input      | Default | Description                                                                                                              |
|------------|---------|--------------------------------------------------------------------------------------------------------------------------|
| `gds-args` | `""`    | Extra arguments appended to `fprime-gds` ExecStart (e.g. `--framing-selection fprime-framing`). Most deployments leave empty. |

## Conventions (not outputs)

The action installs to fixed paths so the test workflow can reference them
directly without chaining outputs (which wouldn't cross job boundaries anyway):

| Path / Name           | Value                                          |
|-----------------------|------------------------------------------------|
| Install dir           | `$HOME/fprime-soak`                            |
| Virtualenv            | `$HOME/fprime-soak/venv`                       |
| Dictionary glob       | `$HOME/fprime-soak/dict/*TopologyDictionary.json` |
| ComLogger directory   | `$HOME/fprime-soak/ComLoggerFiles`             |
| FSW systemd service   | `fprime-soak-fsw`                              |
| GDS systemd service   | `fprime-soak-gds`                              |

## Usage

```yaml
- uses: actions/download-artifact@v4
  with:
    name: my-soak-artifact
    path: artifacts
- uses: nasa/fprime-actions/soak-deploy@devel
```

> Requires a self-hosted runner with `sudo` (for `systemctl` only) and `systemd`.
> Meant to run once (setup); the periodic [`soak-monitor`](../soak-monitor/)
> action runs on a schedule against the services it leaves running.
