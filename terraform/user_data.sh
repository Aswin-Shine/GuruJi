#!/usr/bin/env bash
# Bootstrap for Amazon Linux 2023. Runs once, as root, on first boot.
#
# Deliberately does NOT clone the repository or start the stack. Both need a
# populated .env holding an OpenAI key and generated secrets, and baking secrets
# into user_data would put them in the instance metadata service and in the
# Terraform state file in plaintext. Deployment stays a manual step; this script
# only prepares the machine to receive it.

set -euxo pipefail

dnf update -y
dnf install -y docker git

# The Compose v2 plugin is not in the AL2023 repositories. It is a single static
# binary installed into Docker's plugin directory.
COMPOSE_VERSION="v2.39.1"
ARCH="$(uname -m)"
install -d /usr/local/lib/docker/cli-plugins
curl -fsSL \
  "https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-linux-${ARCH}" \
  -o /usr/local/lib/docker/cli-plugins/docker-compose
chmod +x /usr/local/lib/docker/cli-plugins/docker-compose

systemctl enable --now docker
usermod -aG docker ec2-user

# Journald keeps container logs bounded. Without a cap, a chatty container can
# fill the root volume, and a full disk takes Postgres down with it.
mkdir -p /etc/docker
cat > /etc/docker/daemon.json <<'JSON'
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "10m", "max-file": "3" }
}
JSON
systemctl restart docker

docker --version
docker compose version
