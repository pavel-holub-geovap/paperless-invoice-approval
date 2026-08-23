FROM nginx:1.27-alpine
COPY infrastructure/nginx/default.conf /etc/nginx/conf.d/default.conf
HEALTHCHECK --interval=20s --timeout=3s --retries=5 CMD wget -q -O /dev/null http://127.0.0.1/healthz || exit 1
