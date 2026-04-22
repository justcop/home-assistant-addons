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

# Start SSH
/usr/sbin/sshd -D
