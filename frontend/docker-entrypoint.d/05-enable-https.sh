#!/bin/sh
# Enable TLS inside the container: when Let's Encrypt certificates are present
# in the bind-mounted /etc/nginx/certs (see docker-compose.production.yml), add
# a :443 server block and an http->https redirect. Otherwise the container
# serves plain HTTP on :80 only (staging / pre-TLS setup).
#
# The official nginx image runs every executable *.sh in /docker-entrypoint.d/
# before starting nginx, which is where this script is placed by Dockerfile.prod.
set -eu

certs_dir=/etc/nginx/certs
if ! [ -f "${certs_dir}/fullchain.pem" ] && ! [ -f "${certs_dir}/privkey.pem" ]; then
    echo "[https] no certificates found in ${certs_dir}; serving plain HTTP"
    exit 0
fi

if ! [ -f "${certs_dir}/fullchain.pem" ] || ! [ -f "${certs_dir}/privkey.pem" ]; then
    echo "[https] WARNING: incomplete certificate pair in ${certs_dir}; serving plain HTTP" >&2
    exit 0
fi

echo "[https] activating TLS (certificates present)"

cat > /etc/nginx/conf.d/https.conf <<'EOF'
server {
    listen 443 ssl;
    server_name _;

    ssl_certificate     /etc/nginx/certs/fullchain.pem;
    ssl_certificate_key /etc/nginx/certs/privkey.pem;

    client_max_body_size 60m;

    root /usr/share/nginx/html;
    index index.html;

    # Shared security headers + HSTS (TLS transport only).
    include /etc/nginx/security-headers.conf;
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;

    include /etc/nginx/mime.types;
    types {
        application/javascript mjs;
    }

    # SPA routing
    location / {
        try_files $uri $uri/ /index.html;
    }

    location = /index.html {
        add_header Cache-Control "no-cache, no-store, must-revalidate";
        add_header Pragma "no-cache";
        include /etc/nginx/security-headers.conf;
        add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    }

    location /assets/ {
        add_header Cache-Control "public, max-age=31536000, immutable";
        include /etc/nginx/security-headers.conf;
        add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    }

    location /api/ {
        proxy_pass http://backend:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
    }

    location /api2/ {
        proxy_pass http://backend:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
    }

    location = /health {
        proxy_pass http://backend:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 30s;
    }
}
EOF

# Redirect existing HTTP pages to HTTPS, but keep /.well-known/acme-challenge/
# served over plain HTTP so certbot renewals can still validate.
cat > /etc/nginx/conf.d/00-http-redirect.conf <<'EOF'
server {
    listen 80;
    server_name _;

    location /.well-known/acme-challenge/ {
        root /usr/share/nginx/html/certbot;
    }

    location / {
        return 301 https://$host$request_uri;
    }
}
EOF