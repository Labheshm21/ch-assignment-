FROM node:22-alpine AS dependencies
WORKDIR /app
COPY package.json package-lock.json ./
# Alpine resolves one optional transitive peer differently from Windows.
# Compatibility mode keeps the locked runtime packages while tolerating that
# dev-only peer layout during the container build.
RUN npm install --global npm@10.9.4 \
    && npm install --legacy-peer-deps --no-audit --no-fund

FROM node:22-alpine AS builder
WORKDIR /app
COPY --from=dependencies /app/node_modules ./node_modules
COPY . .
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
RUN npm run build

FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
COPY --from=builder /app/.next ./.next
COPY --from=builder /app/public ./public
COPY --from=builder /app/package.json ./package.json
COPY --from=builder /app/node_modules ./node_modules
EXPOSE 3000
CMD ["npm", "start"]
