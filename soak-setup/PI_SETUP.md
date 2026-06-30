# Two-Pi Soak Test Setup Guide

This guide walks you through configuring two Raspberry Pis for the remote FSW deployment architecture.

## Architecture Overview

- **Pi 1 (GDS Runner)**: Runs GitHub Actions runner, GDS, and integration tests
- **Pi 2 (FSW-Only)**: Runs flight software binary only
- **Connection**: Direct ethernet cable between the two Pis

## Prerequisites

- Two Raspberry Pis (both ARM64/AArch64)
- One ethernet cable
- Both Pis running a Linux distribution (Ubuntu, Raspberry Pi OS, etc.)
- Internet connectivity on Pi 1 (GDS Runner) via WiFi or separate ethernet

---

## Pi 1 Setup (GDS Runner)

### 1. Configure Static IP for Ethernet Interface

Use NetworkManager's text UI for easy configuration:

```bash
sudo nmtui
```

In the `nmtui` interface:
1. Select **"Edit a connection"**
2. Select your ethernet interface (e.g., `eth0`, `Wired connection 1`)
3. Press Enter to edit
4. Configure the following:
   - **IPv4 CONFIGURATION**: Change from `<Automatic>` to `<Manual>`
   - Select **"Show"** next to IPv4 CONFIGURATION
   - **Addresses**: Add `192.168.10.1/24` (adjust to match your chosen subnet)
   - **Gateway**: Leave empty (direct connection)
   - **DNS servers**: Leave empty or use your existing DNS
5. Select **"OK"** at the bottom
6. Select **"Back"**
7. Select **"Activate a connection"**
8. Deactivate and reactivate the connection

Verify the configuration:
```bash
ip addr show
# You should see 192.168.10.1 on your ethernet interface
```

**Example for this setup**: Use `192.168.10.1/24` for Pi 1

### 2. Install Dependencies

```bash
# Python and venv
sudo apt-get update
sudo apt-get install -y python3 python3-pip python3-venv

# SSH client (usually pre-installed)
sudo apt-get install -y openssh-client

# Systemd (usually pre-installed)
```

### 3. Generate SSH Key for FSW Pi Access

**CRITICAL**: Run this as the user that runs the GitHub Actions runner (e.g., `fprime` user on Pi 1):

```bash
# Switch to runner user if needed
# sudo su - fprime

# Generate key
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -C "gds-runner-to-fsw"

# Press Enter twice to skip passphrase (required for automation)
```

**Important**: The key must be generated as the user that runs the GitHub Actions runner, not as root or your personal user.

### 4. GitHub Actions Runner

If not already installed, follow GitHub's runner setup instructions for your repository.

---

## Pi 2 Setup (FSW-Only)

### 1. Create User Account

Create a user matching what you'll use in `fsw-host` workflow parameter. **This username must match exactly** (e.g., if workflow uses `fprime@192.168.10.2`, create user `fprime`):

```bash
# If user doesn't exist (replace 'fprime' with your chosen username)
sudo adduser fprime
sudo usermod -aG sudo fprime
```

**Important**: The username in `fsw-host: "username@ip"` must exist on Pi 2.

### 2. Configure Static IP for Ethernet Interface

Use NetworkManager's text UI for easy configuration:

```bash
sudo nmtui
```

In the `nmtui` interface:
1. Select **"Edit a connection"**
2. Select your ethernet interface (e.g., `eth0`, `Wired connection 1`)
3. Press Enter to edit
4. Configure the following:
   - **IPv4 CONFIGURATION**: Change from `<Automatic>` to `<Manual>`
   - Select **"Show"** next to IPv4 CONFIGURATION
   - **Addresses**: Add `192.168.10.2/24` (must be same subnet as Pi 1)
   - **Gateway**: Leave empty (direct connection)
   - **DNS servers**: Leave empty or use your existing DNS
5. Select **"OK"** at the bottom
6. Select **"Back"**
7. Select **"Activate a connection"**
8. Deactivate and reactivate the connection

Verify the configuration:
```bash
ip addr show
# You should see 192.168.10.2 on your ethernet interface
```

**Example for this setup**: Use `192.168.10.2/24` for Pi 2

### 3. Enable SSH

```bash
sudo systemctl enable ssh
sudo systemctl start ssh
```

### 4. Configure Passwordless Sudo (required for systemd service management)

```bash
sudo visudo
```

Add at the end (replace `fprime` with your actual username):
```
fprime ALL=(ALL) NOPASSWD: /bin/systemctl daemon-reload, /bin/systemctl enable, /bin/systemctl disable, /bin/systemctl start, /bin/systemctl stop, /bin/systemctl restart, /bin/systemctl status, /bin/systemctl is-active, /usr/bin/journalctl, /bin/mv /tmp/fprime-soak-fsw.service /etc/systemd/system/fprime-soak-fsw.service, /usr/bin/rm, /usr/bin/ss
```

**Important**: Also add `/usr/bin/rm` and `/usr/bin/ss` for the deployment script to work properly.

Or for full sudo access (less secure but simpler):
```
fprime ALL=(ALL) NOPASSWD: ALL
```

### 5. Install Dependencies

```bash
sudo apt-get update
sudo apt-get install -y systemd
```

### 6. Set Up SSH Key Authentication

From **Pi 1 (GDS Runner)**, as the runner user, copy the SSH key to Pi 2:

```bash
# From Pi 1, as the runner user (e.g., fprime)
ssh-copy-id -i ~/.ssh/id_ed25519 fprime@192.168.10.2
```

You'll be prompted for the Pi 2 user's password once. After this, test passwordless SSH:

```bash
# Should work without password
ssh fprime@192.168.10.2 "echo SSH works"
```

**Troubleshooting**: If you get "Permission denied", ensure:
1. You're running as the runner user on Pi 1
2. The username matches on both Pis
3. SSH service is running on Pi 2: `sudo systemctl status ssh`

### 7. Verify SSH Key Permissions

On **Pi 1**, ensure correct permissions:

```bash
chmod 700 ~/.ssh
chmod 600 ~/.ssh/id_ed25519
chmod 644 ~/.ssh/id_ed25519.pub
```

---

## Physical Connection

1. Connect the ethernet cable between the two Pis
2. Verify connectivity from Pi 1:

```bash
ping -c 4 192.168.10.2
```

If ping fails:
- Check cable connection
- Verify both interfaces are up: `ip link show eth0`
- Check IP configuration: `ip addr show eth0`
- Check firewall rules: `sudo iptables -L`

---

## GitHub Repository Setup

### 1. Update Workflow Configuration

In your workflow file (e.g., `ext-aarch64-linux-led-blinker-soak-setup.yml`), configure the deployment step:

```yaml
- name: "Deploy FSW + persistent GDS services"
  uses: moisesmata/fprime-actions/soak-setup@soak-actions-improvements
  with:
    platform: linux-remote
    fsw-host: "fprime@192.168.10.2"  # Use format: username@ip-address
    fsw-ip: "192.168.10.2"
    fsw-args: "-a 0.0.0.0 -p 50000"
    gds-args: "--communication-selection ip --ip-address 192.168.10.2 --ip-port 50000 --ip-client --zmq-transport ipc:///tmp/fprime-server-in ipc:///tmp/fprime-server-out"
```

**Key configuration points:**
- `platform: linux-remote` - Uses the two-Pi deployment script
- `fsw-host` - SSH target in format `username@ip-address` (must match the user on Pi 2)
- `fsw-ip` - IP address where FSW will listen
- `fsw-args: "-a 0.0.0.0 -p 50000"` - FSW binds to all interfaces on port 50000
- `gds-args` - Critical flags:
  - `--communication-selection ip` - Use TCP/IP to connect to remote FSW
  - `--ip-address <FSW_IP>` - Where FSW is running
  - `--ip-port 50000` - FSW port
  - `--ip-client` - GDS acts as client connecting to FSW server
  - `--zmq-transport ...` - Creates ZMQ sockets for integration tests

**Note**: Replace IP addresses with your actual network configuration (e.g., `192.168.10.1` and `192.168.10.2` or `192.168.100.1` and `192.168.100.2`).

### 2. Verify Runner Labels

Ensure your self-hosted runner on Pi 1 has the correct labels:
- `self-hosted`
- `ARM64`
- `soak-test`

Check in: Repository Settings → Actions → Runners

---

## Testing the Setup

### 1. Test Network Connectivity

From Pi 1 (as the runner user):
```bash
# Ping test
ping -c 4 192.168.10.2

# SSH test (use your actual username)
ssh fprime@192.168.10.2 "echo SSH connection successful"
```

### 2. Test Sudo Commands

From Pi 1:
```bash
ssh fprime@192.168.10.2 "sudo systemctl daemon-reload && echo Sudo works"
```

### 3. Test File Transfer

From Pi 1:
```bash
echo "test" > /tmp/test.txt
scp /tmp/test.txt fprime@192.168.10.2:/tmp/
ssh fprime@192.168.10.2 "cat /tmp/test.txt"
```

### 4. Test Port Connectivity

From Pi 1:
```bash
# Install netcat if needed
sudo apt-get install -y netcat-openbsd

# Test if port 50000 is reachable
nc -zv 192.168.10.2 50000
```

### 5. Verify ZMQ Socket Creation

After running the workflow setup, verify the GDS created ZMQ sockets on Pi 1:

```bash
# On Pi 1
ls -la /tmp/fprime-server-*
# Should show: fprime-server-in and fprime-server-out

# Check GDS is running
sudo systemctl status fprime-soak-gds

# Check GDS logs
sudo journalctl -u fprime-soak-gds -n 50 --no-pager
```

### 6. Verify FSW is Running on Pi 2

From Pi 1:
```bash
# Check FSW service status
ssh fprime@192.168.10.2 "sudo systemctl status fprime-soak-fsw"

# Check FSW is listening on port 50000
ssh fprime@192.168.10.2 "sudo ss -tulpn | grep 50000"
# Should show the FSW binary listening on 0.0.0.0:50000 or 192.168.10.2:50000
```

---

## Troubleshooting

### SSH Connection Refused
```bash
# On Pi 2
sudo systemctl status ssh
sudo systemctl restart ssh
```

### Permission Denied on systemctl
- Check sudo configuration with `sudo -l`
- Verify the user has sudo access: `groups pi`

### Network Unreachable
- Check both Pis have correct static IPs: `ip addr`
- Check routing: `ip route`
- Check if interface is up: `sudo ip link set eth0 up`

### FSW Binary Not Executing
- Check permissions: `ssh fprime@192.168.10.2 "ls -l /home/fprime/fprime-soak/bin/fsw"`
- Check it's the correct architecture: `ssh fprime@192.168.10.2 "file /home/fprime/fprime-soak/bin/fsw"`
- Check FSW logs: `ssh fprime@192.168.10.2 "sudo journalctl -u fprime-soak-fsw -n 50"`

### Integration Tests Timeout
If integration tests can't send commands:
- Verify ZMQ sockets exist: `ls -la /tmp/fprime-server-*` on Pi 1
- Check GDS args include: `--zmq-transport ipc:///tmp/fprime-server-in ipc:///tmp/fprime-server-out`
- Verify GDS service file: `cat /etc/systemd/system/fprime-soak-gds.service`
- Check GDS is connected to FSW: `sudo journalctl -u fprime-soak-gds -n 100 | grep -i connect`

### GDS Not Connecting to Remote FSW
- Verify FSW is listening: `ssh fprime@192.168.10.2 "sudo ss -tulpn | grep 50000"`
- Test port connectivity: `nc -zv 192.168.10.2 50000`
- Check GDS logs for connection errors: `sudo journalctl -u fprime-soak-gds -n 100`
- Verify GDS args include: `--communication-selection ip --ip-address 192.168.10.2 --ip-port 50000 --ip-client`

### Firewall Issues
```bash
# Temporarily disable firewall for testing (Ubuntu)
sudo ufw disable

# Or open specific port
sudo ufw allow 50000
```

---

## Security Considerations

1. **Network Isolation**: The direct ethernet connection creates an isolated network. No internet traffic can reach Pi 2 unless you configure routing.

2. **SSH Key Protection**: Keep the private key (`~/.ssh/id_ed25519`) on Pi 1 secure. It should only be readable by the runner user:
   ```bash
   chmod 600 ~/.ssh/id_ed25519
   ```

3. **Limited Sudo**: The recommended sudo configuration only allows specific systemctl commands. This is more secure than `NOPASSWD: ALL`.

4. **No Password SSH**: Password authentication can remain enabled on Pi 2 for manual access, but the workflow only uses key-based auth.

---

## Alternative: Using a Switch/Router

If you prefer not to use a direct cable, you can connect both Pis to a dedicated switch or router:

1. Connect both Pis to the switch via ethernet
2. Configure static IPs on the same subnet (e.g., 192.168.10.1 and 192.168.10.2)
3. Add a gateway if needed in nmtui (optional)
4. The rest of the setup remains identical

This approach allows easier expansion if you want to add more FSW Pis in the future.

---

## Summary of Working Configuration

Based on successful deployment, here's what works:

**Network:**
- Pi 1 (GDS): `192.168.10.1/24`
- Pi 2 (FSW): `192.168.10.2/24`
- Direct ethernet connection (no gateway needed)

**Users:**
- Same username on both Pis (e.g., `fprime`)
- SSH key authentication with `~/.ssh/id_ed25519`

**Workflow Configuration:**
```yaml
platform: linux-remote
fsw-host: "fprime@192.168.10.2"
fsw-ip: "192.168.10.2"
fsw-args: "-a 0.0.0.0 -p 50000"
gds-args: "--communication-selection ip --ip-address 192.168.10.2 --ip-port 50000 --ip-client --zmq-transport ipc:///tmp/fprime-server-in ipc:///tmp/fprime-server-out"
```

**Services:**
- Pi 1: `fprime-soak-gds.service` (GDS with ZMQ sockets + TCP/IP client to FSW)
- Pi 2: `fprime-soak-fsw.service` (FSW binary listening on 0.0.0.0:50000)

**Integration Tests:**
- Connect to GDS via ZMQ: `ipc:///tmp/fprime-server-in` and `ipc:///tmp/fprime-server-out`
- GDS forwards commands to FSW at `192.168.10.2:50000`
