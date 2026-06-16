# nasa/fprime-actions/soak-setup

The `soak-setup` action installs a previously-built F´ deployment onto a
self-hosted runner:

The install tree (`$HOME/fprime-soak`) is owned by the runner user. 

Current support linux platforms by using systemd services. Uses `sudo` for systemd operations (`systemctl`, `journalctl`, and writing
unit files into `/etc/systemd/system/`).

More platforms will be added via adding more deploy scripts. 

The systemd unit files and the GDS config are rendered from templates in
[`templates/`](templates/) (`fsw.service`, `gds.service`, `fprime-gds.yml`)

## Artifact contract

The calling workflow uploads a soak artifact and the deploy job downloads it
into `./artifacts/`. The action looks for:

```
artifacts/build-artifacts/<arch>/<deployment>/bin/<binary>
artifacts/build-artifacts/<arch>/<deployment>/dict/*TopologyDictionary.json
artifacts/lib/fprime/requirements.txt
artifacts/int/                                
```

The `build-artifacts` and `lib/fprime/requirements.txt` paths are produced by
existing actions (`build-with-aarch64-toolchain`, `external-repository-setup`).
The calling workflow is responsible for staging `int/` and `requirements.txt` before upload.

## Inputs

| Input      | Default   | Description                                                                                                              |
|------------|-----------|--------------------------------------------------------------------------------------------------------------------------|
| `platform` | `linux`   | Selects the platform specific deploy script (`scripts/deploy_<platform>.sh`). Currently only `linux` |
| `gds-args` | `""`      | Extra arguments appended to `fprime-gds` ExecStart (e.g. `--framing-selection fprime-framing`). |


## Usage

```yaml
- uses: actions/download-artifact@v4
  with:
    name: my-soak-artifact
    path: artifacts
- uses: nasa/fprime-actions/soak-setup@devel
```

> For Linux: Requires a self-hosted runner with `sudo` (for `systemctl` only) and `systemd`.
> Meant to run once (setup); the periodic [`soak-test`](../soak-test/)
> action runs on a schedule against the services it leaves running.
