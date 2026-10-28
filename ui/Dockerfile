FROM node:26-slim AS build
WORKDIR /ui
COPY package*.json ./
RUN --mount=type=secret,id=npm_ca,target=/tmp/npm-ca.pem \
    if [ -s /tmp/npm-ca.pem ]; then NODE_EXTRA_CA_CERTS=/tmp/npm-ca.pem npm ci --no-audit --no-fund; else npm ci --no-audit --no-fund; fi
COPY . .
RUN npm run build
FROM nginx:stable
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /ui/dist /usr/share/nginx/html
