#!/usr/bin/env bash

# Provider SDKs in the Worker use HTTPX without SOCKS support. Desktop VPNs
# commonly export a SOCKS ALL_PROXY even when valid HTTP(S) proxy variables are
# also available. Remove only the incompatible catch-all values; preserve
# HTTP_PROXY, HTTPS_PROXY, NO_PROXY, and HTTP(S)-valued ALL_PROXY settings.
sanitize_worker_proxy_env() {
  local variable value normalized
  for variable in ALL_PROXY all_proxy; do
    value="${!variable-}"
    normalized="${value,,}"
    case "$normalized" in
      socks://*|socks4://*|socks4a://*|socks5://*|socks5h://*)
        unset "$variable"
        printf 'Ignoring unsupported SOCKS proxy from %s for Worker provider clients.\n' \
          "$variable" >&2
        ;;
    esac
  done
}
