"""Run explicitly provisioned local gateway files; never create endpoint grants.

python -m tinystudio.lab.gateway_cli --binding-file /private/binding.json
 --token-file /private/token.json --journal-root /private/state
Defaults to 127.0.0.1. Non-loopback listeners require explicit --binding-mode tls
with certificate/key, or --binding-mode reverse-proxy with trusted proxy addresses.
The reverse proxy must terminate TLS and preserve endpoint bearer authentication.
"""
from __future__ import annotations

import argparse
import ipaddress
from pathlib import Path
import stat

from .gateway_api import create_gateway_app
from .gateway_configuration import GatewayFilePolicy,GatewayRequestPolicy


def listener_options(host, binding_mode, tls_cert=None, tls_key=None, trusted_proxies=None):
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError("listener must use an explicit numeric address") from exc
    options = {"host": str(address), "proxy_headers": False}
    if binding_mode == "loopback":
        if not address.is_loopback or tls_cert or tls_key:
            raise ValueError("loopback mode requires a loopback address and no implicit TLS selection")
    elif binding_mode == "tls":
        if not tls_cert or not tls_key or not Path(tls_cert).is_file() or not Path(tls_key).is_file():
            raise ValueError("TLS mode requires explicit certificate and private key files")
        if stat.S_IMODE(Path(tls_key).stat().st_mode) != 0o600:
            raise ValueError("TLS private key must have mode 0600")
        options.update(ssl_certfile=str(tls_cert), ssl_keyfile=str(tls_key))
    elif binding_mode == "reverse-proxy":
        if not trusted_proxies or tls_cert or tls_key:
            raise ValueError("reverse-proxy mode requires explicit proxy addresses and external TLS termination")
        proxies = [str(ipaddress.ip_address(item)) for item in trusted_proxies.split(",")]
        options.update(proxy_headers=True, forwarded_allow_ips=",".join(proxies))
    else:
        raise ValueError("unsupported operator listener binding mode")
    return options


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binding-file", required=True)
    parser.add_argument("--token-file", required=True)
    parser.add_argument("--journal-root", required=True)
    parser.add_argument('--runtime-secrets-file',help='Private device credentials, separate from the public binding')
    parser.add_argument('--config-max-bytes',type=int,help='Optional operational byte budget for each private configuration file')
    parser.add_argument('--request-max-bytes',type=int,help='Optional operational byte budget for each HTTP command envelope')
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8766, type=int)
    parser.add_argument("--binding-mode", choices=["loopback", "tls", "reverse-proxy"], default="loopback")
    parser.add_argument("--tls-cert")
    parser.add_argument("--tls-key")
    parser.add_argument("--trusted-proxies")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("port must be 1..65535")
    try:
        options = listener_options(args.host, args.binding_mode, args.tls_cert, args.tls_key, args.trusted_proxies)
        selected={}
        if args.runtime_secrets_file is not None:selected['runtime_secrets_file']=args.runtime_secrets_file
        if args.config_max_bytes is not None:selected['file_policy']=GatewayFilePolicy(args.config_max_bytes)
        if args.request_max_bytes is not None:selected['request_policy']=GatewayRequestPolicy(args.request_max_bytes)
        app = create_gateway_app(args.binding_file, args.journal_root, args.token_file,**selected)
    except ValueError as exc:
        parser.error(str(exc))
    import uvicorn
    uvicorn.run(app, port=args.port, **options)


if __name__ == "__main__":
    main()
