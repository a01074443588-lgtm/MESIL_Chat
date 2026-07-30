FROM node:22-alpine AS build

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY . .
RUN npm run build

FROM node:22-alpine

WORKDIR /app
ENV NODE_ENV=production
COPY --from=build /app /app

EXPOSE 3000

CMD ["npm", "run", "start", "--", "--host", "0.0.0.0", "--port", "3000"]
