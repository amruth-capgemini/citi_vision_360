# Deployment

Build from the repository root (backend is the build context):

```sh
docker build -t citi-api ./backend
docker run --rm -p 8080:8080 --env-file backend/.env -e PORT=8080 -e CITI_API_HOST=0.0.0.0 citi-api
```

Supply secrets at runtime through an untracked env file or Cloud Run Secret Manager bindings; they are excluded from the image.

Runtime configuration:

- `PORT`: Cloud Run supplies this; defaults to `8000` locally. Explicit `--port` wins.
- `CITI_API_HOST`: defaults to `127.0.0.1` locally; the image sets `0.0.0.0`. Explicit `--host` wins.
- `CITI_CORS_ORIGINS`: comma-separated frontend origins, e.g. `https://example.vercel.app, https://app.example.com`. Whitespace is trimmed; omit trailing slashes. Defaults to `http://localhost:5173,http://127.0.0.1:5173` when absent. Wildcard `*` is rejected.
- `CITI_PG_READER_DSN`: required PostgreSQL connection string for the existing reader role. `CITI_PG_DSN` is only needed for separate administrative/loading tools, not API reads.
- `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`, `NEO4J_DATABASE`: required for default Bolt transport. For optional `NEO4J_TRANSPORT=http`, supply `NEO4J_QUERY_API_URL` unless the URL can be derived from an Aura URI.
- `OPENAI_API_KEY`: required for OpenAI; optional `OPENAI_MODEL` selects the model. Alternatively set `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`, and `AZURE_OPENAI_DEPLOYMENT` (or `AZURE_OPENAI_CHAT_DEPLOYMENT`); `AZURE_OPENAI_API_VERSION` is optional.

Cloud Run must run a Linux amd64 image, retain the container host setting, and provide network access to externally provisioned PostgreSQL, Neo4j (including the populated business graph/catalog), and OpenAI or Azure OpenAI. Configure database TLS, credentials and any private-network connectivity for those services. This image does not provision databases or load data. Set a request timeout sufficient for agent responses and SSE streams. Browser access assumes the service permits unauthenticated invocation; the API currently has no application authentication. Sessions and dashboard caches live in process memory and are lost on restarts; multiple instances do not share sessions.

For the separately hosted frontend, set `VITE_API_URL=https://example-backend.run.app` in the frontend host's build environment, then run `npm run build` in `frontend` and publish `dist`. The value is public build-time configuration, never a secret. Both fetch and EventSource calls use that base plus `/api/...`; trailing slashes are removed. With the variable absent, relative `/api` URLs remain unchanged. Add the actual frontend origin to the backend's `CITI_CORS_ORIGINS`. Rebuild the frontend when its API URL changes.

Local Vite development still proxies `/api` to `http://127.0.0.1:8000`; `CITI_API_URL` can override that dev proxy target.
