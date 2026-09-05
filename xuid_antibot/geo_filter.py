# -*- coding: utf-8 -*-
# Copyright 2026 dyan1619
# SPDX-License-Identifier: GPL-3.0-or-later
"""Country filtering + AntiVPN (proxy/VPN/hosting) via ip-api.com —
one shared API, one shared cache, both sitting in the filter layer
BEFORE verification (step -1 in login_guard.py).

How it works:
  - whitelist: only country codes in geo_filter_countries are allowed;
    blacklist: those codes are blocked, the rest are allowed.
  - Lookup: http://ip-api.com/json/<ip>?fields=status,countryCode,
    proxy,hosting (free tier: HTTP, limited to 45 req/min).
  - Results are cached for 24h per IP (self.geo_cache, not saved to
    file) to avoid repeat API calls and stay under ip-api.com's rate
    limit.
  - AntiVPN (geo_filter_block_vpn): blocks IPs with proxy=true OR
    hosting=true, checked BEFORE the country filter (a VPN IP from an
    allowed country is still blocked) — the goal is blocking
    anonymous IPs.
  - FAIL-OPEN: on lookup errors (network, quota, private IP...) ->
    do NOT block, log a warning — better to not block real players by
    mistake; the later layers (bot name/xuid/rate limit) still apply.

See xuid_antibot/__init__.py for where this module sits in the package."""
import json
import time
import urllib.request

GEO_CACHE_TTL = 24 * 3600  # 24 hours, per API guidelines


class GeoFilterMixin:
    DEFAULT_GEO_FILTER_ENABLED = False
    DEFAULT_GEO_FILTER_MODE = "whitelist"
    DEFAULT_GEO_FILTER_COUNTRIES: list = []
    DEFAULT_GEO_FILTER_BLOCK_VPN = False

    # ── ip-api.com lookup (with a 24h cache) ────────────────────

    def _geo_lookup_proxy_hosting(self, ip: str):
        """Returns {'country': str|None, 'proxy': bool, 'hosting':
        bool} for the IP, or None if the lookup failed / ip-api.com
        reported an error (private IP, unresolvable IP...)."""
        now = time.time()
        cached = self.geo_cache.get(ip)
        if cached is not None:
            info, expires_at = cached
            if now < expires_at:
                return info
            # Cache expired -> drop it and look it up again below.
            del self.geo_cache[ip]

        try:
            url = f"http://ip-api.com/json/{ip}?fields=status,countryCode,proxy,hosting"
            with urllib.request.urlopen(url, timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if data.get("status") != "success":
                # e.g. private IP (192.168.x.x, 127.0.0.1...) -> status="fail".
                self.logger.info(
                    f"[XuidAntiBot] geo_filter: ip-api.com could not resolve info for {ip} "
                    f"({data.get('message', 'unknown reason')}) — skipping the country/VPN filter layer (fail-open)."
                )
                return None
            info = {
                "country": data.get("countryCode") or None,
                "proxy": bool(data.get("proxy", False)),
                "hosting": bool(data.get("hosting", False)),
            }
            self.geo_cache[ip] = (info, now + GEO_CACHE_TTL)
            return info
        except Exception as e:
            self.logger.error(
                f"[XuidAntiBot] geo_filter: error calling ip-api.com for IP {ip}: {e} — "
                f"skipping the country/VPN filter layer this time (fail-open, real players are not blocked)."
            )
            return None

    # ── Allow / block decision ──────────────────────────────────

    def _geo_is_allowed(self, ip: str) -> bool:
        """True if the IP passes this filter layer (or both layers are
        disabled, or the lookup failed -> fail-open)."""
        if not self.geo_filter_enabled and not self.geo_filter_block_vpn:
            return True
        if not ip:
            # No IP available -> not enough data, fail-open.
            return True

        # Call the API only once (the cache is shared by both layers).
        info = self._geo_lookup_proxy_hosting(ip)
        if info is None:
            return True  # fail-open

        # AntiVPN is checked BEFORE the country filter (see module
        # docstring).
        if self.geo_filter_block_vpn and (info["proxy"] or info["hosting"]):
            return False

        if not self.geo_filter_enabled:
            return True
        if not self.geo_filter_countries:
            # Filter enabled but the list is empty -> don't filter, to
            # avoid locking out the whole server (empty whitelist =
            # block everyone).
            return True

        country = info["country"]
        if country is None:
            return True  # fail-open

        in_list = country in self.geo_filter_countries
        if self.geo_filter_mode == "blacklist":
            return not in_list
        # Any value other than "blacklist" is treated as "whitelist".
        return in_list
