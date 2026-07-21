FROM nginx:alpine

# Experimental Void marketing theme — LAN preview only (:5081)
COPY index.html /usr/share/nginx/html/
COPY style.css /usr/share/nginx/html/
COPY app.js /usr/share/nginx/html/
COPY bliss-homage.jpg /usr/share/nginx/html/
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
COPY releases.json /usr/share/nginx/html/
COPY scan-results.json /usr/share/nginx/html/
RUN mkdir -p /usr/share/nginx/html/.well-known
COPY tdmrep.json /usr/share/nginx/html/.well-known/tdmrep.json

RUN cat > /etc/nginx/conf.d/default.conf << 'EOF'
types {
    application/manifest+json                        webmanifest;
}

server {
    listen 80;
    root /usr/share/nginx/html;
    index index.html;
    server_tokens off;

    add_header X-Frame-Options "DENY" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    add_header Permissions-Policy "camera=(), microphone=(), geolocation=(), payment=(), usb=(), magnetometer=(), gyroscope=(), accelerometer=(), interest-cohort=()" always;
    add_header Content-Security-Policy "default-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.bunny.net; font-src 'self' https://fonts.bunny.net; img-src 'self' data: https://img.shields.io https://github.com; script-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'" always;
    add_header X-Permitted-Cross-Domain-Policies "none" always;
    add_header Cross-Origin-Opener-Policy "same-origin" always;
    add_header Cross-Origin-Resource-Policy "same-origin" always;
    add_header X-Robots-Tag "noindex, nofollow, noai, noimageai" always;

    location = /manifest.json {
        types { }
        default_type application/manifest+json;
        add_header Cache-Control "public, max-age=300" always;
        add_header X-Content-Type-Options "nosniff" always;
        try_files $uri =404;
    }
    location = /site.webmanifest {
        types { }
        default_type application/manifest+json;
        add_header Cache-Control "public, max-age=300" always;
        add_header X-Content-Type-Options "nosniff" always;
        try_files $uri =404;
    }

    location = /releases.json {
        default_type application/json;
        add_header Cache-Control "public, max-age=300" always;
        add_header X-Content-Type-Options "nosniff" always;
        try_files $uri =404;
    }
    location = /scan-results.json {
        default_type application/json;
        add_header Cache-Control "public, max-age=300" always;
        add_header X-Content-Type-Options "nosniff" always;
        try_files $uri =404;
    }

    location / {
        try_files $uri $uri/ /index.html;
    }

    location ~* \.(css|js|png|jpg|ico|svg|woff2?|webmanifest)$ {
        expires 1d;
        add_header Cache-Control "public";
    }

    location ^~ /.well-known/ { allow all; }
    location ~ /\.[^/]+ { deny all; }
    location ~ ~$ { deny all; }
}
EOF

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD wget -qO /dev/null http://127.0.0.1/ || exit 1
