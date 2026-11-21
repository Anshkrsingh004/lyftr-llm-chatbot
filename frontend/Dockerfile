# ---- Build stage: compile the React app with Vite ----
FROM node:20-alpine AS build

WORKDIR /app
COPY package.json ./
RUN npm install

COPY . .

# API base URL is baked into the static build.
ARG VITE_API_BASE_URL=http://localhost:8000
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
RUN npm run build

# ---- Runtime stage: serve static files with a tiny nginx image ----
FROM nginx:1.27-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 3000
CMD ["nginx", "-g", "daemon off;"]
