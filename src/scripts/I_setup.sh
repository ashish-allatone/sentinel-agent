set -euo pipefail

# Settings (matches what the agent expects) ---------------------------------
MQTT_USER="my_mqtt_user"
MQTT_PASS="mqttpassword"

cd "$(dirname "$(readlink -f "$0")")"

log() { printf ">> %s\n" "$*"; }
ok()  { printf "[OK] %s\n" "$*"; }
die() { printf "[ERROR] %s\n" "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "Run with sudo."

# ---------------------------------------------------------------------------
# 1. Docker
# ---------------------------------------------------------------------------
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    ok "Docker is already installed."
elif [[ -f /etc/os-release ]] && grep -qE '^ID="?(ol|rhel|rocky|almalinux|centos)' /etc/os-release; then
    log "Detected RHEL family - installing Docker from the docker-ce repo..."
    dnf install -y dnf-utils
    dnf config-manager --add-repo=https://download.docker.com/linux/centos/docker-ce.repo
    dnf install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
    systemctl enable --now docker
    ok "Docker installed."
else
    log "Installing Docker via get.docker.com (works on most distros)..."
    curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
    sh /tmp/get-docker.sh
    systemctl enable --now docker || true
    ok "Docker installed."
fi

# ---------------------------------------------------------------------------
# 2. Mosquitto password file
# ---------------------------------------------------------------------------
mkdir -p mosquitto/config
if [[ -f mosquitto/config/passwd ]]; then
    ok "Mosquitto password file already exists."
else
    log "Generating Mosquitto password file (via the official image)..."
    docker run --rm \
        -v "$(pwd)/mosquitto/config:/mosquitto/config" \
        eclipse-mosquitto:2 \
        mosquitto_passwd -b -c /mosquitto/config/passwd "$MQTT_USER" "$MQTT_PASS"
    chmod 600 mosquitto/config/passwd
    ok "Mosquitto password file created."
fi

# ---------------------------------------------------------------------------
# 3. Host firewall — allow 5432 (Postgres) and 1883 (MQTT) inbound on TCP
# ---------------------------------------------------------------------------
open_port() {
    local p=$1
    if command -v ufw >/dev/null 2>&1; then
        ufw allow "${p}/tcp" >/dev/null && ok "ufw: allowed ${p}/tcp"
    elif command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; then
        firewall-cmd --permanent --add-port="${p}/tcp" >/dev/null
        firewall-cmd --reload >/dev/null
        ok "firewalld: allowed ${p}/tcp"
    elif command -v iptables >/dev/null 2>&1; then
        iptables -C INPUT -p tcp --dport "$p" -j ACCEPT 2>/dev/null \
            || iptables -I INPUT -p tcp --dport "$p" -j ACCEPT
        ok "iptables: allowed ${p}/tcp (note: not persistent across reboot unless saved)"
    else
        log "No firewall tool found — assuming no host firewall is active."
    fi
}
open_port 5433
open_port 1884

# ---------------------------------------------------------------------------
# 4. Bring up the stack
# ---------------------------------------------------------------------------
log "Starting containers..."
docker compose up -d
docker compose ps
# Wait for Postgres to be healthy, then confirm the extension is in.
log "Waiting for Postgres to be ready..."
for i in $(seq 1 30); do
    if docker exec sentinel-postgres pg_isready -U developer -d developmentdb >/dev/null 2>&1; then
        break
    fi
    sleep 1
done
 
log "Verifying TimescaleDB extension..."
TSDB_VERSION=$(docker exec sentinel-postgres psql -U developer -d developmentdb -tAc \
    "SELECT extversion FROM pg_extension WHERE extname='timescaledb';" 2>/dev/null || true)
if [[ -n "$TSDB_VERSION" ]]; then
    ok "TimescaleDB $TSDB_VERSION is installed in developmentdb."
else
    log "WARNING: TimescaleDB extension not detected. Check: docker logs sentinel-postgres"
fi
# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
HOST_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
[[ -z "$HOST_IP" ]] && HOST_IP="localhost"

cat <<EOF

------------------------------------------------------------------
 Stack is up.

 PostgreSQL  (container: sentinel-postgres)
   host       $HOST_IP
   port       5433
   user       developer
   password   password
   database   developmentdb

 Mosquitto MQTT  (container: sentinel-mosquitto)
   host       $HOST_IP
   port       1884
   user       $MQTT_USER
   password   $MQTT_PASS
   topic      agent/events_test  (client-side, broker doesn't enforce)

 Connect from inside the host:
   docker exec -it sentinel-postgres psql -U developer -d developmentdb
   docker logs -f sentinel-mosquitto

 Connect from another machine:
   psql "postgresql://developer:password@$HOST_IP:5433/developmentdb"

 IMPORTANT - cloud firewalls
 If this host is on AWS / Azure / GCP / OCI, the host firewall is only
 one layer. You also need to open 5432 and 1883 in the cloud-side
 security group / network rules, or external connections will still be
 blocked at the network edge.
------------------------------------------------------------------
EOF
