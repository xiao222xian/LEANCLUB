"""Bind each public Host to its own same-origin API without sharing browser identity."""
import ipaddress
import re
from urllib.parse import urlsplit

from campaign_http import CampaignAPI
from store import Problem

DNS_LABEL = re.compile(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?')


def authority(value, default_port):
    """Parse one HTTP Host authority; no URLs, lists, credentials or forwarding headers."""
    if (not isinstance(value, str) or not value or len(value) > 280
            or any(ord(char) <= 32 or ord(char) >= 127 for char in value)
            or any(char in value for char in '/\\@?#,%') or value.endswith(':')):
        raise ValueError('Invalid Host authority.')
    parsed = urlsplit('//' + value)
    if parsed.path or parsed.query or parsed.fragment or not parsed.hostname:
        raise ValueError('Invalid Host authority.')
    host = parsed.hostname.lower()
    port = parsed.port if parsed.port is not None else default_port
    if not 1 <= port <= 65535:
        raise ValueError('Invalid Host port.')
    try:
        address = ipaddress.ip_address(host)
        host = str(address)
        if address.version == 6 and not value.startswith('['):
            raise ValueError('IPv6 Host must use brackets.')
    except ValueError:
        if ':' in host or len(host) > 253 or not all(DNS_LABEL.fullmatch(part) for part in host.split('.')):
            raise ValueError('Invalid Host name.')
    return host, port


def is_loopback(host):
    if host == 'localhost':
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def https_origin_key(origin):
    key = CampaignAPI.origin_key(origin)
    if key is None or key[0] != 'https' or key[2] != 443:
        raise ValueError('Compatibility origins must be exact HTTPS origins on port 443.')
    host, port = authority(urlsplit(origin).netloc, 443)
    if is_loopback(host) or '.' not in host:
        raise ValueError('Compatibility origins must use explicit public DNS names.')
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return key
    raise ValueError('Compatibility origins must use DNS names, not IP addresses.')


def compatibility_apis(primary, configured):
    """Validate configuration once, creating immutable, origin-specific API objects."""
    if not configured:
        return ()
    if not isinstance(configured, str):
        raise ValueError('CLUB_COMPAT_ORIGINS must be a comma-separated string.')
    origins = [value.strip() for value in configured.split(',')]
    if len(origins) > 8 or any(not value for value in origins):
        raise ValueError('Supply between one and eight nonempty compatibility origins.')
    seen = {https_origin_key(primary.origin)}
    result = []
    for origin in origins:
        key = https_origin_key(origin)
        if key in seen:
            raise ValueError('Compatibility origins must be distinct from the primary and each other.')
        seen.add(key)
        result.append(CampaignAPI(primary.store, 'https://' + key[1]))
    return tuple(result)


def select_api(primary, compatible, headers, peer):
    hosts = headers.get_all('Host', [])
    if len(hosts) != 1:
        raise Problem(400, '请求域名无效。')
    scheme, configured_host, configured_port = CampaignAPI.origin_key(primary.origin)
    try:
        requested = authority(hosts[0], 443 if scheme == 'https' else 80)
    except (ValueError, TypeError):
        raise Problem(400, '请求域名无效。')
    # The local preview intentionally calls the API's loopback port rather than
    # the frontend port. Preserve this development-only behavior, never for a
    # production HTTPS origin and never by trusting X-Forwarded-Host/Proto.
    if (scheme == 'http' and is_loopback(configured_host) and is_loopback(peer)
            and is_loopback(requested[0]) and not compatible):
        return primary
    for api in (primary, *compatible):
        key = CampaignAPI.origin_key(api.origin)
        if requested == (key[1], key[2]):
            return api
    raise Problem(421, '请求域名未配置。')
