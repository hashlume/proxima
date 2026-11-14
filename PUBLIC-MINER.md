# Primary miner with a public seeker port (Clore)

Goal: one box holds the key and runs the miner; every other box connects straight to
`http://<public-endpoint>:<port>` with a token. No SSH tunnels.

**The port is the seeker job server: TCP 8100.** (`mine.seeker.listen`)

## 1. When you create the Clore order

Ports are fixed at order creation — you cannot add them later without redeploying.

| Container port | Protocol | Why |
|---|---|---|
| `22` | `tcp` | SSH |
| `8100` | `tcp` | **the miner's seeker job server** |
| `8080` | `http` | optional: the dashboard over HTTPS |

API form:

```json
"ports": { "22": "tcp", "8100": "tcp", "8080": "http" }
```

CLI form:

```bash
clore deploy <server_id> --image cloreai/jupyter:ubuntu24.04-v2 \
  --type on-demand --currency bitcoin \
  --port 22:tcp --port 8100:tcp --port 8080:http
```

Clore maps the container port to a **public port that is usually different**, and hands you
a proxy hostname, not the server IP. In My Orders / `my_orders` the mapping looks like:

```
pub_cluster: ["n1.c1.clorecloud.net", "n2.c1.clorecloud.net"]
tcp_ports:   ["22:10000", "8100:10037"]       ← 8100 inside = 10037 outside
```

So the endpoint is `n1.c1.clorecloud.net:10037`, not `8100` and not the raw IP.

Rules that bite: exactly 5 ports max per order, the service must bind `0.0.0.0` (not
`127.0.0.1`), and use the mapped public port from My Orders.

## 2. On the primary box

`proxi.yaml`:

```yaml
mine:
    seeker:
        listen: 0.0.0.0:8100        # public listener; was 127.0.0.1
        token: <32+ random hex>     # long, this is the only access control
```

```bash
ss -ltnp | grep 8100                # must show 0.0.0.0:8100
TOK=$(sed -n 's/^ *token: //p' proxi.yaml | head -1)
curl -s -H "Authorization: Bearer $TOK" \
     http://127.0.0.1:8100/seeker/job | head -c 100
```

From anywhere else:

```bash
curl -s -H "Authorization: Bearer <token>" \
     http://n1.c1.clorecloud.net:10037/seeker/job | head -c 100
```

That JSON (`version: 2`, `k`, `message`, `ttl_ms`) means it works.

## 3. On every seeker box (Moscow, the old 3090 box, anything new)

No `link.sh`, no SSH:

```bash
screen -dmS gpu0 python3 /root/proxima/seeker/gpu_seeker.py run \
   http://n1.c1.clorecloud.net:10037 <token>
```

The token goes in `/root/proxima/token`; `start.sh` reads it and starts one screen per GPU.

## 4. Security, since the port is public

* **The token is the only gate.** Use 32+ random bytes, rotate it by editing `proxi.yaml`
   and restarting the miner, then updating each seeker.
* The token travels in cleartext HTTP over the Clore TCP forward. Fine for a mining
  endpoint, but if that bothers you: map `8443:http` instead and put Caddy/nginx in front,
  terminating TLS on 8443 and proxying to `127.0.0.1:8100`; seekers then use
  `https://<pub_cluster>`. Do not expose the token on a public URL with the `http` type.
* A wrong token gets `401 {"error":"bad token"}`. There is no rate limiting: without a token
  anyone can make the miner do work, and each posted result costs it one hash check.
* Do not expose the dashboard (`8080`) and the job port on the same port; they are separate
  listeners. The dashboard is read-only but shows your holder ID and progress.

## 5. Migrating from the current setup

1. **Stop the miner on the old box** so two miners do not race on the same key:
   `supervisorctl stop proxima`, then quit the `mine` and `cons` screens.
   Keep its 2× 3090s as a seeker.
2. Copy `proxima.key` and `proxi.yaml` to the new box, `proxi util key info --file
   proxima.key` must print the same holder ID.
3. On the new box: `proxi node mine --workers 0 --stream <two public nodes>` plus **the only**
   `proxi node consolidate`.
4. Point every seeker at the public endpoint (step 3) and delete `link.sh` from their
   start scripts (or leave it stopped).
5. New Clore order = new mapped port. If the order is recreated later, update the URL in
   every seeker's `start.sh`/`token` file.

One consolidator per wallet. Two would build conflicting transactions from the same UTXOs.

---

## Deployed instance (current)

```
miner      n1.de.clorecloud.net:1854    6 × CMP 50HX (sm_75), 2 cores
public job n1.de.clorecloud.net:1855 -> container 8100   (Clore TCP mapping)
token      05c83b2b3cd40281e89e8ddd469c9d01   (/root/proxima/token)
wallet     a/1e88ad2dc180069e716cdf18162ce759a8f8b1ebbf1667503fac0a37bcc000e0
```

Verified from outside: `GET http://n1.de.clorecloud.net:1855/seeker/job` with the bearer
token returns `version: 2`; without it, `401 bad token`.

A seeker anywhere on the internet is then just:

```bash
python3 gpu_seeker.py run http://n1.de.clorecloud.net:1855 05c83b2b3cd40281e89e8ddd469c9d01
```

Note the two numbers in the Clore mapping: **1855 is the public port, 8100 is the container
port**; the miner inside still listens on 8100.
