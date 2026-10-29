"""Ask Your Video - the Step 4 RAG chatbot page.

Like the transcription and summarisation pages, this file is deliberately thin:
it chooses the active video, shows the index state, calls :class:`RagService`
and renders what came back. No prompt text, no embedding call and no ChromaDB
access lives here.

State rules:

* chat history is stored **per video id**, so switching videos cannot leak one
  conversation into another;
* nothing is embedded or generated on page load - the index is built and
  questions are asked only when the user acts;
* a failed answer is reported as a failure: the chat never shows invented text,
  and the previous messages are kept so the user can retry;
* the index status shown is the real one (registry + vector store), never an
  optimistic guess.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from typing import Any

import streamlit as st

from config.settings import Settings, get_settings
from src.models.rag_schemas import (
    ChatTurn,
    IndexInfo,
    IndexResult,
    IndexStatus,
    IndexOutcome,
    RagAnswer,
    RagCitation,
    RagResult,
    RetrievedSource,
)
from src.models.schemas import TranscriptMetadata, TranscriptRecord
from src.rag.service import RagService
from src.services.summary_storage import SummaryStorage, SummaryStorageError
from src.services.transcript_service import TranscriptService
from src.services.transcript_storage import TranscriptStorageError
from src.ui import components as ui
from src.ui.transcription_view import TRANSCRIPT_ID_KEY
from src.utils.time_utils import format_duration

logger = logging.getLogger(__name__)

NOTICE_KEY = "rag_notice"
VIDEO_CHOICE_KEY = "rag_video_choice"
RUNNING_KEY = "rag_running"
DELETE_CONFIRM_KEY = "rag_confirm_delete"
FAILED_QUESTION_KEY = "rag_failed_question"
HISTORY_PREFIX = "rag_chat_"
SUGGESTED_QUESTIONS: tuple[str, ...] = (
    "What is this video mainly about?",
    "What are the most important points?",
    "Explain the main concept in simple language.",
    "What examples does the speaker give?",
    "What conclusions does the speaker reach?",
    "What does the speaker say about the first minute?",
)
PRIMARY_SUGGESTIONS = SUGGESTED_QUESTIONS[:3]


# --------------------------------------------------------------------------- state
def initialise_state() -> None:
    """Create the session keys used by the chatbot page."""
    defaults: dict[str, Any] = {
        NOTICE_KEY: None,
        VIDEO_CHOICE_KEY: None,
        RUNNING_KEY: False,
        DELETE_CONFIRM_KEY: False,
        FAILED_QUESTION_KEY: None,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def history_key(video_id: str) -> str:
    """Session key of one video's conversation (never shared between videos)."""
    return f"{HISTORY_PREFIX}{video_id}"


def get_history(state: Any, video_id: str) -> list[dict[str, Any]]:
    """Return the stored messages for one video (empty when there are none)."""
    messages = state.get(history_key(video_id))
    return list(messages) if isinstance(messages, list) else []


def set_history(state: Any, video_id: str, messages: Sequence[dict[str, Any]]) -> None:
    """Replace one video's conversation."""
    state[history_key(video_id)] = list(messages)


def clear_history(state: Any, video_id: str) -> None:
    """Forget one video's conversation only."""
    state[history_key(video_id)] = []


def chat_turns(messages: Sequence[dict[str, Any]]) -> list[ChatTurn]:
    """Convert rendered messages into the compact history sent to the model."""
    turns: list[ChatTurn] = []
    pending_question: str | None = None
    for message in messages:
        role = message.get("role")
        content = str(message.get("content") or "")
        if role == "user":
            pending_question = content
        elif role == "assistant" and pending_question is not None:
            turns.append(ChatTurn(question=pending_question, answer=content))
            pending_question = None
    return turns


def _set_notice(level: str, title: str, message: str) -> None:
    st.session_state[NOTICE_KEY] = {"level": level, "title": title, "message": message}


def _render_notice() -> None:
    notice = st.session_state.get(NOTICE_KEY)
    if not notice:
        return
    st.session_state[NOTICE_KEY] = None
    ui.render_status_banner(
        {"success": "ok", "warning": "warning", "error": "error"}.get(
            notice.get("level"), "warning"
        ),
        notice.get("message", ""),
        notice.get("title"),
    )


# --------------------------------------------------------------------------- services
def service_for(settings: Settings) -> RagService:
    """Create the RAG service (a seam for tests; the page never builds one inline)."""
    return RagService(settings)


def stored_videos(settings: Settings) -> list[TranscriptMetadata]:
    """List the transcripts the chatbot can talk about."""
    try:
        return TranscriptService(settings).list_transcripts()
    except Exception:  # noqa: BLE001 - listing must never break the page
        logger.warning("Stored transcripts could not be listed for the chatbot.")
        return []


def load_transcript(settings: Settings, transcript_id: str | None) -> TranscriptRecord | None:
    """Load one transcript, reporting (not raising) a storage problem."""
    if not transcript_id:
        return None
    try:
        return TranscriptService(settings).load(transcript_id)
    except (TranscriptStorageError, ValueError) as error:
        logger.warning("Transcript could not be loaded: %s", type(error).__name__)
        _set_notice(
            "error",
            "Transcript unavailable",
            f"{error} Re-run transcription on the Home page if the file is gone.",
        )
        return None


def video_label(metadata: TranscriptMetadata) -> str:
    """Readable selector label for a video."""
    title = (metadata.title or metadata.original_filename or "Untitled recording").strip()
    created = metadata.created_at.astimezone().strftime("%Y-%m-%d %H:%M")
    duration = format_duration(metadata.duration_seconds) if metadata.duration_seconds else "unknown length"
    return f"{title} · {created} · {duration}"


def has_summary(settings: Settings, transcript: TranscriptRecord) -> bool:
    """True when a stored Step 3 summary exists for this transcript."""
    if not transcript.transcript_id:
        return False
    try:
        return (
            SummaryStorage(settings.summary_dir).latest_for_transcript(transcript.transcript_id)
            is not None
        )
    except SummaryStorageError:
        return False


def build_index(
    settings: Settings,
    transcript: TranscriptRecord,
    *,
    rebuild: bool = False,
    service: RagService | None = None,
    on_stage: Callable[[str], None] | None = None,
) -> IndexResult:
    """Build or reuse the video's vector index."""
    active = service or service_for(settings)
    return active.index_transcript(transcript, rebuild=rebuild, on_stage=on_stage)


def ask_question(
    settings: Settings,
    transcript: TranscriptRecord,
    question: str,
    history: Sequence[ChatTurn] = (),
    *,
    service: RagService | None = None,
    on_stage: Callable[[str], None] | None = None,
) -> RagResult:
    """Answer one question through the service (the only model call on this page)."""
    active = service or service_for(settings)
    return active.ask(question, transcript, history=history, on_stage=on_stage)


def delete_index(settings: Settings, transcript: TranscriptRecord) -> bool:
    """Delete the active video's index."""
    return service_for(settings).delete_index(transcript.video_id)


# --------------------------------------------------------------------------- rendering
def render_chat_page(settings: Settings) -> None:
    """Render the chatbot: video picker, index controls and the conversation."""
    initialise_state()
    ui.section_header(
        "Ask Your Video",
        "Ask questions, explore important moments, and get answers grounded in your "
        "video transcript.",
    )
    _render_notice()

    videos = stored_videos(settings)
    if not videos:
        ui.render_status_banner(
            "warning",
            "No transcript is available yet. Upload a video or audio file on the Home page "
            "and run transcription first - the chatbot answers from that transcript.",
            "Transcript required",
        )
        if st.button("Go to the transcription workflow", key="rag_go_home"):
            from src.ui.layout import go_to_page

            go_to_page("home")
        return

    transcript = _render_video_selector(settings, videos)
    if transcript is None:
        return

    service = service_for(settings)
    info = service.index_status(transcript)
    _render_video_overview(settings, transcript, info)
    _render_index_controls(settings, transcript, info)

    if not info.is_ready:
        ui.render_status_banner(
            "pending",
            "Build the index for this video, then ask a question. The transcript itself is "
            "never changed by indexing.",
            "Questions are disabled until the index is ready",
        )
        return

    _render_conversation(settings, transcript)


def _render_video_selector(
    settings: Settings, videos: list[TranscriptMetadata]
) -> TranscriptRecord | None:
    """Render the video picker and load the chosen transcript."""
    identifiers = [item.transcript_id for item in videos if item.transcript_id]
    if not identifiers:
        ui.render_status_banner(
            "error",
            "Stored transcripts could not be read. Check that the data directory is writable.",
            "Transcripts unavailable",
        )
        return None

    active_id = st.session_state.get(TRANSCRIPT_ID_KEY)
    if st.session_state.get(VIDEO_CHOICE_KEY) not in identifiers:
        st.session_state[VIDEO_CHOICE_KEY] = (
            active_id if active_id in identifiers else identifiers[0]
        )

    labels = {item.transcript_id: video_label(item) for item in videos if item.transcript_id}
    chosen = st.selectbox(
        "Video",
        options=identifiers,
        format_func=lambda value: labels.get(value, value),
        key=VIDEO_CHOICE_KEY,
        help="Every conversation below belongs to this recording only.",
    )
    return load_transcript(settings, chosen)


def _render_video_overview(
    settings: Settings, transcript: TranscriptRecord, info: IndexInfo
) -> None:
    """Show what the chatbot knows about the active video."""
    summary_ready = has_summary(settings, transcript)
    ui.render_key_values(
        [
            ("Video", transcript.title or "Untitled recording"),
            ("Source", transcript.source_reference or transcript.original_filename or "unknown"),
            ("Transcript", f"{transcript.segment_count} segment(s) · {transcript.language or 'language not reported'}"),
            ("Summary", "available" if summary_ready else "not generated yet"),
            ("Index", info.describe()),
        ]
    )
    if info.status is IndexStatus.READY:
        ui.render_status_banner(
            "ok",
            f"{info.chunk_count} chunk(s) indexed with {info.embedding_model or 'the configured model'}"
            + (f" · built {info.indexed_at.astimezone():%Y-%m-%d %H:%M}" if info.indexed_at else ""),
            "Index ready",
        )
    elif info.status is IndexStatus.STALE:
        ui.render_status_banner("warning", info.describe(), "Index out of date")
    elif info.status is IndexStatus.EMBEDDING_MISMATCH:
        ui.render_status_banner("warning", info.describe(), "Embedding model changed")
    elif info.status is IndexStatus.UNAVAILABLE:
        ui.render_status_banner("error", info.describe(), "Vector store unavailable")
    else:
        ui.render_status_banner(
            "pending",
            "This video has not been indexed yet. Indexing embeds the transcript locally and "
            "stores the vectors in ChromaDB; it does not call the AI provider.",
            "Not indexed",
        )


def _render_index_controls(
    settings: Settings, transcript: TranscriptRecord, info: IndexInfo
) -> None:
    """Build, rebuild or delete the active video's index."""
    ui.section_header("Index", "Local embeddings and ChromaDB - rebuilt only when you ask.")
    rebuilding = info.status in {
        IndexStatus.STALE,
        IndexStatus.EMBEDDING_MISMATCH,
    }
    label = "Rebuild index" if info.is_ready or rebuilding else "Build index"

    column_action, column_delete, column_hint = st.columns([1, 1, 2])
    with column_action:
        clicked = st.button(
            label,
            key="rag_build_index",
            type="primary",
            disabled=st.session_state.get(RUNNING_KEY, False),
        )
    with column_delete:
        can_delete = info.status is not IndexStatus.NOT_INDEXED
        delete_clicked = st.button(
            "Delete index",
            key="rag_delete_index",
            disabled=not can_delete or st.session_state.get(RUNNING_KEY, False),
        )
    with column_hint:
        st.caption(
            "Indexing reads the stored transcript, creates embeddings on this computer and "
            "writes them to the local vector database. Nothing is sent to NVIDIA."
        )

    if clicked:
        _run_index_build(settings, transcript, rebuild=info.is_ready or rebuilding)

    if delete_clicked:
        st.session_state[DELETE_CONFIRM_KEY] = True

    if st.session_state.get(DELETE_CONFIRM_KEY):
        st.warning(
            "Deleting the index removes this video's embeddings and chunk metadata from the "
            "local vector database. The transcript and summary are kept."
        )
        confirm_column, cancel_column, _ = st.columns([1, 1, 2])
        with confirm_column:
            confirmed = st.button("Delete permanently", key="rag_delete_confirm", type="primary")
        with cancel_column:
            if st.button("Cancel", key="rag_delete_cancel"):
                st.session_state[DELETE_CONFIRM_KEY] = False
                st.rerun()
        if confirmed:
            st.session_state[DELETE_CONFIRM_KEY] = False
            try:
                removed = delete_index(settings, transcript)
            except Exception:  # noqa: BLE001 - reported safely below
                logger.exception("Deleting the index failed unexpectedly.")
                _set_notice("error", "Index not deleted", "The index could not be deleted. See logs/app.log.")
            else:
                if removed:
                    clear_history(st.session_state, transcript.video_id)
                    _set_notice(
                        "success",
                        "Index deleted",
                        "The embeddings for this video were removed. The transcript is untouched.",
                    )
                else:
                    _set_notice("warning", "Nothing to delete", "This video had no stored index.")
            st.rerun()


def _run_index_build(
    settings: Settings, transcript: TranscriptRecord, *, rebuild: bool
) -> None:
    """Build the index with real stage messages, then rerun to show the new state."""
    if st.session_state.get(RUNNING_KEY):
        return
    st.session_state[RUNNING_KEY] = True
    progress = st.empty()
    try:
        with st.spinner("Preparing the index..."):
            result = build_index(
                settings,
                transcript,
                rebuild=rebuild,
                on_stage=lambda label: progress.caption(label),
            )
    except Exception:  # noqa: BLE001 - the page must survive an unexpected failure
        logger.exception("Indexing failed unexpectedly.")
        _set_notice(
            "error",
            "Indexing failed",
            "The index could not be built. See logs/app.log for the technical details.",
        )
    else:
        if result.succeeded:
            created = result.outcome is IndexOutcome.SUCCESS
            _set_notice(
                "success" if created else "warning",
                "Index ready" if created else "Existing index reused",
                result.message,
            )
        else:
            _set_notice("error", "Index not built", result.message)
        logger.info("Index run finished (outcome=%s).", result.outcome.value)
    finally:
        st.session_state[RUNNING_KEY] = False
        progress.empty()
    st.rerun()


# --------------------------------------------------------------------------- chat
def _render_conversation(settings: Settings, transcript: TranscriptRecord) -> None:
    """Render the message history and the input box for the active video."""
    ui.section_header(
        "Conversation",
        "Answers are generated only from retrieved parts of this recording's transcript.",
    )
    messages = get_history(st.session_state, transcript.video_id)
    failed_question = st.session_state.get(FAILED_QUESTION_KEY)
    if isinstance(failed_question, dict) and failed_question.get("video_id") == transcript.video_id:
        _render_failed_question(settings, transcript, failed_question)

    if not messages:
        st.info(
            "Ask about anything the speaker covered. Follow-up questions work too - the "
            "conversation stays with this video."
        )

    for message in messages:
        _render_message(message)

    if messages:
        _render_history_controls(transcript, messages)

    if not messages:
        _render_suggestions(settings, transcript)

    prompt = st.chat_input(
        "Ask a question about this video",
        key="rag_chat_input",
    )
    if prompt:
        _handle_question(settings, transcript, str(prompt))


def _render_message(message: dict[str, Any]) -> None:
    """Render one stored chat message, including its sources."""
    role = message.get("role", "assistant")
    with st.chat_message("user" if role == "user" else "assistant"):
        st.markdown(str(message.get("content") or ""))
        if role != "assistant":
            return
        citations = message.get("citations") or []
        if citations:
            _render_citations(citations)
        sources = message.get("sources") or []
        if sources:
            with st.expander(f"Retrieved passages ({len(sources)})", expanded=False):
                for source in sources:
                    st.markdown(f"**{source.get('source_ref', '')} · {source.get('label', '')}**")
                    st.caption(source.get("excerpt", ""))
        notes = message.get("notes") or []
        if notes:
            for note in notes:
                st.caption(f"Note: {note}")


def _render_citations(citations: Sequence[dict[str, Any]]) -> None:
    """Render the resolved sources of one answer with their real timestamps."""
    st.markdown("**Sources**")
    for citation in citations:
        reference = citation.get("source_ref", "?")
        label = citation.get("source_name") or "Untitled recording"
        stamp = citation.get("time_label") or "timestamp unavailable"
        st.markdown(f"[{reference}] {label} - {stamp}")
        if citation.get("quote"):
            st.caption(f'"{citation["quote"]}"')


def _render_history_controls(
    transcript: TranscriptRecord, messages: Sequence[dict[str, Any]]
) -> None:
    """Offer a clear-chat action scoped to this video."""
    column_clear, column_hint = st.columns([1, 3])
    with column_clear:
        if st.button("Clear conversation", key="rag_clear_chat"):
            clear_history(st.session_state, transcript.video_id)
            st.session_state[FAILED_QUESTION_KEY] = None
            _set_notice(
                "success",
                "Conversation cleared",
                "Only this video's conversation was cleared; other videos are untouched.",
            )
            st.rerun()
    with column_hint:
        st.caption(f"{len(messages)} message(s) kept for this video only.")


def _render_suggestions(settings: Settings, transcript: TranscriptRecord) -> None:
    """Offer example questions (they run through the real pipeline)."""
    st.caption("Try one of these:")
    columns = st.columns(len(PRIMARY_SUGGESTIONS))
    for index, (column, question) in enumerate(zip(columns, PRIMARY_SUGGESTIONS)):
        with column:
            if st.button(question, key=f"rag_suggest_{index}"):
                _handle_question(settings, transcript, question)


def _render_failed_question(
    settings: Settings, transcript: TranscriptRecord, failed: dict[str, Any]
) -> None:
    """Show a failed question honestly, with an explicit retry."""
    question = str(failed.get("question") or "")
    message = str(failed.get("message") or "")
    st.error(f"That question was not answered: {message}")
    if question and st.button("Retry last question", key="rag_retry"):
        # The question is already the last user message; do not duplicate it.
        st.session_state[FAILED_QUESTION_KEY] = None
        _handle_question(settings, transcript, question, append_user=False)


def _handle_question(
    settings: Settings,
    transcript: TranscriptRecord,
    question: str,
    *,
    append_user: bool = True,
) -> None:
    """Run one question and store the outcome in the video's conversation."""
    cleaned = question.strip()
    if not cleaned:
        return

    messages = get_history(st.session_state, transcript.video_id)
    if append_user:
        messages.append({"role": "user", "content": cleaned})
        set_history(st.session_state, transcript.video_id, messages)

    history = chat_turns(messages[:-1])
    st.session_state[RUNNING_KEY] = True
    progress = st.empty()
    try:
        with st.spinner("Searching the transcript and generating an answer..."):
            result = ask_question(
                settings,
                transcript,
                cleaned,
                history,
                on_stage=lambda label: progress.caption(label),
            )
    except Exception:  # noqa: BLE001 - never lose the conversation to a crash
        logger.exception("Answering a question failed unexpectedly.")
        st.session_state[FAILED_QUESTION_KEY] = {
            "video_id": transcript.video_id,
            "question": cleaned,
            "message": "The request failed unexpectedly. See logs/app.log for the details.",
        }
    else:
        if result.succeeded and result.answer is not None:
            messages.append(_assistant_message(result.answer))
            set_history(st.session_state, transcript.video_id, messages)
            st.session_state[FAILED_QUESTION_KEY] = None
        else:
            st.session_state[FAILED_QUESTION_KEY] = {
                "video_id": transcript.video_id,
                "question": cleaned,
                "message": result.message,
            }
        logger.info("Chat question finished (outcome=%s).", result.outcome.value)
    finally:
        st.session_state[RUNNING_KEY] = False
        progress.empty()
    st.rerun()


def _assistant_message(answer: RagAnswer) -> dict[str, Any]:
    """Convert a validated answer into a stored message."""
    return {
        "role": "assistant",
        "content": answer.answer,
        "citations": [_citation_payload(citation) for citation in answer.citations],
        "sources": [_source_payload(source) for source in answer.sources],
        "notes": list(answer.notes),
        "insufficient_evidence": answer.insufficient_evidence,
    }


def _citation_payload(citation: RagCitation) -> dict[str, Any]:
    """JSON-safe citation for session state."""
    return {
        "source_ref": citation.source_ref,
        "source_name": citation.source_name,
        "document_type": citation.document_type.value,
        "time_label": citation.time_label,
        "start_seconds": citation.start_seconds,
        "end_seconds": citation.end_seconds,
        "quote": citation.quote,
        "chunk_id": citation.chunk_id,
    }


def _source_payload(source: RetrievedSource) -> dict[str, Any]:
    """JSON-safe retrieved passage for session state."""
    return {
        "source_ref": source.source_ref,
        "label": source.label,
        "excerpt": source.excerpt(),
        "chunk_id": source.chunk_id,
        "document_type": source.document_type.value,
        "time_label": source.time_label,
        "start_seconds": source.start_seconds,
        "end_seconds": source.end_seconds,
    }
