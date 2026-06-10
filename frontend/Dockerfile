FROM nginx:1.27-alpine
RUN apk add --no-cache gettext

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY index.html /tmp/index.html.template

EXPOSE 80

# envsubst substitui DT_RUM_SCRIPT_SRC e DT_TENANT no HTML no arranque
CMD ["/bin/sh", "-c", \
  "envsubst '${DT_RUM_SCRIPT_SRC} ${DT_TENANT}' < /tmp/index.html.template > /usr/share/nginx/html/index.html && nginx -g 'daemon off;'"]
