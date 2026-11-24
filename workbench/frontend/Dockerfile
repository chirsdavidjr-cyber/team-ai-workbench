# 前端镜像：多阶段构建（build 期固化 /api 反代目标，运行 next start）
FROM node:22-alpine AS builder
WORKDIR /app

# 构建参数：后端服务地址（compose 内为 http://backend:8000）
ARG BACKEND_ORIGIN=http://backend:8000
ENV BACKEND_ORIGIN=${BACKEND_ORIGIN} \
    NEXT_TELEMETRY_DISABLED=1

# 国内镜像加速，避免 npm ci 超时
RUN npm config set registry https://registry.npmmirror.com

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build

FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000

COPY --from=builder /app/.next ./.next
COPY --from=builder /app/public ./public
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/package.json ./package.json
COPY --from=builder /app/next.config.ts ./next.config.ts

EXPOSE 3000
CMD ["npx", "next", "start", "-p", "3000"]
