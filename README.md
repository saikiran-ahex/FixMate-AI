# FixMate-AI

FixMate-AI is a multi-agent appliance support system with a React frontend and a Python backend powered by Semantic Kernel, OpenAI, Qdrant, PostgreSQL, and structured logging.

## Core product flows

- Users can register and log in.
- Admin logs in with credentials stored in environment variables.
- Admin can upload files, list them, delete them, and reindex the vector library.
- Users can ask questions against both appliance data and admin-uploaded files.
- Users have persistent chat history with a left-side chat list and new chat flow.
- Every 20 unsummarized messages are condensed into a stored summary so the app can load summaries plus the latest turns.

## Architecture

- `backend/` contains the Flask API, Semantic Kernel orchestration, auth/chat/file persistence, vector indexing, tests, maintenance scripts, and logging.
- `frontend/` contains the React UI for auth, admin, and user chat workspace.
- `docker-compose.yml` runs PostgreSQL, Qdrant, the backend API, and the frontend together.

## Run with Docker

Create a root `.env` file or export environment variables with at least:

```bash
OPENAI_API_KEY=your_key_here
OPENAI_CHAT_MODEL=gpt-5-mini
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
POSTGRES_DB=fixmate
POSTGRES_USER=fixmate
POSTGRES_PASSWORD=fixmate
DATABASE_URL=postgresql://fixmate:fixmate@postgres:5432/fixmate
ADMIN_USERNAME=admin
ADMIN_PASSWORD=change-me-admin
LOG_LEVEL=INFO
```

Then start everything:

```bash
docker compose up --build
```

Open:

- Frontend: `http://localhost:3000`
- Backend health: `http://localhost:5000/api/health`
- PostgreSQL: `localhost:5432`
- Qdrant: `http://localhost:6333/dashboard`

## Logs

```bash
docker compose logs -f backend
```

## API highlights

- `POST /api/auth/register`
- `POST /api/auth/login`
- `GET /api/auth/me`
- `GET /api/chat-threads`
- `POST /api/chat-threads`
- `GET /api/chat-threads/<thread_id>`
- `POST /api/chat-threads/<thread_id>/messages`
- `GET /api/admin/files`
- `POST /api/admin/files`
- `DELETE /api/admin/files/<id>`
- `POST /api/admin/reindex`
