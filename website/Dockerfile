FROM nginx:alpine

# Copy website assets
COPY index.html /usr/share/nginx/html/
COPY style.css /usr/share/nginx/html/
COPY robots.txt /usr/share/nginx/html/
COPY sitemap.xml /usr/share/nginx/html/
COPY ai.txt /usr/share/nginx/html/
COPY favicon.ico /usr/share/nginx/html/
COPY favicon-32.png /usr/share/nginx/html/
COPY favicon-192.png /usr/share/nginx/html/
COPY favicon-512.png /usr/share/nginx/html/
COPY apple-touch-icon.png /usr/share/nginx/html/
COPY site.webmanifest /usr/share/nginx/html/
COPY manifest.json /usr/share/nginx/html/
RUN mkdir -p /usr/share/nginx/html/.well-known
COPY tdmrep.json /usr/share/nginx/html/.well-known/tdmrep.json

# Security-hardened nginx config — we practice what we preach
RUN cat > /etc/nginx/conf.d/default.conf << 'EOF'
# Correct MIME for PWA manifest (alpine default is often octet-stream)
types {
    text/html                                        html htm shtml;
    text/css                                         css;
    text/xml                                         xml;
    image/gif                                        gif;
    image/jpeg                                       jpeg jpg;
    image/png                                        png;
    image/svg+xml                                    svg svgz;
    image/x-icon                                     ico;
    application/javascript                           js;
    application/json                                 json;
    application/manifest+json                        webmanifest;
    application/wasm                                 wasm;
    font/woff                                        woff;
    font/woff2                                       woff2;
    text/plain                                       txt;
}

server {
    listen 80;
    root /usr/share/nginx/html;
    index index.html;
    server_tokens off;

    # Security headers
    add_header X-Frame-Options "DENY" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    add_header Permissions-Policy "camera=(), microphone=(), geolocation=(), payment=(), usb=(), magnetometer=(), gyroscope=(), accelerometer=(), interest-cohort=()" always;
    add_header Content-Security-Policy "default-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self'; img-src 'self' data: https://img.shields.io https://github.com; script-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'" always;
    add_header X-Permitted-Cross-Domain-Policies "none" always;
    # Avoid COEP require-corp — it breaks shields.io badges and other cross-origin assets
    add_header Cross-Origin-Opener-Policy "same-origin" always;
    add_header Cross-Origin-Resource-Policy "same-origin" always;

    # AI Scraping Prevention Headers
    add_header X-Robots-Tag "noai, noimageai" always;
    add_header X-AI-Scraping "Disallowed. This content is not licensed for AI training." always;
    add_header TDM-Reservation "1" always;
    add_header X-Content-Rights "Not licensed for AI training, scraping, or data mining. All rights reserved. GPL-3.0 applies to the software, not to website content." always;

    # Explicit PWA manifests — correct type (CF often challenges *.webmanifest; prefer /manifest.json)
    location = /manifest.json {
        types { }
        default_type application/manifest+json;
        add_header Cache-Control "public, max-age=86400" always;
        add_header X-Content-Type-Options "nosniff" always;
        add_header Cross-Origin-Resource-Policy "cross-origin" always;
        try_files $uri =404;
    }
    location = /site.webmanifest {
        default_type application/manifest+json;
        add_header Content-Type "application/manifest+json" always;
        add_header Cache-Control "public, max-age=86400" always;
        add_header X-Content-Type-Options "nosniff" always;
        add_header Cross-Origin-Resource-Policy "cross-origin" always;
        try_files $uri =404;
    }

    location / {
        try_files $uri $uri/ /index.html;
    }

    # Cache static assets (include webmanifest)
    location ~* \.(css|js|png|jpg|ico|svg|woff2?|webmanifest)$ {
        expires 30d;
        add_header Cache-Control "public, immutable";
    }

    # Allow .well-known (TDM protocol, ACME, etc)
    location ^~ /.well-known/ { allow all; }

    # Block hidden dotfiles only (/.git, /.env) — NOT filenames like site.webmanifest
    location ~ /\.[^/]+ { deny all; }
    location ~ ~$ { deny all; }
    location ~* /(wp-admin|wp-login|xmlrpc|\.env|\.git|\.svn|\.htaccess|cgi-bin) {
        return 444;
    }
}
EOF

EXPOSE 80

# Use 127.0.0.1 — alpine wget resolves localhost to ::1, but nginx only listens on IPv4
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD wget -qO /dev/null http://127.0.0.1/ || exit 1
