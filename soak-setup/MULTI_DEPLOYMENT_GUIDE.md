# Running Multiple Soak Tests on One Runner

This guide explains how to run multiple F´ deployments simultaneously on a single self-hosted runner using deployment namespacing.

## Overview

With deployment namespacing, you can run multiple soak tests concurrently on the same Raspberry Pi runner. Each deployment gets isolated:

- **Install directory**: `~/fprime-soak-<deployment-name>`
- **GDS service**: `fprime-soak-gds-<deployment-name>.service`
- **FSW service** (remote): `fprime-soak-fsw-<deployment-name>.service`
- **ZMQ sockets**: `/tmp/fprime-server-<deployment-name>-in` and `-out`
- **Logs**: Separate directories per deployment

## Example: LedBlinker + Pico 2 on One Pi

### Runner Configuration

Your single Pi runner needs **all** the labels for deployments it will run:

```bash
# In GitHub Settings → Actions → Runners
# Add multiple labels:
- self-hosted
- ARM64
- soak-test       # For LedBlinker (Linux remote)
- pico2-soak      # For Pico 2 (Zephyr)
```

### LedBlinker Setup (Linux Remote)

**Workflow**: `ext-aarch64-linux-led-blinker-soak-setup.yml`

```yaml
- name: "Deploy FSW + persistent GDS services"
  uses: moisesmata/fprime-actions/soak-setup@soak-actions-improvements
  with:
    deployment-name: ledblinker  # Unique name
    platform: linux-remote
    fsw-host: "fprime@192.168.10.2"
    fsw-ip: "192.168.10.2"
    fsw-args: "-a 0.0.0.0 -p 50000"
    gds-args: "--communication-selection ip --ip-address 192.168.10.2 --ip-port 50000 --ip-client --zmq-transport ipc:///tmp/fprime-server-ledblinker-in ipc:///tmp/fprime-server-ledblinker-out"
```

**Test workflow**: `ext-aarch64-linux-led-blinker-soak-test.yml`

```yaml
- uses: moisesmata/fprime-actions/soak-test@soak-actions-improvements
  with:
    deployment-name: ledblinker
```

**Resources created**:
- Directory: `~/fprime-soak-ledblinker/`
- GDS service: `fprime-soak-gds-ledblinker.service` (on Pi 1)
- FSW service: `fprime-soak-fsw-ledblinker.service` (on Pi 2)
- ZMQ sockets: `/tmp/fprime-server-ledblinker-{in,out}`

### Pico 2 Setup (Zephyr)

**Workflow**: `ext-pico2-zephyr-reference-soak-setup.yml`

```yaml
- name: "Deploy FSW + persistent GDS services"
  uses: moisesmata/fprime-actions/soak-setup@soak-actions-improvements
  with:
    deployment-name: pico2-ref  # Different unique name
    platform: pico2
    fsw-device: /dev/pico2
    fsw-hex: artifacts/build-artifacts/zephyr.hex
    gds-args: "--communication-selection uart --uart-device /dev/pico2 --uart-baud 115200 --uart-skip-port-check --zmq-transport ipc:///tmp/fprime-server-pico2-in ipc:///tmp/fprime-server-pico2-out"
```

**Test workflow**: `ext-pico2-zephyr-reference-soak-test.yml`

```yaml
- uses: moisesmata/fprime-actions/soak-test@soak-actions-improvements
  with:
    deployment-name: pico2-ref
```

**Resources created**:
- Directory: `~/fprime-soak-pico2-ref/`
- GDS service: `fprime-soak-gds-pico2-ref.service` (on Pi 1)
- FSW: Runs on Pico 2 hardware (no service)
- ZMQ sockets: `/tmp/fprime-server-pico2-{in,out}`

## No Conflicts!

With namespacing, both deployments coexist:

```
Pi Runner (Pi 1):
├── ~/fprime-soak-ledblinker/
│   ├── bin/fsw
│   ├── dict/
│   ├── gds-logs/
│   ├── test/
│   └── venv/
├── ~/fprime-soak-pico2-ref/
│   ├── bin/zephyr.hex
│   ├── dict/
│   ├── gds-logs/
│   ├── test/
│   └── venv/
├── /tmp/fprime-server-ledblinker-{in,out}  # ZMQ for LedBlinker
└── /tmp/fprime-server-pico2-{in,out}       # ZMQ for Pico 2

Services:
├── fprime-soak-gds-ledblinker.service   # GDS for LedBlinker
└── fprime-soak-gds-pico2-ref.service    # GDS for Pico 2

Pi 2 (FSW for LedBlinker):
└── fprime-soak-fsw-ledblinker.service   # FSW service

Pico 2 (FSW for Zephyr):
└── (bare-metal, no service)             # FSW runs directly
```

## Monitoring Both Deployments

### View All Services

```bash
# List all soak services
sudo systemctl list-units 'fprime-soak-*'

# Output:
# fprime-soak-gds-ledblinker.service    loaded active running F' soak-test GDS
# fprime-soak-gds-pico2-ref.service     loaded active running F' soak-test GDS for Pico 2
```

### Check Specific Deployment

```bash
# LedBlinker status
sudo systemctl status fprime-soak-gds-ledblinker
ls -la /tmp/fprime-server-ledblinker-*
ls ~/fprime-soak-ledblinker/

# Pico 2 status
sudo systemctl status fprime-soak-gds-pico2-ref
ls -la /tmp/fprime-server-pico2-*
ls ~/fprime-soak-pico2-ref/
```

### View Logs

```bash
# LedBlinker GDS logs
sudo journalctl -u fprime-soak-gds-ledblinker -n 50

# Pico 2 GDS logs
sudo journalctl -u fprime-soak-gds-pico2-ref -n 50

# LedBlinker soak database
cat ~/fprime-soak-ledblinker/soak-database.log

# Pico 2 soak database
cat ~/fprime-soak-pico2-ref/soak-database.log
```

## Scheduling

Both soak tests run on their own schedules:

- **LedBlinker**: Every 30 min (defined in its test workflow)
- **Pico 2**: Every 30 min (defined in its test workflow)

GitHub Actions ensures they don't conflict because:
1. Each uses a different concurrency group (`ledblinker-soak` vs `pico2-soak`)
2. Each accesses different namespaced resources
3. The runner can handle both simultaneously

## Adding More Deployments

To add a third deployment (e.g., `teensy41-ref`):

1. **Choose a unique deployment name**: `teensy41-ref`

2. **Update the setup workflow**:
   ```yaml
   with:
     deployment-name: teensy41-ref
     platform: teensy41
     gds-args: "... --zmq-transport ipc:///tmp/fprime-server-teensy41-in ipc:///tmp/fprime-server-teensy41-out"
   ```

3. **Update the test workflow**:
   ```yaml
   with:
     deployment-name: teensy41-ref
   ```

4. **Add runner label** (if needed): `teensy41-soak`

That's it! The namespacing handles the rest.

## Best Practices

### Deployment Naming

- Use lowercase with hyphens: `my-deployment`
- Keep it short but descriptive: `ledblinker`, `pico2-ref`, `teensy41-ref`
- Avoid special characters beyond hyphens
- Must be filesystem-safe (used in directory names)

### ZMQ Socket Naming

Always namespace ZMQ sockets in `gds-args`:
```yaml
--zmq-transport ipc:///tmp/fprime-server-<deployment-name>-in ipc:///tmp/fprime-server-<deployment-name>-out
```

**Bad** (conflicts):
```yaml
--zmq-transport ipc:///tmp/fprime-server-in ipc:///tmp/fprime-server-out
```

**Good** (namespaced):
```yaml
--zmq-transport ipc:///tmp/fprime-server-ledblinker-in ipc:///tmp/fprime-server-ledblinker-out
```

### Concurrency Groups

Keep workflow concurrency groups unique per deployment:

```yaml
concurrency:
  group: ledblinker-soak     # Unique per deployment
  cancel-in-progress: false  # Let tests complete
```

Don't use generic names like `soak` - deployments would cancel each other.

### Runner Labels

For clarity, give each deployment a unique label:
- `soak-test` → LedBlinker (or generic deployments)
- `pico2-soak` → Pico 2 deployments
- `teensy41-soak` → Teensy 4.1 deployments

Or use a single label if you want all deployments on the same runner:
- `multi-soak` → all deployments

## Resource Considerations

### CPU & Memory

Each GDS service consumes:
- ~50-100 MB RAM
- ~1-5% CPU (idle)
- ~10-20% CPU (during active testing)

**Recommendation**: Raspberry Pi 4/5 with 4GB+ RAM can handle 3-5 deployments comfortably.

### Disk Space

Each deployment uses:
- ~200-500 MB for installation
- ~10-50 MB per day for logs (depending on telemetry rate)

**Recommendation**: 32GB+ SD card or SSD for long-term soak testing.

### Network/Serial Ports

- **Linux remote** deployments need distinct remote Pis (different IPs)
- **Pico 2** deployments need distinct serial devices (e.g., `/dev/pico2`, `/dev/pico2-2`)

You can run multiple Pico 2s by:
1. Creating udev rules for each: `/dev/pico2`, `/dev/pico2-second`, etc.
2. Passing different `fsw-device` per deployment

## Troubleshooting

### Service Name Already Exists

**Error**: `fprime-soak-gds.service already exists`

**Cause**: Deployment name not provided or duplicated.

**Fix**: Ensure every setup workflow has a unique `deployment-name`.

### ZMQ Socket Conflicts

**Error**: Integration tests timeout / "Address already in use"

**Cause**: Two deployments using the same ZMQ socket paths.

**Fix**: Namespace sockets in `gds-args`:
```yaml
--zmq-transport ipc:///tmp/fprime-server-<unique-name>-in ipc:///tmp/fprime-server-<unique-name>-out
```

### Directory Conflicts

**Error**: Files from wrong deployment in test directory

**Cause**: `DEPLOYMENT_NAME` not set or incorrect.

**Fix**: Verify `deployment-name` matches in both setup and test workflows.

### Viewing All Active Deployments

```bash
# List all soak directories
ls -d ~/fprime-soak-*/

# List all soak services
sudo systemctl list-units 'fprime-soak-*' --all

# List all ZMQ sockets
ls -la /tmp/fprime-server-*
```

## Example: Clean Slate

To remove all deployments and start fresh:

```bash
# Stop all GDS services
sudo systemctl disable --now fprime-soak-gds-*

# Remove service files
sudo rm /etc/systemd/system/fprime-soak-gds-*.service
sudo systemctl daemon-reload

# Remove installation directories
rm -rf ~/fprime-soak-*

# Remove ZMQ sockets
rm -f /tmp/fprime-server-*
```

Then re-run your setup workflows.

---

## Summary

**Key Requirement**: Set `deployment-name` to a unique value in every workflow.

**Benefits**:
- ✅ Run multiple deployments on one runner
- ✅ No resource conflicts
- ✅ Independent scheduling and testing
- ✅ Isolated logs and telemetry
- ✅ Easy to add/remove deployments

**One Pi, many deployments!**
