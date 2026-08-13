FROM node:24-alpine AS assets
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY assets ./assets
COPY scripts ./scripts
COPY templates ./templates
COPY dashboard ./dashboard
COPY analytics ./analytics
COPY mcp_gateway ./mcp_gateway
RUN mkdir -p static/css && npm run build

FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DJANGO_DEBUG=false
WORKDIR /app
RUN addgroup --system sitehits && adduser --system --ingroup sitehits sitehits
COPY requirements.txt ./
COPY packages ./packages
RUN pip install --no-cache-dir -r requirements.txt
RUN mkdir -p /home/sitehits && chown -R sitehits:sitehits /home/sitehits \
    && su -s /bin/sh sitehits -c "python -c \"import duckdb; c=duckdb.connect(); [c.install_extension(name) for name in ('httpfs','postgres','sqlite')]; c.close()\""
COPY . .
COPY --from=assets /app/static ./static
RUN DJANGO_DEBUG=true python manage.py collectstatic --noinput \
    && chown -R sitehits:sitehits /app
USER sitehits
EXPOSE 8000 8001
CMD ["sh", "scripts/start.sh"]
