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
   - **Addresses**: Add `192.168.100.1/24` (or `192.168.10.1/24` to match your setup)
   - **Gateway**: Leave empty (direct connection)
   - **DNS servers**: Leave empty or use your existing DNS
5. Select **"OK"** at the bottom
6. Select **"Back"**
7. Select **"Activate a connection"**
8. Deactivate and reactivate the connection

Verify the configuration:
```bash
ip addr show
# You should see 192.168.100.1 on your ethernet interface
```

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

```bash
# Generate key as the runner user
ssh-keygen -t ed25519 -f ~/.ssh/id_fsw_pi -C "gds-runner-to-fsw"

# Leave passphrase empty (required for automation)
```

**Important**: The key must be generated as the user that runs the GitHub Actions runner.

### 4. GitHub Actions Runner

If not already installed, follow GitHub's runner setup instructions for your repository.

---

## Pi 2 Setup (FSW-Only)

### 1. Create User Account

Create a user matching what you'll use in `FSW_PI_HOST` (e.g., `pi`):

```bash
# If user doesn't exist
sudo adduser pi
sudo usermod -aG sudo pi
```

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
   - **Addresses**: Add `192.168.100.2/24` (or `192.168.10.2/24` to match your setup)
   - **Gateway**: Leave empty (direct connection)
   - **DNS servers**: Leave empty or use your existing DNS
5. Select **"OK"** at the bottom
6. Select **"Back"**
7. Select **"Activate a connection"**
8. Deactivate and reactivate the connection

Verify the configuration:
```bash
ip addr show
# You should see 192.168.100.2 on your ethernet interface
```

### 3. Enable SSH

```bash
sudo systemctl enable ssh
sudo systemctl start ssh
```

### 4. Configure Passwordless Sudo (required for systemd service management)

```bash
sudo visudo
```

Add at the end (replace `pi` with your username):
```
pi ALL=(ALL) NOPASSWD: /bin/systemctl daemon-reload, /bin/systemctl enable, /bin/systemctl disable, /bin/systemctl start, /bin/systemctl stop, /bin/systemctl restart, /bin/systemctl status, /bin/systemctl is-active, /usr/bin/journalctl, /bin/mv /tmp/fprime-soak-fsw.service /etc/systemd/system/fprime-soak-fsw.service
```

Or for full sudo access (less secure):
```
pi ALL=(ALL) NOPASSWD: ALL
```

### 5. Install Dependencies

```bash
sudo apt-get update
sudo apt-get install -y systemd
```

### 6. Set Up SSH Key Authentication

From **Pi 1 (GDS Runner)**, copy the SSH key:

```bash
# From Pi 1
ssh-copy-id -i ~/.ssh/id_fsw_pi pi@192.168.100.2
```

You'll be prompted for the password once. After this, test passwordless SSH:

```bash
ssh -i ~/.ssh/id_fsw_pi pi@192.168.100.2 "echo SSH works"
```

### 7. Configure SSH on Pi 1 for Automatic Key Usage

On **Pi 1**, create/edit SSH config:

```bash
nano ~/.ssh/config
```

Add:
```
Host fsw-pi
    HostName 192.168.100.2
    User pi
    IdentityFile ~/.ssh/id_fsw_pi
    StrictHostKeyChecking no
```

Test with the alias:
```bash
ssh fsw-pi "echo Works with alias"
```

---

## Physical Connection

1. Connect the ethernet cable between the two Pis
2. Verify connectivity from Pi 1:

```bash
ping -c 4 192.168.100.2
```

If ping fails:
- Check cable connection
- Verify both interfaces are up: `ip link show eth0`
- Check IP configuration: `ip addr show eth0`
- Check firewall rules: `sudo iptables -L`

---

## GitHub Repository Setup

### 1. Add GitHub Secrets

In your fprime repository (Settings → Secrets and variables → Actions):

- **Name**: `FSW_PI_HOST`
  - **Value**: `pi@192.168.100.2` (or use the alias: `fsw-pi`)

- **Name**: `FSW_PI_IP`
  - **Value**: `192.168.100.2`

### 2. Verify Runner Labels

Ensure your self-hosted runner on Pi 1 has the correct labels:
- `self-hosted`
- `ARM64`
- `soak-test`

Check in: Repository Settings → Actions → Runners

---

## Testing the Setup

### 1. Test Network Connectivity

From Pi 1:
```bash
# Ping test
ping -c 4 192.168.100.2

# SSH test
ssh pi@192.168.100.2 "echo SSH connection successful"
```

### 2. Test Sudo Commands

From Pi 1:
```bash
ssh pi@192.168.100.2 "sudo systemctl daemon-reload && echo Sudo works"
```

### 3. Test File Transfer

From Pi 1:
```bash
echo "test" > /tmp/test.txt
scp /tmp/test.txt pi@192.168.100.2:/tmp/
ssh pi@192.168.100.2 "cat /tmp/test.txt"
```

### 4. Test Port Connectivity

From Pi 1:
```bash
# Install netcat if needed
sudo apt-get install -y netcat

# On Pi 2, open a test port
ssh pi@192.168.100.2 "nc -l 50000 &"

# On Pi 1, connect to it
nc -zv 192.168.100.2 50000
```

### 5. Manual Deployment Test

Create a minimal test binary on Pi 1:
```bash
echo '#!/bin/bash' > /tmp/test_fsw
echo 'echo "FSW running on $(hostname)"' >> /tmp/test_fsw
echo 'sleep 10' >> /tmp/test_fsw
chmod +x /tmp/test_fsw

# Copy to Pi 2
ssh pi@192.168.100.2 "mkdir -p /home/pi/fprime-soak/bin"
scp /tmp/test_fsw pi@192.168.100.2:/home/pi/fprime-soak/bin/fsw

# Run it remotely
ssh pi@192.168.100.2 "/home/pi/fprime-soak/bin/fsw"
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
- Check permissions: `ls -l /home/pi/fprime-soak/bin/fsw`
- Check it's the correct architecture: `file /home/pi/fprime-soak/bin/fsw`
- Try running manually: `ssh pi@192.168.100.2 "/home/pi/fprime-soak/bin/fsw -h"`

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

2. **SSH Key Protection**: Keep the private key (`~/.ssh/id_fsw_pi`) on Pi 1 secure. It should only be readable by the runner user:
   ```bash
   chmod 600 ~/.ssh/id_fsw_pi
   ```

3. **Limited Sudo**: The recommended sudo configuration only allows specific systemctl commands. This is more secure than `NOPASSWD: ALL`.

4. **No Password SSH**: Password authentication can remain enabled on Pi 2 for manual access, but the workflow only uses key-based auth.

---

## Alternative: Using a Switch/Router

If you prefer not to use a direct cable, you can connect both Pis to a dedicated switch or router:

1. Connect both Pis to the switch via ethernet
2. Configure static IPs on the same subnet (e.g., 192.168.100.1 and 192.168.100.2)
3. Add a gateway if needed: `gateway4: 192.168.100.254`
4. The rest of the setup remains identical

This approach allows easier expansion if you want to add more FSW Pis in the future.
