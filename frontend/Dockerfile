FROM node:22-alpine@sha256:b6f26b36c8ff49624cfdac716b8ea1138d606df02586a77d364bb5536a634f85 AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . ./

RUN npm run build

FROM nginx:1.27-alpine@sha256:65645c7bb6a0661892a8b03b89d0743208a18dd2f3f17a54ef4b76fb8e2f2a10 AS runtime

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
COPY docker-entrypoint.d/40-runtime-config.sh /docker-entrypoint.d/40-runtime-config.sh
RUN chmod +x /docker-entrypoint.d/40-runtime-config.sh \
    && sed -i 's!^pid[[:space:]].*;!pid /tmp/nginx.pid;!' /etc/nginx/nginx.conf \
    && sed -i '/^user  nginx;/d' /etc/nginx/nginx.conf \
    && chown -R nginx:nginx /usr/share/nginx/html /etc/nginx/conf.d

USER nginx
EXPOSE 8080
