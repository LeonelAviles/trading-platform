# Frontend image: Vite build served by `vite preview`, proxying /api to the
# backend service (see vite.config.js — the target is read from VITE_API_PROXY).
FROM node:22-alpine

WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build

EXPOSE 5173
CMD ["npm", "run", "preview", "--", "--host", "0.0.0.0", "--port", "5173"]
