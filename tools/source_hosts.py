#!/usr/bin/env python3
"""The approved source hosts: web hosts whose documents are treated as published by a public body.

The list is registry/source-hosts.json (README, "Approved source hosts"). A document whose
source_url is on an approved host can be downloaded again and compared with its manifest sha256:
tools/check_sources.py does this in CI, and the daily archive job does it before uploading an
external blob.

  python3 tools/source_hosts.py <url> [<url> ...]   prints the matching entry for each, or "not approved"

Standard library only.
"""
import json, os, re, sys, urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join("registry", "source-hosts.json")
HOST_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*$")


def load(root=ROOT):
    """The list of host entries, or [] when the registry does not exist."""
    p = os.path.join(root, PATH)
    if not os.path.exists(p):
        return []
    with open(p) as f:
        return json.load(f)["hosts"]


def problems(root=ROOT):
    """Reasons the registry is malformed (empty when it is well formed or absent)."""
    p = os.path.join(root, PATH)
    if not os.path.exists(p):
        return []
    try:
        with open(p) as f:
            reg = json.load(f)
    except json.JSONDecodeError as x:
        return [f"invalid JSON: {x}"]
    hosts = reg.get("hosts") if isinstance(reg, dict) else None
    if not isinstance(hosts, list):
        return ['needs a "hosts" list']
    out, seen = [], set()
    for k, h in enumerate(hosts):
        if not isinstance(h, dict):
            out.append(f"hosts[{k}] must be an object"); continue
        name = h.get("host")
        if not isinstance(name, str) or not HOST_RE.match(name):
            out.append(f"hosts[{k}]: host must be a lowercase host name such as berkeleyca.gov")
        elif name in seen:
            out.append(f"hosts[{k}]: {name} is listed twice")
        else:
            seen.add(name)
        if not isinstance(h.get("body"), str) or not h["body"].strip():
            out.append(f"hosts[{k}]: body must name the public body the host belongs to")
        if not isinstance(h.get("subdomains"), bool):
            out.append(f"hosts[{k}]: subdomains must be true or false")
        extra = set(h) - {"host", "body", "subdomains"}
        if extra:
            out.append(f"hosts[{k}]: unknown fields {sorted(extra)}")
    return out


def approved(url, hosts):
    """The host entry that url belongs to, or None. Only http and https URLs can match."""
    try:
        parts = urllib.parse.urlsplit(url or "")
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    name = parts.hostname.lower().rstrip(".")
    for h in hosts:
        if name == h["host"] or (h["subdomains"] and name.endswith("." + h["host"])):
            return h
    return None


def main(argv=None):
    hosts = load()
    for url in (argv if argv is not None else sys.argv[1:]):
        h = approved(url, hosts)
        print(f"{url}\t{h['host']} ({h['body']})" if h else f"{url}\tnot approved")
    return 0


if __name__ == "__main__":
    sys.exit(main())
