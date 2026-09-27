# Contributing

Thanks for taking a look. Issues and pull requests are welcome in English or Russian.

## Development setup

The whole stack runs in Docker:

```bash
cp .env.example .env            # add LLM_API_KEY
docker compose up -d --build
```

- Backend changes: `docker compose up -d --build api worker`
- Frontend changes: `docker compose up -d --build web`, or for hot reload run `cd frontend && npm install && npm run dev` (stop the `web` container first so port 3000 is free).
- Model settings in `config/models.yaml` are hot-reloaded; keep personal settings in `config/models.local.yaml` (git-ignored).

Type-check the frontend with `cd frontend && npx tsc --noEmit`. There is no test suite yet — a PR that adds one is very welcome.

## Layout

```
backend/app/
  routers/      HTTP endpoints (auth, chats, files, admin)
  rag.py        query rewriting, vector search, prompt assembly, citation renumbering
  ingest.py     PDF / CSV / media extraction and chunking
  worker.py     arq jobs: process_file, refresh_file_embeddings, reindex_all
  llm.py        provider client (OpenAI-compatible + GigaChat OAuth), retries
  tools/        experimental chat modules (feature-flagged)
frontend/       Next.js app: app/ routes, components/, lib/
transcriber/    optional local GigaAM ASR service
config/         models.yaml — models and retrieval settings
docs/TZ.md      product spec (Russian)
```

## Pull requests

- Keep PRs focused; describe the user-visible change and how you verified it.
- Match the surrounding style: the codebase uses Russian comments and UI strings — either language is fine for new comments, but UI strings stay Russian until i18n lands.
- Never commit `.env`, `config/models.local.yaml`, uploaded studies or generated tasks.
