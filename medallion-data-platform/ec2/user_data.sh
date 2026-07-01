#!/usr/bin/env bash
#
# EC2 user-data for the serving host (PostgreSQL + Apache Superset on Docker).
# Target AMI: Amazon Linux 2023 (dnf, x86_64 / t3.medium).
#
# CONTRACT — before this script runs, ServingStack (CDK) must have created:
#   /opt/serving/docker-compose.yml   (from ec2/docker-compose.yml)
#   /opt/serving/schema.sql           (from ec2/schema.sql)
#   /opt/serving/.env                 containing:
#       DB_NAME, DB_USER, DB_PASSWORD,
#       SUPERSET_SECRET_KEY, SUPERSET_ADMIN_USER, SUPERSET_ADMIN_PASSWORD
#
# This script only provisions the host (swap + Docker) and brings the stack up;
# Postgres applies schema.sql on first init and Superset self-initializes via its
# compose command. Re-running is safe.
set -euxo pipefail

APP_DIR=/opt/serving

# --- 2 GB swap (spike insurance during image pulls / Superset init) ---
if [ ! -f /swapfile ]; then
  dd if=/dev/zero of=/swapfile bs=1M count=2048
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

# --- Docker engine ---
dnf update -y
dnf install -y docker
systemctl enable --now docker
usermod -aG docker ec2-user

# --- Docker Compose v2 (CLI plugin) ---
COMPOSE_VERSION=v2.29.7
PLUGIN_DIR=/usr/local/lib/docker/cli-plugins
mkdir -p "$PLUGIN_DIR"
curl -fsSL \
  "https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-linux-x86_64" \
  -o "$PLUGIN_DIR/docker-compose"
chmod +x "$PLUGIN_DIR/docker-compose"

# --- bring up Postgres + Superset ---
cd "$APP_DIR"
docker compose pull
docker compose up -d

echo "serving stack started — Superset on :8088, Postgres on :5432"
