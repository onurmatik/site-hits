# SiteHits deployment

From this directory, deploy the current `main` branch with:

```bash
python3 -m fabric deploy
```

The first deployment creates `/srv/apps/sitehits/.env` with private runtime
secrets. Later deployments preserve that file, rebuild frontend assets, apply
database migrations, collect static files, and refresh the cold-tier socket.
