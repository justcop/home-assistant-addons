#!/bin/bash

USER=$(jq -r .ssh_user /data/options.json)
PASS=$(jq -r .ssh_password /data/options.json)

# Create user
useradd -m -s /bin/bash $USER
echo "$USER:$PASS" | chpasswd

# Setup SSH
mkdir -p /home/$USER/.ssh
chown -R $USER:$USER /home/$USER/.ssh

echo "PermitRootLogin no" >> /etc/ssh/sshd_config
echo "PasswordAuthentication yes" >> /etc/ssh/sshd_config

# 🔁 Fresh venv each container start
rm -rf /workspace/venv
python3 -m venv /workspace/venv

# Auto-activate venv on login
echo "source /workspace/venv/bin/activate" >> /home/$USER/.bashrc

# 🟢 Print Startup Banner to Home Assistant Logs
echo "========================================================"
echo "✅ Python Venv SSH Add-on is up and running!"
echo "👤 Authenticated User: $USER"
echo "🐍 Virtual Environment: /workspace/venv (Ready)"
echo "========================================================"

# Start SSH daemon in the foreground
/usr/sbin/sshd -D
