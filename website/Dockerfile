FROM nginx:alpine

COPY index.html /usr/share/nginx/html/
COPY style.css /usr/share/nginx/html/

# Security headers — we practice what we preach
RUN echo 'server { \
    listen 80; \
    root /usr/share/nginx/html; \
    index index.html; \
    \
    add_header X-Frame-Options "DENY" always; \
    add_header X-Content-Type-Options "nosniff" always; \
    add_header X-XSS-Protection "1; mode=block" always; \
    add_header Referrer-Policy "strict-origin-when-cross-origin" always; \
    add_header Permissions-Policy "camera=(), microphone=(), geolocation=(), payment=()" always; \
    add_header Content-Security-Policy "default-src '\''self'\''; style-src '\''self'\'' https://fonts.googleapis.com '\''unsafe-inline'\''; font-src '\''self'\'' https://fonts.gstatic.com; img-src '\''self'\'' data:; script-src '\''self'\'' '\''unsafe-inline'\''" always; \
    \
    location / { \
        try_files $uri $uri/ /index.html; \
    } \
    \
    location ~* \.(css|js|png|jpg|ico|svg|woff2?)$ { \
        expires 30d; \
        add_header Cache-Control "public, immutable"; \
    } \
}' > /etc/nginx/conf.d/default.conf

EXPOSE 80
