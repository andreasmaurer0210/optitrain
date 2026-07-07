"""
OptiTrain — MCP server for Deutsche Bahn fare optimization.

Modules:
  api.py        HTTP client for HAFAS transport REST API
  tools.py      MCP tool definitions + handlers
  strategies.py Predictive pricing strategies (21-day, split-ticketing)
  resources.py  MCP resource handlers
  prompts.py    MCP prompt templates
  server.py     MCP wiring + entry point
"""

import argparse
import asyncio
import getpass
import json
import os
import sys

from optitrain.server import run_stdio
from optitrain import api as _api


def _credentials_set(args: argparse.Namespace) -> int:
    creds = _api._load_credentials()
    token = args.token
    if token is None:
        # prompt securely
        token = getpass.getpass(prompt=f"Token for {args.backend}: ")
    creds[args.backend] = token
    _api._save_credentials(creds)
    print(f"Saved token for {args.backend} -> {_api._CRED_PATH}")
    return 0


def _credentials_get(args: argparse.Namespace) -> int:
    creds = _api._load_credentials()
    val = creds.get(args.backend)
    if val is None:
        print("<none>")
        return 1
    # mask token minimally
    if isinstance(val, str) and len(val) > 8:
        print(val[:4] + "..." + val[-4:])
    else:
        print(val)
    return 0


def _credentials_list(_: argparse.Namespace) -> int:
    creds = _api._load_credentials()
    for k in sorted(creds.keys()):
        v = creds[k]
        ok = bool(v)
        print(f"{k}: {'present' if ok else 'empty'}")
    return 0


def _credentials_remove(args: argparse.Namespace) -> int:
    creds = _api._load_credentials()
    if args.backend in creds:
        creds.pop(args.backend)
        _api._save_credentials(creds)
        print(f"Removed {args.backend}")
        return 0
    print("not found")
    return 1


def main() -> None:
    """CLI entrypoint. If subcommand 'credentials' used, run admin helpers. Else run server."""
    if len(sys.argv) >= 2 and sys.argv[1] == "credentials":
        parser = argparse.ArgumentParser(prog="optitrain credentials")
        sub = parser.add_subparsers(dest="cmd", required=True)

        p_set = sub.add_parser("set")
        p_set.add_argument("backend")
        p_set.add_argument("--token", help="API token (omit to prompt)")
        p_set.set_defaults(func=_credentials_set)

        p_get = sub.add_parser("get")
        p_get.add_argument("backend")
        p_get.set_defaults(func=_credentials_get)

        p_rm = sub.add_parser("remove")
        p_rm.add_argument("backend")
        p_rm.set_defaults(func=_credentials_remove)

        p_ls = sub.add_parser("list")
        p_ls.set_defaults(func=_credentials_list)

        args = parser.parse_args(sys.argv[2:])
        rc = args.func(args)
        raise SystemExit(rc)

    # default behavior: run server
    asyncio.run(run_stdio())


__all__ = ["main"]
