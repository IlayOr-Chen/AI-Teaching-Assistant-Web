"""
Purpose:
    Extracts the client's real external IP address from incoming HTTP requests
     across Cloudflare Tunnel (`cloudflared`), Render reverse proxies, and local networks.
Inputs:
    FastAPI `Request` objects or raw headers/host strings.
Outputs:
    `ClientIPResolver` class providing deterministic external IP resolution.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Mapping
from fastapi import Request


class ClientIPResolver:
    """
    Purpose:
        Resolves the real external IP address of an incoming HTTP request using
        the strict priority hierarchy:
        1) `CF-Connecting-IP` (injected by Cloudflare Tunnel / edge proxy)
        2) `X-Forwarded-For` (first comma-separated client IP from Render / proxies)
        3) `request.client.host` (direct socket fallback, defaulting to `127.0.0.1`)
    Inputs:
        None (stateless utility class with static/class methods).
    Outputs:
        Resolved IPv4/IPv6 address string and resolution source metadata.
    """

    @staticmethod
    def resolve_from_headers(
        headers: Mapping[str, str], fallback_host: str | None = "127.0.0.1"
    ) -> tuple[str, str]:
        """
        Purpose:
            Determine the client IP and the header source from a case-insensitive
            or standard mapping of HTTP headers and a fallback socket host.
        Inputs:
            headers (Mapping[str, str]): HTTP request headers mapping.
            fallback_host (str | None): Direct client socket host fallback.
        Outputs:
            tuple[str, str]: `(resolved_ip, source_name)` where `source_name` is one of
            `"CF-Connecting-IP"`, `"X-Forwarded-For"`, or `"client.host"`.
        """
        # Normalize header lookup to be case-insensitive
        lower_headers = {k.lower(): v.strip() for k, v in headers.items() if v}

        # Priority 1: Cloudflare Tunnel header (CF-Connecting-IP)
        cf_ip = lower_headers.get("cf-connecting-ip", "")
        if cf_ip:
            # In case multiple values were concatenated, take the first clean token
            first_cf_ip = cf_ip.split(",")[0].strip()
            if first_cf_ip:
                return first_cf_ip, "CF-Connecting-IP"

        # Priority 2: Standard reverse-proxy header (X-Forwarded-For: client, proxy1, proxy2)
        xff = lower_headers.get("x-forwarded-for", "")
        if xff:
            first_xff_ip = xff.split(",")[0].strip()
            if first_xff_ip:
                return first_xff_ip, "X-Forwarded-For"

        # Priority 3: Direct client socket host fallback
        host = (fallback_host or "127.0.0.1").strip() or "127.0.0.1"
        return host, "client.host"

    @staticmethod
    def is_loopback_or_private(ip_address: str) -> bool:
        """
        Purpose:
            Detect addresses that can never be a classroom's shared public IP
            (loopback `127.0.0.1` / `::1` or private LAN ranges such as `192.168.x.x`),
            so the lecturer can be warned that the same-network check cannot work.
        Inputs:
            ip_address (str): IPv4/IPv6 address string.
        Outputs:
            bool: True for loopback/private/link-local/unparseable addresses.
        """
        try:
            parsed = ipaddress.ip_address(ip_address.strip())
        except ValueError:
            return True
        return parsed.is_loopback or parsed.is_private or parsed.is_link_local

    @classmethod
    def resolve(cls, request: Request) -> str:
        """
        Purpose:
            Extract the client's real external IP address string from a FastAPI `Request`.
        Inputs:
            request (Request): Incoming FastAPI HTTP request.
        Outputs:
            str: Resolved client IP address string.
        """
        fallback_host = request.client.host if request.client else "127.0.0.1"
        ip_address, _ = cls.resolve_from_headers(request.headers, fallback_host)
        return ip_address

    @classmethod
    def resolve_with_source(cls, request: Request) -> dict[str, str]:
        """
        Purpose:
            Extract both the client's external IP address and the detection source
            for diagnostic and lecturer dashboard verification.
        Inputs:
            request (Request): Incoming FastAPI HTTP request.
        Outputs:
            dict[str, str]: Dictionary with keys `"ip"` and `"source"`.
        """
        fallback_host = request.client.host if request.client else "127.0.0.1"
        ip_address, source = cls.resolve_from_headers(request.headers, fallback_host)
        return {"ip": ip_address, "source": source}
