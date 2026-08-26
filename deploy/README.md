# Deployment assets

This directory contains editable examples, not a complete server-specific
configuration:

> Scope: these files are for Linux Streamable HTTP deployment. They do not
> configure BaiLian FC/uvx stdio execution; see
> [PyPI and BaiLian uvx preparation](../docs/pypi_release.md).

- researchtwin-mcp.service.example runs the Streamable HTTP server under
  systemd and restarts it after an unexpected exit.
- nginx-researchtwin-mcp.conf.example sends /mcp traffic to a server bound
  to 127.0.0.1:8000.

Use the step-by-step [Linux deployment guide](../docs/deployment_linux.md)
before copying either example. Do not commit an environment file, runtime
records, a real host name, certificate paths, credentials, or private network
details to this repository.
