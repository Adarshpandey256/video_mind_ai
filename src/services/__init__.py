"""Backend services for VideoMind AI.

Step 1: provider access, generation and diagnostics.
Step 2: media sources, inspection/extraction, URL acquisition, Whisper
transcription, transcript storage and pipeline orchestration.
Step 3: bounded summarisation, transcript chunking, timestamp validation and
summary storage.
"""

from src.services.chunk_summarizer import ChunkSummarizer
from src.services.health_service import run_health_checks, summarise_checks
from src.services.llm_service import LLMService
from src.services.nvidia_client import (
    MISSING_CREDENTIALS_MESSAGE,
    NvidiaConfigurationError,
    NvidiaError,
    NvidiaRequestError,
    create_nvidia_client,
    describe_client_config,
    get_nvidia_client,
    is_nvidia_configured,
)
from src.services.source_service import (
    SourceValidationError,
    StoredUpload,
    cleanup_stored_file,
    save_upload,
    validate_upload,
)
from src.services.summarization_service import (
    SummarizationService,
    summarize_transcript,
)
from src.services.summary_generation import (
    SummaryGenerationError,
    SummaryJsonClient,
)
from src.services.summary_merger import SummaryMerger
from src.services.summary_storage import (
    SummaryNotFoundError,
    SummaryStorage,
    SummaryStorageError,
)
from src.services.timestamp_service import TimestampValidator, validator_for
from src.services.transcript_preprocessor import (
    TranscriptChunk,
    TranscriptPreprocessor,
    estimate_tokens,
)
from src.services.transcript_service import TranscriptService, video_id_for
from src.services.transcript_storage import (
    TranscriptNotFoundError,
    TranscriptStorage,
    TranscriptStorageError,
)
from src.services.transcription_service import (
    TranscriptionService,
    TranscriptionServiceError,
    WhisperUnavailableError,
    unload_whisper_models,
)
from src.services.url_ingestion_service import (
    BlockedDestinationError,
    DownloadFailedError,
    DownloadedMedia,
    DownloadTooLargeError,
    DownloadTimeoutError,
    RecordingTooLongError,
    UnsupportedUrlError,
    UrlIngestionError,
    build_url_source,
    download_media,
    validate_url,
)
from src.services.video_pipeline import (
    JobRegistry,
    VideoPipeline,
    job_key_for,
)

__all__ = [
    "MISSING_CREDENTIALS_MESSAGE",
    "BlockedDestinationError",
    "DownloadFailedError",
    "DownloadTooLargeError",
    "DownloadTimeoutError",
    "DownloadedMedia",
    "JobRegistry",
    "LLMService",
    "NvidiaConfigurationError",
    "NvidiaError",
    "NvidiaRequestError",
    "ChunkSummarizer",
    "RecordingTooLongError",
    "SourceValidationError",
    "StoredUpload",
    "SummarizationService",
    "SummaryGenerationError",
    "SummaryJsonClient",
    "SummaryMerger",
    "SummaryNotFoundError",
    "SummaryStorage",
    "SummaryStorageError",
    "TimestampValidator",
    "TranscriptChunk",
    "TranscriptNotFoundError",
    "TranscriptPreprocessor",
    "TranscriptService",
    "TranscriptStorage",
    "TranscriptStorageError",
    "TranscriptionService",
    "TranscriptionServiceError",
    "UnsupportedUrlError",
    "UrlIngestionError",
    "VideoPipeline",
    "WhisperUnavailableError",
    "build_url_source",
    "cleanup_stored_file",
    "create_nvidia_client",
    "describe_client_config",
    "download_media",
    "estimate_tokens",
    "get_nvidia_client",
    "is_nvidia_configured",
    "job_key_for",
    "run_health_checks",
    "save_upload",
    "summarise_checks",
    "summarize_transcript",
    "unload_whisper_models",
    "validate_upload",
    "validate_url",
    "validator_for",
    "video_id_for",
]
