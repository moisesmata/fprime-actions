# FSW Pi Hardening Guide

This guide helps configure Pi 2 (FSW-only) to run a minimal, flight-like environment by disabling unnecessary services and background processes that wouldn't exist during spaceflight.

## Philosophy

Flight software typically runs on:
- Bare-metal or minimal RTOS (VxWorks, FreeRTOS)
- Embedded Linux with minimal userspace
- No background services beyond the FSW itself
- Deterministic, predictable resource usage

While we can't eliminate Linux entirely on the Pi, we can approximate a flight environment by:
1. Disabling all non-essential services
2. Minimizing kernel noise
3. Reducing logging overhead
4. Eliminating network services beyond what FSW needs
5. Removing package management and update daemons

---

## Phase 1: Identify Running Services

First, understand what's currently running:

```bash
# On Pi 2 (FSW Pi)
systemctl list-units --type=service --state=running

# See what's enabled at boot
systemctl list-unit-files --type=service --state=enabled

# Check resource usage
top -b -n 1 | head -20
ps aux | wc -l  # Count total processes
```

---

## Phase 2: Disable Non-Flight Services

### Services to Disable

**Network Services (keep minimal):**
```bash
# Disable NetworkManager - use static config only
sudo systemctl disable --now NetworkManager

# Disable mDNS/Avahi (service discovery)
sudo systemctl disable --now avahi-daemon.service
sudo systemctl disable --now avahi-daemon.socket

# Disable systemd-resolved (DNS not needed)
sudo systemctl disable --now systemd-resolved

# Disable ModemManager
sudo systemctl disable --now ModemManager
```

**Bluetooth & Wireless:**
```bash
# Disable Bluetooth
sudo systemctl disable --now bluetooth.service
sudo systemctl disable --now hciuart.service

# Disable WiFi (if using ethernet only)
sudo rfkill block wifi
# Make permanent
echo "rfkill block wifi" | sudo tee -a /etc/rc.local
```

**Audio Services:**
```bash
sudo systemctl disable --now alsa-restore.service
sudo systemctl disable --now alsa-state.service
```

**Package Management & Updates:**
```bash
# Disable automatic updates
sudo systemctl disable --now apt-daily.service
sudo systemctl disable --now apt-daily.timer
sudo systemctl disable --now apt-daily-upgrade.service
sudo systemctl disable --now apt-daily-upgrade.timer
sudo systemctl disable --now unattended-upgrades.service

# Disable snapd (if present)
sudo systemctl disable --now snapd.service
sudo systemctl disable --now snapd.socket
```

**Logging & Monitoring (reduce to minimum):**
```bash
# Disable rsyslog (systemd journal only)
sudo systemctl disable --now rsyslog.service

# Limit journal size and retention
sudo mkdir -p /etc/systemd/journald.conf.d
sudo tee /etc/systemd/journald.conf.d/flight.conf <<EOF
[Journal]
SystemMaxUse=50M
MaxRetentionSec=1day
MaxFileSec=1day
ForwardToSyslog=no
EOF
sudo systemctl restart systemd-journald
```

**Power Management:**
```bash
# Disable power management (not relevant in flight)
sudo systemctl disable --now systemd-logind
```

**Miscellaneous:**
```bash
# Disable GPU firmware (if not needed)
sudo systemctl disable --now rpi-eeprom-update.service

# Disable unnecessary timers
sudo systemctl disable --now systemd-tmpfiles-clean.timer
sudo systemctl disable --now man-db.timer
sudo systemctl disable --now fstrim.timer

# Disable crash reporting
sudo systemctl disable --now apport.service 2>/dev/null || true
```

---

## Phase 3: Kernel & Boot Optimization

### Disable Unnecessary Kernel Modules

Edit `/boot/firmware/config.txt` or `/boot/config.txt`:
```bash
sudo nano /boot/firmware/config.txt
```

Add/modify:
```ini
# Disable Bluetooth
dtoverlay=disable-bt

# Disable WiFi (if using ethernet only)
dtoverlay=disable-wifi

# Disable audio
dtparam=audio=off

# Disable camera
start_x=0

# Reduce GPU memory (FSW doesn't need GPU)
gpu_mem=16
```

### Kernel Boot Parameters

Edit `/boot/firmware/cmdline.txt` or `/boot/cmdline.txt`:
```bash
sudo nano /boot/firmware/cmdline.txt
```

Add these parameters (on the single line):
```
# Reduce kernel logging
quiet loglevel=3

# Disable IPv6 (if not needed)
ipv6.disable=1

# Disable unnecessary CPU features for determinism
nohz=off
```

### Disable Unnecessary Kernel Modules

Create `/etc/modprobe.d/flight-blacklist.conf`:
```bash
sudo tee /etc/modprobe.d/flight-blacklist.conf <<EOF
# Blacklist modules not needed in flight
blacklist bluetooth
blacklist btbcm
blacklist hci_uart
blacklist brcmfmac
blacklist brcmutil
blacklist snd_bcm2835
blacklist uvcvideo
EOF
```

---

## Phase 4: SSH Hardening

SSH is needed for deployment but wouldn't exist in flight. Harden it:

```bash
sudo nano /etc/ssh/sshd_config
```

Add/modify:
```
# Only allow specific user
AllowUsers fprime

# Disable password authentication (key only)
PasswordAuthentication no
ChallengeResponseAuthentication no

# Disable X11 forwarding
X11Forwarding no

# Disable TCP forwarding
AllowTcpForwarding no

# Disable agent forwarding
AllowAgentForwarding no

# Fast disconnect
ClientAliveInterval 60
ClientAliveCountMax 2

# Limit authentication attempts
MaxAuthTries 3
```

Restart SSH:
```bash
sudo systemctl restart ssh
```

---

## Phase 5: Filesystem Optimization

### Read-Only Root Filesystem (Advanced)

For true flight-like behavior, consider read-only root:

**Warning**: This is advanced and can make the system harder to manage. Only do this after testing.

```bash
# 1. Move logs to tmpfs
sudo nano /etc/fstab
```

Add:
```
tmpfs    /tmp         tmpfs    defaults,noatime,mode=1777    0 0
tmpfs    /var/log     tmpfs    defaults,noatime,mode=0755    0 0
tmpfs    /var/tmp     tmpfs    defaults,noatime,mode=1777    0 0
```

### Disable Swap (flight systems don't swap)

```bash
sudo swapoff -a
sudo systemctl disable dphys-swapfile 2>/dev/null || true
sudo apt-get remove -y dphys-swapfile 2>/dev/null || true
```

---

## Phase 6: Remove Unnecessary Packages

**Warning**: Be careful removing packages. Test after each removal.

```bash
# Remove GUI packages (if any)
sudo apt-get remove -y xserver-* x11-* desktop-* lightdm 2>/dev/null || true

# Remove documentation
sudo apt-get remove -y man-db manpages 2>/dev/null || true

# Clean up
sudo apt-get autoremove -y
sudo apt-get autoclean
```

---

## Phase 7: CPU & IRQ Isolation (Advanced)

For deterministic FSW behavior, isolate CPUs:

Edit `/boot/firmware/cmdline.txt`:
```
isolcpus=2,3 nohz_full=2,3 rcu_nocbs=2,3
```

This reserves CPUs 2-3 for FSW only. Then pin FSW to those CPUs in the systemd service:

Edit `/etc/systemd/system/fprime-soak-fsw.service`:
```ini
[Service]
CPUAffinity=2,3
Nice=-10
```

---

## Phase 8: Monitoring & Validation

### Measure Impact

Before and after hardening:

```bash
# Count running processes
ps aux | wc -l

# Count running services
systemctl list-units --type=service --state=running | wc -l

# Memory usage
free -h

# CPU idle percentage
top -b -n 1 | grep "Cpu(s)" | awk '{print $8}'

# Interrupts per second
watch -n 1 'cat /proc/interrupts'
```

### Create a Baseline Report

```bash
#!/bin/bash
# Save as check_baseline.sh on Pi 2

echo "=== System Baseline Report ==="
echo "Date: $(date)"
echo ""

echo "Processes: $(ps aux | wc -l)"
echo "Services running: $(systemctl list-units --type=service --state=running | wc -l)"
echo "Memory used: $(free -h | grep Mem | awk '{print $3}')"
echo "CPU idle: $(top -b -n 1 | grep "Cpu(s)" | awk '{print $8}')"
echo ""

echo "=== Running Services ==="
systemctl list-units --type=service --state=running --no-pager | grep running
echo ""

echo "=== Top CPU Consumers ==="
ps aux --sort=-%cpu | head -10
```

Run before and after hardening:
```bash
chmod +x check_baseline.sh
./check_baseline.sh > baseline_before.txt
# ... do hardening ...
sudo reboot
./check_baseline.sh > baseline_after.txt
diff baseline_before.txt baseline_after.txt
```

---

## Phase 9: Automated Hardening Script

Create a script to apply most changes:

```bash
#!/bin/bash
# Save as harden_fsw_pi.sh

set -e

echo "=== FSW Pi Hardening Script ==="
echo "This will make your Pi 2 more flight-like by disabling non-essential services."
echo ""
read -p "Continue? (yes/no): " confirm

if [ "$confirm" != "yes" ]; then
    echo "Aborted."
    exit 0
fi

echo ""
echo "[1/7] Disabling network services..."
sudo systemctl disable --now NetworkManager 2>/dev/null || true
sudo systemctl disable --now avahi-daemon.service 2>/dev/null || true
sudo systemctl disable --now avahi-daemon.socket 2>/dev/null || true
sudo systemctl disable --now systemd-resolved 2>/dev/null || true
sudo systemctl disable --now ModemManager 2>/dev/null || true

echo "[2/7] Disabling Bluetooth & WiFi..."
sudo systemctl disable --now bluetooth.service 2>/dev/null || true
sudo systemctl disable --now hciuart.service 2>/dev/null || true
sudo rfkill block bluetooth 2>/dev/null || true
sudo rfkill block wifi 2>/dev/null || true

echo "[3/7] Disabling package management timers..."
sudo systemctl disable --now apt-daily.service 2>/dev/null || true
sudo systemctl disable --now apt-daily.timer 2>/dev/null || true
sudo systemctl disable --now apt-daily-upgrade.service 2>/dev/null || true
sudo systemctl disable --now apt-daily-upgrade.timer 2>/dev/null || true

echo "[4/7] Configuring minimal logging..."
sudo mkdir -p /etc/systemd/journald.conf.d
sudo tee /etc/systemd/journald.conf.d/flight.conf > /dev/null <<EOF
[Journal]
SystemMaxUse=50M
MaxRetentionSec=1day
ForwardToSyslog=no
EOF
sudo systemctl restart systemd-journald

echo "[5/7] Disabling swap..."
sudo swapoff -a 2>/dev/null || true
sudo systemctl disable dphys-swapfile 2>/dev/null || true

echo "[6/7] Disabling unnecessary timers..."
sudo systemctl disable --now systemd-tmpfiles-clean.timer 2>/dev/null || true
sudo systemctl disable --now man-db.timer 2>/dev/null || true
sudo systemctl disable --now fstrim.timer 2>/dev/null || true

echo "[7/7] Creating kernel module blacklist..."
sudo tee /etc/modprobe.d/flight-blacklist.conf > /dev/null <<EOF
blacklist bluetooth
blacklist btbcm
blacklist hci_uart
blacklist brcmfmac
blacklist brcmutil
blacklist snd_bcm2835
EOF

echo ""
echo "=== Hardening Complete ==="
echo "Reboot required for all changes to take effect."
echo ""
read -p "Reboot now? (yes/no): " reboot_confirm

if [ "$reboot_confirm" = "yes" ]; then
    sudo reboot
else
    echo "Remember to reboot later: sudo reboot"
fi
```

Run it:
```bash
chmod +x harden_fsw_pi.sh
./harden_fsw_pi.sh
```

---

## What to Keep

**Essential for deployment:**
- SSH (for remote deployment)
- systemd (minimal init)
- Network stack (for FSW communication)

**Essential for FSW:**
- `fprime-soak-fsw.service` (the FSW itself)
- Basic kernel

**Optional but useful:**
- systemd-journald (minimal logging for debugging)
- systemd-timesyncd (if time sync is needed)

---

## Expected Results

After hardening, you should see:
- **Processes**: Drop from ~150-200 to ~50-80
- **Services**: Drop from ~50 to ~10-15
- **Memory usage**: Drop by 100-200MB
- **CPU idle**: Increase (less background noise)
- **Determinism**: More consistent FSW performance

---

## Reverting Changes

If something breaks:

```bash
# Re-enable a service
sudo systemctl enable --now <service-name>

# View what was disabled
systemctl list-unit-files --state=disabled

# Boot in rescue mode (from console)
# Add "systemd.unit=rescue.target" to kernel command line
```

---

## Further Steps: Container Isolation

For even more isolation, consider running FSW in a container:

```bash
# Install minimal container runtime
sudo apt-get install -y systemd-container

# Create container with only FSW dependencies
# (This is advanced - requires significant setup)
```

Or use `systemd-nspawn` for lightweight containerization without Docker overhead.

---

## Comparison: Flight vs. Ground Test

| Aspect | Real Flight | Hardened Pi | Standard Pi |
|--------|-------------|-------------|-------------|
| Init System | Minimal/None | systemd (minimal) | systemd (full) |
| Background Services | 0-5 | 10-15 | 50+ |
| SSH | No | Yes (for deploy) | Yes |
| Package Management | No | No (disabled) | Yes |
| Logging | Minimal/None | Minimal | Full |
| Network Services | FSW only | FSW + SSH | Many |
| Swap | No | No | Yes |
| GUI | No | No | Maybe |

---

## Validation Checklist

After hardening:

- [ ] FSW service starts correctly
- [ ] SSH deployment works
- [ ] Network communication to GDS works
- [ ] No unexpected background CPU usage
- [ ] Memory footprint is minimal
- [ ] Integration tests pass
- [ ] Soak test shows stable performance
- [ ] Process count is minimized
- [ ] Only essential services running

---

## Notes

1. **SSH Trade-off**: SSH wouldn't exist in flight, but we need it for deployment. Consider this when interpreting soak test results.

2. **Time Sync**: Flight systems often use GPS or spacecraft clock. The Pi uses NTP. Consider if you need `systemd-timesyncd` or can disable it.

3. **Watchdog**: Real flight systems have hardware watchdogs. Consider enabling the Pi's hardware watchdog for FSW.

4. **Logging**: Flight systems log to non-volatile storage or downlink. Minimal logging here is a compromise.

5. **Updates**: Never update packages during a soak test. Freeze the system state.
