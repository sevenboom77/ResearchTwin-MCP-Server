# Linux deployment guide

This guide prepares the existing ResearchTwin MCP server for a Linux host that
OpenTrek can reach. It does not provision a server, buy cloud resources,
create DNS records, change a cloud account, open a firewall, or register a
service in OpenTrek.

> Scope: these assets deploy the remote Streamable HTTP service. They are not
> a BaiLian FC/uvx deployment guide, and their host data directory guarantees
> do not apply to an FC temporary filesystem. See
> [PyPI and BaiLian uvx preparation](pypi_release.md).

The server continues to expose the existing Streamable HTTP endpoint:

~~~text
/mcp
~~~

The recommended production shape is:

~~~text
OpenTrek / BaiLian
        |
        | HTTPS Streamable HTTP
        v
Nginx on TCP 443
        |
        v
ResearchTwin MCP Server on 127.0.0.1:8000
        |
        v
/opt/researchtwin-mcp/data
~~~

For an initial, trusted campus-LAN verification only, a Linux host may bind
directly to 0.0.0.0:8000. Do not leave that unauthenticated HTTP endpoint open
to the public Internet.

> **Security boundary:** the current MCP service has no application-level
> authentication or authorization. HTTPS protects traffic in transit but does
> not decide who may call the tools. For public use, use HTTPS, restrict
> inbound access according to the platform/network policy, and evaluate an
> approved authentication mechanism before long-term exposure.

## A. Prerequisites

Use Ubuntu 22.04/24.04 or a compatible Linux distribution. Before starting,
confirm that the target has:

- Git;
- Python **3.11 or newer** plus its venv module;
- a service account or sudo access for the native systemd option;
- Docker Engine only if choosing the Docker option;
- Nginx only if choosing reverse-proxy/TLS deployment.

Ubuntu 24.04 includes a supported Python version by default. Ubuntu 22.04 may
need an organization-approved Python 3.11+ installation. Do not continue with
Python 3.10: the project requires Python 3.11 or newer.

Check the interpreter before cloning:

~~~bash
python3 --version
~~~

If the result is lower than 3.11, ask the server administrator for an approved
Python 3.11+ installation. Do not replace the system Python.

## B. Choose the deployment target before changing anything

Choose one authorized target:

1. A campus or laboratory Linux server that OpenTrek's caller can route to.
2. An ECS or other cloud Linux server that you are authorized to use.

For a public server, a domain and an approved TLS-certificate process are
strongly recommended. A private Windows address, a browser running locally,
or a successfully saved OpenTrek form does not prove that OpenTrek's backend
can reach a server.

Keep actual IP addresses, domains, environment files, API keys, and research
records outside this Git repository.

## C. Clone and install the native Python deployment

The following native option uses /opt/researchtwin-mcp/. Adjust the paths
consistently if your administrator requires another location.

Create a least-privilege service account and the deployment directory:

~~~bash
sudo useradd --system --user-group --home-dir /opt/researchtwin-mcp --shell /usr/sbin/nologin researchtwin
sudo install -d -o researchtwin -g researchtwin -m 0755 /opt/researchtwin-mcp
~~~

Clone the repository as that account. Substitute the actual repository URL
only in the terminal; do not add a user-specific URL to tracked files.

~~~bash
sudo -u researchtwin git clone <REPOSITORY_URL> /opt/researchtwin-mcp
cd /opt/researchtwin-mcp
~~~

Create an isolated environment using a verified 3.11+ command (replace
python3.11 with the approved interpreter name if necessary):

~~~bash
sudo -u researchtwin python3.11 -m venv /opt/researchtwin-mcp/.venv
sudo -u researchtwin /opt/researchtwin-mcp/.venv/bin/python -m pip install --upgrade pip setuptools wheel
sudo -u researchtwin /opt/researchtwin-mcp/.venv/bin/python -m pip install -e "/opt/researchtwin-mcp[dev]"
~~~

Run the preflight script. It is read-only unless --probe-url is supplied:

~~~bash
sudo -u researchtwin /opt/researchtwin-mcp/.venv/bin/python /opt/researchtwin-mcp/scripts/deployment_check.py
~~~

## D. Configure environment variables and persistent data

Create data and configuration directories. The data directory is deliberately
separate from Git-tracked source and is ignored if it exists beneath the
repository.

~~~bash
sudo install -d -o researchtwin -g researchtwin -m 0750 /opt/researchtwin-mcp/data
sudo install -d -o root -g researchtwin -m 0750 /etc/researchtwin-mcp
sudo install -o root -g researchtwin -m 0640 /dev/null /etc/researchtwin-mcp/researchtwin-mcp.env
sudoedit /etc/researchtwin-mcp/researchtwin-mcp.env
~~~

For native systemd plus Nginx, put only these generic settings in that file:

~~~dotenv
RESEARCHTWIN_HOST=127.0.0.1
RESEARCHTWIN_PORT=8000
RESEARCHTWIN_DATA_DIR=/opt/researchtwin-mcp/data
RESEARCHTWIN_LOG_LEVEL=INFO
~~~

Do not commit this file. It is the right place for future operational
configuration; do not put secrets in the systemd unit or in the repository.

RESEARCHTWIN_HOST=127.0.0.1 keeps the Python process private to the Linux
host. Nginx will be the only public-facing listener. For a short, trusted
campus-LAN direct test, an operator may manually set
RESEARCHTWIN_HOST=0.0.0.0; that does not make it safe for public use.

## E. Run natively with systemd and automatic restart

Copy the supplied example, then review its paths, user, group, and environment
file before enabling it:

~~~bash
cd /opt/researchtwin-mcp
sudo install -m 0644 deploy/researchtwin-mcp.service.example /etc/systemd/system/researchtwin-mcp.service
sudo systemctl daemon-reload
sudo systemctl enable --now researchtwin-mcp
sudo systemctl status researchtwin-mcp --no-pager
~~~

The unit uses Restart=on-failure, so it starts again after an unexpected exit
and is enabled across host restarts. Read logs without adding a separate log
file:

~~~bash
sudo journalctl -u researchtwin-mcp -f
~~~

Expected binding in this reverse-proxy layout:

~~~bash
sudo ss -lntp | grep 8000
~~~

The listener should be on 127.0.0.1:8000, not a public interface.

## F. Docker deployment with automatic restart

Choose this instead of the native systemd Python process; do not run both on
the same host port.

Build from a clean checkout:

~~~bash
cd /opt/researchtwin-mcp
docker build -t researchtwin-mcp:latest .
~~~

Prepare a host-side persistent directory. The image runs as UID/GID 10001, so
that account must be able to write the mounted directory:

~~~bash
sudo install -d -m 0750 /opt/researchtwin-mcp/data
sudo chown 10001:10001 /opt/researchtwin-mcp/data
sudoedit /opt/researchtwin-mcp/.env
~~~

For Docker, use the following non-secret operational configuration. The
container must use 0.0.0.0 internally; the host port mapping below still
limits outside access to the host loopback interface.

~~~dotenv
RESEARCHTWIN_HOST=0.0.0.0
RESEARCHTWIN_PORT=8000
RESEARCHTWIN_DATA_DIR=/app/runtime_data
RESEARCHTWIN_LOG_LEVEL=INFO
~~~

Run the container:

~~~bash
docker run -d \
  --name researchtwin-mcp \
  --restart unless-stopped \
  -p 127.0.0.1:8000:8000 \
  -v /opt/researchtwin-mcp/data:/app/runtime_data \
  --env-file /opt/researchtwin-mcp/.env \
  researchtwin-mcp:latest
~~~

Check its logs and its read-only protocol discovery:

~~~bash
docker logs --tail 100 researchtwin-mcp
docker exec researchtwin-mcp python scripts/deployment_check.py --probe-url http://127.0.0.1:8000/mcp
~~~

The Docker image deliberately excludes .env, runtime_data/, /data/, .git/,
.venv/, test caches, tests, and documentation from its build context. The
bind mount ensures data survives a container replacement.

## G. Configure Nginx reverse proxy

For the native systemd route, copy the example into the site location approved
by your Linux distribution, replace its server_name, then validate before
reloading:

~~~bash
sudo install -m 0644 /opt/researchtwin-mcp/deploy/nginx-researchtwin-mcp.conf.example /etc/nginx/conf.d/researchtwin-mcp.conf
sudoedit /etc/nginx/conf.d/researchtwin-mcp.conf
sudo nginx -t
sudo systemctl reload nginx
~~~

The example proxies only the exact /mcp path to 127.0.0.1:8000/mcp and passes
Host, X-Real-IP, X-Forwarded-For, and X-Forwarded-Proto.

The installed MCP SDK 2.1.0 can return either normal JSON or
text/event-stream for Streamable HTTP. For streamed responses it sends
X-Accel-Buffering: no and keepalive comments. The supplied configuration uses
proxy_http_version 1.1, disables proxy buffering, and applies a 300-second
proxy read/send timeout for that actual behavior. It does not pretend that
legacy SSE /sse is the primary endpoint.

For a public deployment, use a separate TLS-enabled Nginx server block on TCP
443 through your organization's approved certificate process. The public URL
should be:

~~~text
https://<REACHABLE_HOST>/mcp
~~~

Do not publish http://<PUBLIC_IP>:8000/mcp as a long-lived service. HTTPS is
necessary for public traffic, but because the app has no business
authentication it is not sufficient by itself. Restrict ingress and confirm
what authentication/custom-header support OpenTrek provides before public
operation.

## H. Firewall and network principles

This repository does not change firewalls, security groups, NAT, DNS, or
routes. An administrator must make any such decision after confirming the
target network model.

- For Nginx, the Python port 8000 remains loopback-only; only the proxy's
  approved public port (normally 443) should be considered for ingress.
- For a trusted campus direct test, use the narrowest approved rule for the
  chosen port and trusted source network.
- Do not disable a firewall, use a public tunnel, or expose bare port 8000 as
  a workaround.
- A cloud security-group or campus firewall change is an external
  administrative action, not a code deployment step.

## I. Verify in three layers

The current MCP application has no /health endpoint. This guide intentionally
does not add one or change the MCP routing just for deployment checks; use the
following TCP, service-log, and protocol layers instead.

First verify the process listener and logs on the Linux host:

~~~bash
sudo ss -lntp | grep 8000
sudo journalctl -u researchtwin-mcp --since "10 minutes ago" --no-pager
~~~

Then use the official MCP client through the deployed local endpoint. This
performs only initialization and tools/list; it does not write research data:

~~~bash
sudo -u researchtwin /opt/researchtwin-mcp/.venv/bin/python \
  /opt/researchtwin-mcp/scripts/deployment_check.py \
  --probe-url http://127.0.0.1:8000/mcp
~~~

It must report exactly these six tools:

1. record_research_activity
2. list_research_activities
3. update_project_status
4. get_project_status
5. record_advisor_instruction
6. generate_research_report

Finally, from an authorized second machine or the actual platform network
path, verify reachability to the final proxy URL. A browser page or raw TCP
connection is not a substitute for MCP discovery. After remote reachability
is proven, use the OpenTrek UI to discover the six tools, call
get_project_status, record an activity, list it back, and generate a report.
Only then is an OpenTrek end-to-end integration complete.

## J. Register the deployed endpoint in OpenTrek

Do this only after the host is running and reachable from the platform.

| Field | Value |
| --- | --- |
| Service name | ResearchTwin-MCP |
| Transport | Select STREAMABLE |
| URL, trusted campus server | http://<CAMPUS_SERVER_IP>:8000/mcp |
| URL, public TLS deployment | https://<REACHABLE_HOST>/mcp |
| Timeout | 60 seconds, if the UI offers it |

If the current platform accepts an imported configuration in this format, the
deployment target is:

~~~json
{
  "mcpServers": {
    "researchtwin-mcp": {
      "disabled": false,
      "timeout": 60,
      "url": "https://<REACHABLE_HOST>/mcp",
      "transportType": "streamable"
    }
  }
}
~~~

Use the actual UI's transport selection if it does not accept that shape. Do
not write a future real address into source code or documentation.

## K. Updates, persistence, and backups

The application data directory is independent from Git-tracked source and from
the container writable layer:

- native systemd: /opt/researchtwin-mcp/data (an ignored directory beneath the
  deployment root);
- Docker: host /opt/researchtwin-mcp/data mounted at /app/runtime_data.

Do not delete that directory during source updates or container replacement.
Before an update, make a protected backup according to your institution's data
policy. For a simple single-host snapshot, stop the service/container first,
archive the data directory to an approved protected location, then restart it.
This project does not implement automated backup, multi-host replication, or
database-grade concurrency.

For a native update, stop the service, update source with a fast-forward-only
Git pull, refresh the virtual environment dependencies, run tests, then start
the service:

~~~bash
sudo systemctl stop researchtwin-mcp
sudo -u researchtwin git -C /opt/researchtwin-mcp pull --ff-only
sudo -u researchtwin /opt/researchtwin-mcp/.venv/bin/python -m pip install -e "/opt/researchtwin-mcp[dev]"
sudo -u researchtwin sh -c 'cd /opt/researchtwin-mcp && .venv/bin/python -m pytest -v'
sudo -u researchtwin /opt/researchtwin-mcp/.venv/bin/python /opt/researchtwin-mcp/scripts/smoke_test.py
sudo systemctl start researchtwin-mcp
~~~

For a Docker update, build a new tagged image, stop and remove only the named
container after verifying its exact name, then run a replacement using the
same bind mount and --restart unless-stopped. Do not remove
/opt/researchtwin-mcp/data.

## What remains a manual decision

Deployment preparation is complete when the files in this repository pass
their local tests. Actual remote deployment still needs an authorized target
and these facts:

- whether the target is campus/lab Linux or cloud Linux;
- operating system and version;
- whether sudo is available;
- the approved internal or public network path;
- whether a domain and TLS certificate process are available;
- the OpenTrek caller's network reachability and any allowed authentication
  headers or access controls.

Do not guess those values or create cloud/network resources automatically.
