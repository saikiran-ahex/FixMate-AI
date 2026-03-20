from __future__ import annotations

import logging
import time
from typing import Any

from flask import Flask, jsonify, request
from werkzeug.utils import secure_filename

from agents import SupportOrchestrator
from services import DatabaseService
from services.chat_support import ChatSummaryService
from settings import Settings
from utils import configure_logging, get_request_id, get_logger, log_event, set_request_id

settings = Settings()
configure_logging(settings.log_level)
logger = get_logger("fixmate.api")

app = Flask(__name__)
database = DatabaseService(settings=settings)
orchestrator = SupportOrchestrator(database=database)
summary_service = ChatSummaryService(gateway=orchestrator.gateway, database=database)


@app.before_request
def before_request_logging() -> None:
    request_id = request.headers.get("X-Request-ID")
    set_request_id(request_id)
    request._fixmate_start_time = time.perf_counter()
    log_event(
        logger,
        logging.INFO,
        "http_request_started",
        method=request.method,
        path=request.path,
        remote_addr=request.remote_addr,
    )


@app.after_request
def after_request_logging(response):
    started = getattr(request, "_fixmate_start_time", None)
    duration_ms = round((time.perf_counter() - started) * 1000, 2) if started else None
    response.headers["X-Request-ID"] = get_request_id()
    log_event(
        logger,
        logging.INFO,
        "http_request_completed",
        method=request.method,
        path=request.path,
        status_code=response.status_code,
        duration_ms=duration_ms,
    )
    return response


@app.errorhandler(Exception)
def handle_exception(error: Exception):
    log_event(logger, logging.ERROR, "http_request_unhandled_exception", error=str(error), path=request.path)
    return jsonify({"error": "internal server error", "request_id": get_request_id()}), 500


@app.get("/api/health")
def health() -> tuple[object, int]:
    return jsonify(
        {
            "status": "ok",
            "service": "FixMate AI API",
            "llm_provider": "openai",
            "database": "postgresql",
            "semantic_kernel_enabled": orchestrator.gateway.enabled,
            "semantic_kernel_status": orchestrator.gateway.status,
            "vector_store": "qdrant",
            "qdrant_configured": orchestrator.knowledge_base.configured,
            "qdrant_initialized": orchestrator.knowledge_base.enabled,
            "log_level": settings.log_level,
        }
    ), 200


@app.post("/api/auth/register")
def register() -> tuple[object, int]:
    payload = request.get_json(force=True)
    name = payload.get("name", "").strip()
    email = payload.get("email", "").strip().lower()
    password = payload.get("password", "")
    if not name or not email or not password:
        return jsonify({"error": "name, email, and password are required", "request_id": get_request_id()}), 400

    try:
        user = database.register_user(name, email, password)
    except Exception as error:
        if "duplicate" in str(error).lower() or "unique" in str(error).lower():
            return jsonify({"error": "email already registered", "request_id": get_request_id()}), 409
        raise

    token = database.create_session(user)
    return jsonify({"token": token, "user": user}), 201


@app.post("/api/auth/login")
def login() -> tuple[object, int]:
    payload = request.get_json(force=True)
    role = payload.get("role", "user")
    identifier = payload.get("identifier", "").strip()
    password = payload.get("password", "")

    if role == "admin":
        user = database.authenticate_admin(identifier, password)
    else:
        user = database.authenticate_user(identifier, password)

    if not user:
        return jsonify({"error": "invalid credentials", "request_id": get_request_id()}), 401

    token = database.create_session(user)
    return jsonify({"token": token, "user": user}), 200


@app.get("/api/auth/me")
def me() -> tuple[object, int]:
    session = _require_session()
    if not session:
        return jsonify({"error": "unauthorized", "request_id": get_request_id()}), 401
    return jsonify({"user": _session_to_user(session)}), 200


@app.get("/api/chat-threads")
def list_threads() -> tuple[object, int]:
    session = _require_user_session()
    if not session:
        return jsonify({"error": "unauthorized", "request_id": get_request_id()}), 401
    threads = database.list_chat_threads(session["user_id"])
    return jsonify({"threads": threads}), 200


@app.post("/api/chat-threads")
def create_thread() -> tuple[object, int]:
    session = _require_user_session()
    if not session:
        return jsonify({"error": "unauthorized", "request_id": get_request_id()}), 401
    payload = request.get_json(silent=True) or {}
    title = payload.get("title", "New chat")
    thread = database.create_chat_thread(session["user_id"], title)
    return jsonify({"thread": thread}), 201


@app.get("/api/chat-threads/<thread_id>")
def get_thread(thread_id: str) -> tuple[object, int]:
    session = _require_user_session()
    if not session:
        return jsonify({"error": "unauthorized", "request_id": get_request_id()}), 401
    thread = database.get_chat_thread(session["user_id"], thread_id)
    if not thread:
        return jsonify({"error": "thread not found", "request_id": get_request_id()}), 404
    return jsonify(_build_thread_payload(thread)), 200


@app.post("/api/chat-threads/<thread_id>/messages")
async def send_thread_message(thread_id: str) -> tuple[object, int]:
    session = _require_user_session()
    if not session:
        return jsonify({"error": "unauthorized", "request_id": get_request_id()}), 401
    thread = database.get_chat_thread(session["user_id"], thread_id)
    if not thread:
        return jsonify({"error": "thread not found", "request_id": get_request_id()}), 404

    payload = request.get_json(force=True)
    message = payload.get("message", "").strip()
    if not message:
        return jsonify({"error": "message is required", "request_id": get_request_id()}), 400

    database.append_message(thread_id, "user", message)
    database.update_thread_title_if_default(thread_id, message)

    if thread.get("active_conversation_id") and thread.get("active_input_key"):
        result = await orchestrator.continue_conversation_async(
            thread["active_conversation_id"],
            {thread["active_input_key"]: message},
        )
    else:
        result = await orchestrator.handle_query_async(message)

    assistant_text = _result_to_text(result)
    database.append_message(thread_id, "assistant", assistant_text, result.get("agent"))
    database.update_thread_state(thread_id, result.get("conversation_id"), result.get("input_key"))
    summary = await summary_service.summarize_if_needed(thread_id)

    refreshed_thread = database.get_chat_thread(session["user_id"], thread_id)
    response_payload = {
        "result": result,
        "assistant_text": assistant_text,
        "summary_created": summary is not None,
        "thread": refreshed_thread,
    }
    return jsonify(response_payload), 200


@app.get("/api/admin/files")
def list_admin_files() -> tuple[object, int]:
    session = _require_admin_session()
    if not session:
        return jsonify({"error": "unauthorized", "request_id": get_request_id()}), 401
    return jsonify({"files": database.list_uploaded_files()}), 200


@app.post("/api/admin/files")
async def upload_admin_file() -> tuple[object, int]:
    session = _require_admin_session()
    if not session:
        return jsonify({"error": "unauthorized", "request_id": get_request_id()}), 401
    if "file" not in request.files:
        return jsonify({"error": "file is required", "request_id": get_request_id()}), 400

    uploaded = request.files["file"]
    filename = secure_filename(uploaded.filename or "")
    if not filename:
        return jsonify({"error": "invalid file name", "request_id": get_request_id()}), 400

    file_bytes = uploaded.read()
    record = database.save_uploaded_file(filename, uploaded.mimetype, file_bytes, session["name"])
    reindex_result = await orchestrator.knowledge_base.reindex()
    return jsonify({"file": record, "reindex": reindex_result}), 201


@app.delete("/api/admin/files/<int:file_id>")
async def delete_admin_file(file_id: int) -> tuple[object, int]:
    session = _require_admin_session()
    if not session:
        return jsonify({"error": "unauthorized", "request_id": get_request_id()}), 401
    deleted = database.delete_uploaded_file(file_id)
    if not deleted:
        return jsonify({"error": "file not found", "request_id": get_request_id()}), 404
    reindex_result = await orchestrator.knowledge_base.reindex()
    return jsonify({"deleted": deleted, "reindex": reindex_result}), 200


@app.post("/api/admin/reindex")
async def admin_reindex() -> tuple[object, int]:
    session = _require_admin_session()
    if not session:
        return jsonify({"error": "unauthorized", "request_id": get_request_id()}), 401
    result = await orchestrator.knowledge_base.reindex()
    return jsonify(result), 200


def _require_session() -> dict[str, Any] | None:
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None
    token = auth_header.split(" ", 1)[1].strip()
    if not token:
        return None
    return database.get_session(token)


def _require_user_session() -> dict[str, Any] | None:
    session = _require_session()
    if session and session["role"] == "user":
        return session
    return None


def _require_admin_session() -> dict[str, Any] | None:
    session = _require_session()
    if session and session["role"] == "admin":
        return session
    return None


def _session_to_user(session: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": session.get("user_id"),
        "name": session.get("name"),
        "email": session.get("email"),
        "role": session.get("role"),
    }


def _build_thread_payload(thread: dict[str, Any]) -> dict[str, Any]:
    return {
        "thread": thread,
        "summaries": database.list_chat_summaries(thread["id"]),
        "messages": database.list_recent_messages(thread["id"], limit=settings.summary_batch_size),
    }


def _result_to_text(result: dict[str, Any]) -> str:
    if result.get("response"):
        return str(result["response"])
    question = result.get("questions", [None])[0]
    if question:
        return f"{result.get('message', '')}\n\n{question}".strip()
    return str(result.get("message", ""))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
