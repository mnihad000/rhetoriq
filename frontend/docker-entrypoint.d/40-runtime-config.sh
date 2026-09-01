#!/bin/sh
set -eu

cat > /usr/share/nginx/html/runtime-config.js <<EOF
window.__RHETORIQ_CONFIG__ = { API_BASE_URL: "${PUBLIC_API_BASE_URL:-}" };
EOF
