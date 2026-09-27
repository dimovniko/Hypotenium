# Hypotenium

**Chat with your UX research archive.** Upload reports, raw data and interview recordings — then ask questions and get answers that cite the exact page, row or timecode they came from.

🇷🇺 Русская версия: [README.ru.md](README.ru.md)

> Status: working MVP, used internally by a UX research team. UI is in Russian; the pipeline is language-agnostic (see [Roadmap](#roadmap) for i18n).

## What it does

- **Knowledge base of studies.** A study is a title, a date and any number of files: PDF reports, CSV raw data, audio/video interviews. Files are chunked, embedded and indexed automatically; media is transcribed with timecodes.
- **Grounded answers with citations.** Every claim in an answer carries a `[n]` marker. Click it to open the source panel: the PDF page, the CSV rows, or the interview segment with a mini-player seeking to the exact moment. If nothing relevant exists, the assistant says so instead of guessing.
- **Honest sources.** Citations are snapshots: if a study is deleted, old chats still show what was cited, marked as removed, and the deleted content never appears in new answers.
- **Chats with history.** Follow-up questions are rewritten with context, dates in the question softly prioritise studies from that period, citation numbers are sequential within a chat, answers render Markdown. Chats can be shared by link and un-shared.
- **Bring your own models.** Three slots — chat, embeddings, transcription — each pointing at any OpenAI-compatible API: OpenAI, OpenRouter, local Ollama/vLLM, GigaChat, and so on. The config is hot-reloaded; switching the embedding model offers a one-click reindex.
- **Admin area.** Manage studies and files (with per-file notes that improve retrieval), approve new users, switch models.
- **Optional local transcription.** A [GigaAM](https://github.com/salute-developers/GigaAM) container gives Russian speech recognition with timecodes and number normalisation, entirely on your machine — audio never leaves it.

## Quick start

Requirements: Docker with Compose v2, and an API key for any OpenAI-compatible provider.

```bash
git clone <this repo> hypotenium && cd hypotenium
cp .env.example .env          # put your key into LLM_API_KEY
docker compose up -d
```

Open http://localhost:3000 and sign in as `admin@example.com` / `admin12345` (change both in `.env` before exposing the service to anyone). Then go to **Супердоступ → Исследования**, add a study with a few files, wait for processing to finish, and ask a question.

The first start builds two images (~3–5 minutes). Data lives in Docker volumes (`pgdata`, `files-data`) and survives restarts and rebuilds.

## Models

Everything about models is in [`config/models.yaml`](config/models.yaml). The defaults use OpenAI (`gpt-4o-mini`, `text-embedding-3-small`, `whisper-1`) with the key from `LLM_API_KEY`. To use something else, edit the slot — the file is re-read on the next request, no restart needed:

| Slot | What it does | Requirements |
|---|---|---|
| `chat` | answers, query rewriting, chat titles | any chat-completions endpoint with streaming |
| `embedding` | vector index for retrieval | any embeddings endpoint; dimension is detected automatically |
| `transcription` | audio/video → text | an `/audio/transcriptions` endpoint; `verbose_json` segments give timecodes |

Commented examples in the file cover OpenRouter-style aggregators, local Ollama, GigaChat's direct API (it has its own OAuth flow, supported natively) and the local GigaAM transcriber. Keep your personal setup in `config/models.local.yaml` — it is git-ignored and takes precedence when present.

**Local transcription.** Set `COMPOSE_PROFILES=gigaam` in `.env` (or run `docker compose --profile gigaam up -d`) and point the `transcription` slot at `http://transcriber:9000/v1`. The image is heavy (PyTorch CPU + model weights), which is why it is off by default.

## How it works

```
upload ──► worker: extract (pypdf / csv / ffmpeg + ASR) ──► chunk ──► embed ──► pgvector
question ──► rewrite with history ──► vector search (+ soft date filter) ──► LLM with numbered sources ──► stream with [n] citations ──► citation snapshots saved
```

- **Backend:** FastAPI, SQLAlchemy 2 (async), PostgreSQL 16 + pgvector, arq workers on Redis. SSE streaming for answers.
- **Frontend:** Next.js 15 (App Router), plain CSS, no UI framework.
- **Ingestion:** page-aware PDF extraction with boilerplate stripping, encoding/delimiter detection for CSV, ffmpeg audio extraction and segmentation for media, garbage-chunk filtering, per-file notes prepended to embedding context.
- **Citations:** the model cites positions in its source list; the server renumbers them into sequential chat numbers on the fly and stores a snapshot (study, file, locator, full chunk text) with every message.

| Service | Port | Role |
|---|---|---|
| `web` | 3000 | Next.js frontend |
| `api` | 8000 | FastAPI backend, Swagger at `/docs` |
| `worker` | — | background processing (transcription, embeddings, reindex) |
| `postgres` | 5432 | PostgreSQL + pgvector |
| `redis` | — | job queue |
| `transcriber` | 127.0.0.1:9000 | optional local GigaAM ASR (`gigaam` profile) |

The full product spec (in Russian) with decisions, threat model and the iteration-2 plan is in [docs/TZ.md](docs/TZ.md).

## Roles and access

- The first admin is created from `ADMIN_EMAIL` / `ADMIN_PASSWORD`.
- New users self-register with email + password and wait until an admin approves them.
- Admins manage studies, users and models; everyone approved can chat and see all studies (it is a shared knowledge base).

## Experimental: chat tools

The chat has a small module system for tools that run their own scenario on top of RAG. The first one, **research task briefing**, kicks in when the knowledge base has no answer: it offers to brief a new study, asks the customer one question at a time, validates hypotheses against NN/g criteria, proposes the methodology part itself and saves the task as Markdown into `tasks/`. It is off by default (`tools.research_task: false` in `config/models.yaml`) while the flow is being polished — turn it on to try it.

## Deploying beyond localhost

`docker compose up` is enough for a laptop or an internal server. Before exposing the service:

1. Set a random `JWT_SECRET` and a real `ADMIN_PASSWORD` in `.env` (the API logs a warning while defaults are in use).
2. Put both services behind HTTPS (any reverse proxy) and set `FRONTEND_ORIGIN` / `NEXT_PUBLIC_API_URL` to the public URLs, then rebuild `web` (the API URL is baked in at build time).
3. Add rate limiting on `/auth/login` at the proxy.
4. If you use GigaChat with `verify_ssl: false`, install the Russian NUC root certificates into the image instead.
5. Uploaded files are stored unencrypted in the `files-data` volume — decide whether you need at-rest encryption.

Nothing leaves your infrastructure except the requests you configure: chat and embedding calls go to the providers in `models.yaml`, and with the local transcriber audio is processed on your machine.

## Roadmap

- Hybrid search (BM25 + vectors, reciprocal rank fusion) for exact numbers, years and product names.
- Chart generation from raw CSV data in answers.
- Citation verification pass to catch paraphrase-as-quote.
- Finishing the research task briefing tool.
- English UI / i18n.
- VLM-based parsing of PDFs with charts and complex layouts.

## Contributing

Issues and pull requests are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md). The codebase has Russian comments and UI strings; contributions in English or Russian are both fine.

## License

[MIT](LICENSE).
