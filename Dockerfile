FROM node:24-alpine AS assets
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY assets ./assets
COPY scripts ./scripts
COPY templates ./templates
COPY dashboard ./dashboard
COPY analytics ./analytics
RUN mkdir -p static/css && npm run build

FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DJANGO_DEBUG=false
WORKDIR /app
RUN addgroup --system sitehits && adduser --system --ingroup sitehits sitehits
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
COPY --from=assets /app/static ./static
RUN python manage.py collectstatic --noinput && chown -R sitehits:sitehits /app
USER sitehits
EXPOSE 8000
CMD ["sh", "scripts/start.sh"]
