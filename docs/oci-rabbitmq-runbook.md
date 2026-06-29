---
layout: default
title: How to install rabbitmq on Oracle Cloud Infrastructure
parent: ORI Sync Tools
nav_order: 2.6
---

# RabbitMQ on OCI Always Free (US West / San Jose) \- Build Runbook

A step-by-step plan to stand up a single RabbitMQ broker in Oracle Cloud Infrastructure, kept inside the Always Free compute tier, in the region closest to Los Angeles.

**Locked decisions for this plan**

- Account: brand new tenancy, home region set to **US West (San Jose), `us-sanjose-1`**  
- Provisioning: **manual** (Console for build, CLI/SSH for on-host work)  
- Runtime: **RabbitMQ in a container** (Docker or Podman) on an Arm node

---

## 0\. Why these choices (the constraints that matter)

1. **Free-tier shape.** The Always Free allocation that can actually run a broker is the Arm shape `VM.Standard.A1.Flex`: up to 4 OCPUs and 24 GB of RAM for an Always Free tenancy, allocatable across one to four instances. The two AMD `VM.Standard.E2.1.Micro` instances are only 1 GB RAM each and are too small for RabbitMQ. We will use one A1 node.  
2. **Region nearest LA.** OCI has two US West regions: Phoenix (`us-phoenix-1`) and San Jose (`us-sanjose-1`). San Jose is the closer of the two to Los Angeles, so it is the target.  
3. **Home-region trap (read this twice).** Always Free resources only exist in the tenancy's **home region**, and the home region is chosen at signup and **cannot be changed afterward**. You must select **US West (San Jose)** as the home region when you create the account. If you ever pick the wrong one, the only fix is a new tenancy.  
4. **Capacity reality.** A1 instances sometimes return "Out of host capacity" in popular regions. San Jose has a single availability domain, so the workaround is retrying over time (Section 4 has a CLI retry loop).  
5. **Idle reclaim.** Always Free compute can be reclaimed by Oracle if it stays idle across a 7-day window (low CPU plus low network). A low-traffic broker is a candidate, so Section 9 includes a keep-warm note.

---

## 1\. Target architecture

```
                    Internet
                       |
            (your office / app IP ranges only)
                       |
        +--------------------------------+
        |   OCI VCN  10.0.0.0/16         |
        |   Public subnet 10.0.0.0/24    |
        |                                |
        |   +------------------------+   |
        |   | A1.Flex VM (arm64)     |   |
        |   |  Oracle Linux 9        |   |
        |   |  Docker / Podman       |   |
        |   |   +----------------+   |   |
        |   |   | rabbitmq:4-mgmt|   |   |
        |   |   |  5671 AMQPS    |   |   |
        |   |   |  15671 mgmt UI |   |   |
        |   |   +----------------+   |   |
        |   |  Caddy (TLS, :443)     |   |
        |   +------------------------+   |
        |   Reserved public IP            |
        +--------------------------------+
```

- One A1.Flex VM, 2 OCPU / 12 GB to start (leaves free-tier headroom; you can scale to 4 OCPU / 24 GB later without leaving the free tier).  
- RabbitMQ runs as a container with a persistent volume.  
- A reverse proxy (Caddy) terminates TLS for the management UI and gives you a clean HTTPS endpoint with automatic certificates.  
- All ingress is restricted by source IP in both the OCI security list and the host firewall.

---

## 2\. Free-tier budget (what keeps this at zero cost)

| Resource | Always Free allowance | This build uses |
| :---- | :---- | :---- |
| Arm compute | 4 OCPU \+ 24 GB RAM total | 2 OCPU \+ 12 GB (1 VM) |
| Block storage | 200 GB total (boot \+ block) | \~50 GB boot volume |
| Outbound data | 10 TB / month | well under |
| Public IP | reserved IP available on free tier | 1 reserved IPv4 |

Guardrails: do not add a load balancer above the free shape, do not exceed 200 GB of total storage, and do not provision a second paid-shape instance. Set a **$0 budget alert** (Section 10\) so any accidental paid resource is caught.

---

## 3\. Account creation (home region \= San Jose)

1. Go to the Oracle Cloud Free Tier signup page.  
2. During signup you are asked for **Home Region**. Select **US West (San Jose)**. This is the irreversible step. Confirm it on the review screen before submitting.  
3. Complete identity verification. A payment method (card) is required for verification even for Always Free; it is not charged as long as you stay on Always Free resources and do not "upgrade to Pay As You Go".  
4. After the tenancy is created, log into the Console and confirm the region selector (top right) shows **US West (San Jose)**.

---

## 4\. Networking (VCN, subnet, security list)

Do this in the Console.

1. **Create the VCN with the wizard.**  
     
   - Console: Networking \> Virtual Cloud Networks \> **Start VCN Wizard** \> "VCN with Internet Connectivity".  
   - Name: `rabbit-vcn`. Accept the default CIDRs (VCN `10.0.0.0/16`, public subnet `10.0.0.0/24`). This creates an Internet Gateway and route table for you.

   

2. **Reserve a public IP.**  
     
   - Networking \> IP Management \> Reserved Public IPs \> Reserve. Name it `rabbit-ip`. You will attach it to the VM's VNIC after launch.

   

3. **Lock down the security list.** Edit the public subnet's security list. Replace the broad default SSH rule with source-restricted rules. Use your actual client CIDR(s) instead of `YOUR.IP.ADDR/32`.  
     
   Ingress rules to add:  
   

| Source CIDR | Protocol | Dest port | Purpose |
| :---- | :---- | :---- | :---- |
| `YOUR.IP.ADDR/32` | TCP | 22 | SSH admin |
| `YOUR.IP.ADDR/32` | TCP | 443 | Management UI over HTTPS (via Caddy) |
| `APP.CIDR/xx` | TCP | 5671 | AMQPS (TLS AMQP) for your apps |
| `YOUR.IP.ADDR/32` | TCP | 80 | Temporary, for first TLS cert issue only |

   

   Do **not** open 5672 (plaintext AMQP) or 15672 (plaintext UI) to the internet. Keep them internal only. Remove the wide-open `0.0.0.0/0` SSH rule the wizard created.

   

   Tip: For tighter control you can use a Network Security Group (NSG) instead of editing the subnet security list, and attach the NSG to the VM's VNIC. Either works; the rules above are identical.

---

## 5\. Provision the A1 compute instance

Console: Compute \> Instances \> **Create Instance**.

1. **Name:** `rabbit-01`.  
2. **Image and shape:**  
   - Image: **Oracle Linux 9** (or Ubuntu 22.04 if you prefer apt; both have arm64 builds). This runbook assumes Oracle Linux 9\.  
   - Shape: click **Change Shape** \> **Ampere** \> `VM.Standard.A1.Flex`. Set **2 OCPUs** and **12 GB**. The form shows an "Always Free eligible" label when you are within the allowance.  
3. **Networking:** VCN `rabbit-vcn`, public subnet, **Assign public IPv4: Yes** (you will swap this for the reserved IP next).  
4. **SSH keys:** upload your public key, or let the Console generate a keypair and download the private key. Store the private key securely.  
5. **Boot volume:** keep the default (about 47 to 50 GB), which stays inside the 200 GB free storage.  
6. Click **Create**.

**If you hit "Out of host capacity":** retry. A scripted retry from your laptop (after installing and configuring the OCI CLI) looks like this. Fill in your own OCIDs first.

```shell
# Placeholders to replace: COMPARTMENT_OCID, SUBNET_OCID, IMAGE_OCID,
# AVAILABILITY_DOMAIN, SSH_PUBKEY_PATH
until oci compute instance launch \
    --availability-domain "AVAILABILITY_DOMAIN" \
    --compartment-id "COMPARTMENT_OCID" \
    --shape "VM.Standard.A1.Flex" \
    --shape-config '{"ocpus":2,"memoryInGBs":12}' \
    --subnet-id "SUBNET_OCID" \
    --image-id "IMAGE_OCID" \
    --display-name "rabbit-01" \
    --ssh-authorized-keys-file "SSH_PUBKEY_PATH" \
    --wait-for-state RUNNING ; do
  echo "Capacity miss, retrying in 60s..."
  sleep 60
done
```

After it is RUNNING, attach the reserved public IP: Instance details \> attached VNIC \> IPv4 addresses \> edit the primary private IP \> assign the reserved public IP `rabbit-ip`.

---

## 6\. Connect and update the host

```shell
# Oracle Linux 9 default user is 'opc'
ssh -i /path/to/private.key opc@RESERVED_PUBLIC_IP

sudo dnf -y update
```

---

## 7\. Install the container runtime (arm64)

Two options. Pick one.

**Option A: Podman (ships with Oracle Linux, rootful, no extra repo)**

```shell
sudo dnf -y install podman podman-docker
sudo systemctl enable --now podman.socket
podman --version
```

**Option B: Docker CE**

```shell
sudo dnf -y install dnf-plugins-core
sudo dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
sudo dnf -y install docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo systemctl enable --now docker
sudo usermod -aG docker opc   # log out and back in to take effect
docker --version
```

The rest of this runbook uses `docker`/`docker compose`. With Podman, substitute `podman` and `podman compose` (the `podman-docker` package also aliases `docker`).

---

## 8\. Host firewall (the OCI port gotcha)

Oracle Linux images enforce their own firewall in addition to the OCI security list, so a port has to be open in **both** layers. Open only what you exposed in Section 4\.

```shell
# 443 (HTTPS UI via Caddy) and 5671 (AMQPS) to the world is fine here because
# the OCI security list already restricts by source IP.
sudo firewall-cmd --permanent --add-port=443/tcp
sudo firewall-cmd --permanent --add-port=5671/tcp
sudo firewall-cmd --permanent --add-port=80/tcp   # temporary, for cert issuance
sudo firewall-cmd --reload
sudo firewall-cmd --list-ports
```

(Ubuntu OCI images do not enable a host firewall by default and rely on the OCI security list; if you chose Ubuntu, you can skip firewalld and just manage the security list.)

---

## 9\. Run RabbitMQ

Create a working directory and a compose file. Set strong credentials; never use the default `guest/guest`.

```shell
mkdir -p ~/rabbit && cd ~/rabbit
```

`~/rabbit/docker-compose.yml`:

```
services:
  rabbitmq:
    image: rabbitmq:4-management   # multi-arch; pulls arm64v8 on the A1 node
    container_name: rabbitmq
    hostname: rabbit-01            # pin the node name so data survives restarts
    restart: unless-stopped
    environment:
      RABBITMQ_DEFAULT_USER: "admin"
      RABBITMQ_DEFAULT_PASS: "CHANGE_ME_long_random_password"
    ports:
      - "127.0.0.1:5672:5672"      # plaintext AMQP, localhost only
      - "127.0.0.1:15672:15672"    # plaintext mgmt UI, localhost only (Caddy proxies it)
      - "5671:5671"                # AMQPS, exposed (restricted by firewall + security list)
    volumes:
      - rabbit-data:/var/lib/rabbitmq
      - ./rabbitmq.conf:/etc/rabbitmq/rabbitmq.conf:ro
      - ./certs:/certs:ro
    healthcheck:
      test: ["CMD", "rabbitmq-diagnostics", "-q", "ping"]
      interval: 30s
      timeout: 10s
      retries: 5

volumes:
  rabbit-data:
```

`~/rabbit/rabbitmq.conf` (memory watermark plus TLS listener for AMQPS):

```
# Keep RabbitMQ aware of the container memory budget
vm_memory_high_watermark.relative = 0.6

# Disable plaintext AMQP on the public interface; keep it on loopback only
# (loopback mapping above already restricts it)

# AMQPS (TLS) listener on 5671
listeners.ssl.default = 5671
ssl_options.certfile  = /certs/server.crt
ssl_options.keyfile   = /certs/server.key
ssl_options.cacertfile = /certs/ca.crt
ssl_options.verify    = verify_none
ssl_options.fail_if_no_peer_cert = false
```

Bring it up (you will add certs in Section 10 before relying on AMQPS):

```shell
docker compose up -d
docker compose logs -f rabbitmq   # watch for "Server startup complete"
```

Verify the broker:

```shell
docker exec rabbitmq rabbitmq-diagnostics status
docker exec rabbitmq rabbitmqctl list_users
```

**Keep-warm (avoid idle reclaim):** if the broker will be quiet, add a tiny cron that publishes and consumes a heartbeat message a few times a day, or run a small periodic `rabbitmq-diagnostics` plus a light network task, so the node does not look idle across a 7-day window.

---

## 10\. Public access

Plaintext UI (15672) and AMQP (5672) stay bound to localhost at all times.

### 10A. SSH tunnel (admin and testing)

Forward both ports to your laptop over SSH without opening any firewall rules:

```shell
ssh -i ~/.ssh/id_rsa \
    -L 15672:127.0.0.1:15672 \
    -L 5673:127.0.0.1:5672 \
    opc@RESERVED_PUBLIC_IP
```

- Management UI: `http://localhost:15672`
- AMQP (plaintext, tunnel-encrypted): `amqp://admin:PASSWORD@localhost:5673`

### 10B. AMQPS with self-signed certificate (direct app access)

Use this when apps need to connect without an SSH tunnel. Port 5671 is exposed publicly but restricted by IP in the OCI security list and host firewall.

**1. Generate a self-signed certificate on the server:**

```shell
mkdir -p /opt/rabbit/certs
openssl req -x509 -newkey rsa:4096 \
    -keyout /opt/rabbit/certs/server.key \
    -out /opt/rabbit/certs/server.crt \
    -days 3650 -nodes \
    -subj "/CN=rabbit-01" \
    -addext "subjectAltName=IP:RESERVED_PUBLIC_IP"
cp /opt/rabbit/certs/server.crt /opt/rabbit/certs/ca.crt
```

**2. Create `/opt/rabbit/rabbitmq.conf`:**

```
vm_memory_high_watermark.relative = 0.6

listeners.ssl.default = 5671
ssl_options.certfile   = /certs/server.crt
ssl_options.keyfile    = /certs/server.key
ssl_options.cacertfile = /certs/ca.crt
ssl_options.verify     = verify_none
ssl_options.fail_if_no_peer_cert = false
```

**3. Update `/opt/rabbit/docker-compose.yml`** to expose port 5671 and mount the certs and config:

```
    ports:
      - "127.0.0.1:5672:5672"
      - "127.0.0.1:15672:15672"
      - "5671:5671"                                        # add
    volumes:
      - rabbit-data:/var/lib/rabbitmq
      - ./rabbitmq.conf:/etc/rabbitmq/rabbitmq.conf:ro    # add
      - ./certs:/certs:ro                                  # add
```

**4. Open the OCI security list** (Networking > VCN > public subnet security list):

| Source CIDR | Protocol | Dest port | Purpose |
| :---- | :---- | :---- | :---- |
| `APP.CIDR/xx` | TCP | 5671 | AMQPS for app clients |

**5. Open the host firewall:**

```shell
sudo firewall-cmd --permanent --add-port=5671/tcp
sudo firewall-cmd --reload
```

**6. Restart the broker:**

```shell
docker compose up -d
```

**Client connection URL.** Because the certificate is self-signed, clients must opt out of certificate verification. The ORIAnnotations plugins support this via a query parameter:

```
amqps://USERNAME:PASSWORD@RESERVED_PUBLIC_IP:5671/?verify=ignore
```

### 10C. Management UI via Caddy (optional, requires DNS)

If you point a hostname at the reserved public IP, Caddy can front the management UI with a trusted Let's Encrypt certificate on port 443.

`~/rabbit/Caddyfile`:

```
rabbit.yourdomain.com {
    reverse_proxy 127.0.0.1:15672
}
```

Add Caddy to the compose stack:

```yaml
  caddy:
    image: caddy:latest
    container_name: caddy
    restart: unless-stopped
    network_mode: host
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy-data:/data
      - caddy-config:/config
```

Add `caddy-data:` and `caddy-config:` under the top-level `volumes:` key. Open port 80 temporarily for the initial cert issuance, then remove it once the cert is issued.

### 10D. Budget alarm

Console: Billing & Cost Management \> Budgets \> create a budget with a low threshold and email alert so any non-free resource is caught immediately.

---

## 11\. Hardening checklist

- [ ] Default `guest` user is absent or disabled (the official image with `RABBITMQ_DEFAULT_USER` does not create `guest`; confirm with `rabbitmqctl list_users`).  
- [ ] Admin password is long and random, stored in a password manager.  
- [ ] 5672 and 15672 bound to `127.0.0.1` only; public access is TLS only (5671, 443).  
- [ ] OCI security list ingress restricted by source IP; no `0.0.0.0/0` on SSH.  
- [ ] Host firewall (firewalld) mirrors the security list.  
- [ ] OS auto-updates or a monthly `dnf update` cadence.  
- [ ] Export broker definitions periodically: `docker exec rabbitmq rabbitmqctl export_definitions /var/lib/rabbitmq/defs.json` and copy them off the box.  
- [ ] Enable OCI block volume backups (free tier allows backups) for the boot volume, or snapshot before upgrades.  
- [ ] Budget alert configured.

---

## 12\. Verification (end-to-end smoke test)

1. Browse to `https://rabbit.yourdomain.com`, log in as `admin`.  
2. From an app host inside the allowed CIDR, test AMQPS:

```shell
# example using rabbitmqadmin or a small client; connect to amqps://admin@HOST:5671/
openssl s_client -connect rabbit.yourdomain.com:5671 -servername rabbit.yourdomain.com </dev/null
```

3. Publish and consume a test message through the management UI ("Queues" tab \> add a queue \> publish \> get).

---

## 13\. Upgrade and teardown

- **Upgrade:** `docker compose pull && docker compose up -d`. The `rabbit-data` volume and pinned `hostname` keep your definitions and queues.  
- **Teardown:** `docker compose down` (keeps data) or `docker compose down -v` (deletes the data volume). Terminate the instance and release the reserved IP in the Console to fully clean up.

---

## References

- OCI Always Free Resources (home-region rule, shapes, idle reclaim): [https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier\_topic-Always\_Free\_Resources.htm](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)  
- OCI Always Free resource reference (A1 4 OCPU / 24 GB, AMD micros): [https://docs.oracle.com/en-us/iaas/Content/FreeTier/resourceref.htm](https://docs.oracle.com/en-us/iaas/Content/FreeTier/resourceref.htm)  
- US West (San Jose) region announcement (`us-sanjose-1`): [https://docs.oracle.com/en-us/iaas/releasenotes/changes/884eb02f-f50a-4b17-9581-1c7446d9485d/index.htm](https://docs.oracle.com/en-us/iaas/releasenotes/changes/884eb02f-f50a-4b17-9581-1c7446d9485d/index.htm)  
- Oracle Cloud region list (Phoenix vs San Jose, US West): [https://dgtlinfra.com/oracle-cloud-data-center-locations/](https://dgtlinfra.com/oracle-cloud-data-center-locations/)  
- RabbitMQ downloads and current version (4.3.1): [https://www.rabbitmq.com/docs/download](https://www.rabbitmq.com/docs/download)  
- RabbitMQ official Docker image (arm64v8, env vars, mgmt UI): [https://hub.docker.com/\_/rabbitmq/](https://hub.docker.com/_/rabbitmq/)  
- RabbitMQ configuration and memory alarms: [https://www.rabbitmq.com/configure](https://www.rabbitmq.com/configure)  
- RabbitMQ TLS Support: [https://www.rabbitmq.com/docs/ssl](https://www.rabbitmq.com/docs/ssl)

