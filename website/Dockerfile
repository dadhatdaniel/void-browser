FROM nginx:alpine

# Copy website assets
COPY index.html /usr/share/nginx/html/
COPY style.css /usr/share/nginx/html/
COPY robots.txt /usr/share/nginx/html/
COPY sitemap.xml /usr/share/nginx/html/
COPY ai.txt /usr/share/nginx/html/
RUN mkdir -p /usr/share/nginx/html/.well-known
COPY tdmrep.json /usr/share/nginx/html/.well-known/tdmrep.json

# Security-hardened nginx config — we practice what we preach
RUN cat > /etc/nginx/conf.d/default.conf << 'EOF'
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
    add_header Content-Security-Policy "default-src 'self'; style-src 'self' https://fonts.googleapis.com 'unsafe-inline'; font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; script-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'" always;
    add_header X-Permitted-Cross-Domain-Policies "none" always;
    add_header Cross-Origin-Embedder-Policy "require-corp" always;
    add_header Cross-Origin-Opener-Policy "same-origin" always;
    add_header Cross-Origin-Resource-Policy "same-origin" always;

    # AI Scraping Prevention Headers
    add_header X-Robots-Tag "noai, noimageai" always;
    add_header X-AI-Scraping "Disallowed. This content is not licensed for AI training." always;
    add_header TDM-Reservation "1" always;  # W3C Text & Data Mining reservation
    add_header X-Content-Rights "Not licensed for AI training, scraping, or data mining. All rights reserved. GPL-3.0 applies to the software, not to website content." always;

    location / {
        try_files $uri $uri/ /index.html;
    }

    # Cache static assets
    location ~* \.(css|js|png|jpg|ico|svg|woff2?)$ {
        expires 30d;
        add_header Cache-Control "public, immutable";
    }

    # Block common exploit paths
    location ~ /\. { deny all; }
    location ~ ~$ { deny all; }
    location ~* /(wp-admin|wp-login|xmlrpc|\.env|\.git|\.svn|\.htaccess|cgi-bin) {
        return 444;
    }
}
EOF

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD wget -qO /dev/null http://localhost/ || exit 1
