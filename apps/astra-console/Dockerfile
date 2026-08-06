FROM node:22-bookworm-slim AS dependencies

WORKDIR /app
COPY package.json ./
RUN npm install --ignore-scripts

FROM node:22-bookworm-slim

WORKDIR /app
COPY --from=dependencies /app/node_modules ./node_modules
COPY . ./
ARG NEXT_PUBLIC_ASTRA_AGENT_URL=http://127.0.0.1:8000
ENV NEXT_PUBLIC_ASTRA_AGENT_URL=$NEXT_PUBLIC_ASTRA_AGENT_URL
RUN npm run build
EXPOSE 3000
CMD ["npm", "run", "start"]
