from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlparse
from urllib.parse import parse_qsl
from urllib.robotparser import RobotFileParser

import httpx
import tldextract


class UrlPolicyError(ValueError):
    pass


_domain_extractor = tldextract.TLDExtract(suffix_list_urls=())
_sensitive_query_keys = {
    "access_token", "api_key", "apikey", "auth", "authorization", "client_secret",
    "key", "password", "secret", "signature", "sig", "token",
}


def has_embedded_credentials(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.username or parsed.password:
        return True
    return any(key.lower() in _sensitive_query_keys for key, _value in parse_qsl(parsed.query, keep_blank_values=True))


def registrable_domain(url: str) -> str:
    hostname = (urlparse(url).hostname or "").lower().rstrip(".")
    extracted = _domain_extractor(hostname)
    return extracted.top_domain_under_public_suffix or hostname


@dataclass
class PublicUrlPolicy:
    user_agent: str = "RhetoriQ/0.2 (+public evidence research)"
    enforce_robots: bool = True

    def validate(self, url: str) -> str:
        parsed = urlparse(url.strip())
        if parsed.scheme not in {"http", "https"}:
            raise UrlPolicyError("only public HTTP(S) URLs are permitted")
        if not parsed.hostname:
            raise UrlPolicyError("URL has no hostname")
        if has_embedded_credentials(url):
            raise UrlPolicyError("credentials embedded in URLs or query parameters are forbidden")
        hostname = parsed.hostname.lower().rstrip(".")
        if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(".local"):
            raise UrlPolicyError("local destinations are forbidden")
        try:
            addresses = {item[4][0] for item in socket.getaddrinfo(hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)}
        except socket.gaierror as exc:
            raise UrlPolicyError(f"hostname could not be resolved: {hostname}") from exc
        if not addresses:
            raise UrlPolicyError("hostname did not resolve")
        for raw in addresses:
            ip = ipaddress.ip_address(raw)
            if not ip.is_global or ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
                raise UrlPolicyError(f"non-public destination is forbidden: {ip}")
        return url

    def check_robots(self, url: str, timeout: float = 5.0) -> None:
        if not self.enforce_robots:
            return
        parsed = urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        parser = RobotFileParser()
        parser.set_url(robots_url)
        try:
            response = httpx.get(robots_url, timeout=timeout, headers={"User-Agent": self.user_agent})
            if response.status_code < 400:
                parser.parse(response.text.splitlines())
                if not parser.can_fetch(self.user_agent, url):
                    raise UrlPolicyError("robots policy does not permit retrieval")
        except UrlPolicyError:
            raise
        except httpx.HTTPError:
            return
