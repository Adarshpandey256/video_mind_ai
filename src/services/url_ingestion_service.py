"""Safe URL acquisition for media sources (Step 2).

Every URL is untrusted input. Before any network activity this module:

* requires an ``http``/``https`` scheme (``file:``, ``ftp:``, ``data:`` are refused),
* refuses URLs that carry embedded credentials,
* refuses host names that are obviously local (``localhost``, ``.local``, ``.internal``),
* resolves the host and refuses loopback, private, link-local, multicast, reserved and
  cloud-metadata addresses (basic SSRF protection),
* follows redirects manually, re-validating each destination,
* streams to disk with a byte limit, a timeout and partial-file cleanup.

Two acquisition paths exist: ``direct`` (the URL points at a media file) and
``platform`` (a supported public page handled by yt-dlp, when it is installed).
Private, authenticated or DRM protected material is never bypassed. If a platform
refuses an anonymous request but the user has their *own* authorized access, they
can point ``YTDLP_COOKIES_FILE`` at a cookies.txt export or name their browser in
``YTDLP_COOKIES_FROM_BROWSER``; the application only passes that choice through to
yt-dlp and never reads, stores or transmits the cookies itself.
"""

from __future__ import annotations

import hashlib
import ipaddress
import logging
import re
import socket
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

from config.settings import Settings, get_settings
from src.models.schemas import IngestionKind, SourceType, ValidatedSource
from src.utils.file_utils import (
    ensure_directory,
    human_readable_size,
    normalise_extension,
)

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str], None]

MAX_REDIRECTS = 5
DOWNLOAD_CHUNK_SIZE = 1024 * 1024
CONNECT_TIMEOUT_SECONDS = 15.0
USER_AGENT = "VideoMindAI/0.2 (local ingestion)"

STRATEGY_DIRECT = "direct"
STRATEGY_PLATFORM = "platform"

#: Browsers yt-dlp can read an existing local profile from. Anything else is
#: refused with this list instead of producing a generic provider failure.
SUPPORTED_COOKIE_BROWSERS = (
    "brave",
    "chrome",
    "chromium",
    "edge",
    "firefox",
    "opera",
    "safari",
    "vivaldi",
    "whale",
)

_ALLOWED_SCHEMES = ("http", "https")
_BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "metadata",
        "metadata.google.internal",
        "instance-data",
        "metadata.azure.internal",
    }
)
_BLOCKED_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".home.arpa", ".lan", ".test")

_CONTENT_TYPE_SUFFIXES = {
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/wave": ".wav",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/aac": ".aac",
    "audio/flac": ".flac",
    "audio/x-flac": ".flac",
    "audio/ogg": ".ogg",
    "audio/opus": ".opus",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/quicktime": ".mov",
    "video/x-matroska": ".mkv",
    "video/x-msvideo": ".avi",
    "application/ogg": ".ogg",
}

_CONTENT_DISPOSITION_FILENAME = re.compile(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)\"?")

#: Schemes that must be preserved so they can be rejected with a clear message
#: instead of being mistaken for a host name.
_EXPLICIT_SCHEME_PATTERN = re.compile(
    r"^(?:mailto|data|file|ftps?|ws|wss|javascript|blob|tel|sms):",
    re.IGNORECASE,
)

#: Characters that are never valid unencoded in a URL. Catching them early turns
#: text such as "not a url" into a clear message instead of a download attempt.
_URL_WHITESPACE_PATTERN = re.compile(r"[\s\u0000-\u001f<>\"{}|\\^`]")

#: A DNS host name: one or more labels plus a top-level label, e.g. media.example.com.
#: A bare word such as "example" or "not a url" is not a usable host.
_HOSTNAME_PATTERN = re.compile(
    r"^(?!-)[a-z0-9_-]{1,63}(?<!-)(?:\.(?!-)[a-z0-9_-]{1,63}(?<!-))+\.?$"
)


class UrlIngestionError(RuntimeError):
    """Base class for URL acquisition failures."""


class UnsupportedUrlError(UrlIngestionError):
    """Raised when the URL itself cannot be used."""


class BlockedDestinationError(UrlIngestionError):
    """Raised when a URL resolves to a local, private or reserved destination."""


class DownloadFailedError(UrlIngestionError):
    """Raised when the remote server cannot provide the media."""


class DownloadTooLargeError(UrlIngestionError):
    """Raised when the download exceeds the configured size limit."""


class RecordingTooLongError(UrlIngestionError):
    """Raised when the remote recording is longer than the configured limit."""


class DownloadTimeoutError(UrlIngestionError):
    """Raised when the download exceeds the configured timeout."""


@dataclass(frozen=True)
class DownloadedMedia:
    """A media file that was successfully acquired from a URL."""

    path: Path
    url: str
    strategy: str
    source_type: SourceType
    original_filename: str
    size_bytes: int
    title: str
    duration_hint: float | None = None

    def as_source(self) -> ValidatedSource:
        """Convert the download into the pipeline's source model."""
        return ValidatedSource(
            kind=IngestionKind.URL,
            source_type=self.source_type,
            original_filename=self.original_filename,
            title=self.title,
            size_bytes=self.size_bytes,
            url=self.url,
            download_strategy=self.strategy,
        )


# --------------------------------------------------------------------------- validation
def normalise_url(value: str | None) -> str:
    """Trim a user supplied URL and add a default scheme when it is missing.

    Bare hosts such as ``example.com/lecture.mp4`` become ``https://...``, while
    explicit non-web schemes (``file:``, ``data:``, ``mailto:`` ...) are preserved
    so validation can reject them with an accurate message.
    """
    text = (value or "").strip()
    if not text:
        raise UnsupportedUrlError("Enter a video URL before starting.")
    if "://" in text or _EXPLICIT_SCHEME_PATTERN.match(text):
        return text
    return f"https://{text}"


def validate_url_syntax(value: str | None, settings: Settings | None = None) -> str:
    """Validate URL syntax only (no DNS lookup) and return the normalised URL."""
    resolved = settings or get_settings()
    text = normalise_url(value)
    parts = urlsplit(text)

    if parts.scheme.lower() not in _ALLOWED_SCHEMES:
        raise UnsupportedUrlError(
            f"Only http and https URLs are supported (received '{parts.scheme or 'none'}'). "
            "Upload the file instead if it lives on your machine."
        )
    if parts.username or parts.password:
        raise UnsupportedUrlError(
            "URLs containing embedded credentials are not supported. Use a public link or "
            "upload an authorized copy of the recording."
        )
    if not parts.hostname:
        raise UnsupportedUrlError("The URL is missing a host name.")
    if _URL_WHITESPACE_PATTERN.search(text):
        raise UnsupportedUrlError(
            "The URL contains spaces or characters that are not valid in a link. "
            "Copy the link again, or upload the file instead."
        )

    host = parts.hostname.lower().rstrip(".")
    if host in _BLOCKED_HOSTNAMES or host.endswith(_BLOCKED_HOST_SUFFIXES):
        raise BlockedDestinationError(
            "Requests to local or internal addresses are not allowed."
        )

    # An IP literal needs no DNS lookup, so refuse non-public ones immediately.
    try:
        literal = ipaddress.ip_address(host.split("%")[0])
    except ValueError:
        literal = None
    if literal is not None:
        if address_is_blocked(literal):
            raise BlockedDestinationError(
                "Requests to private, loopback or reserved addresses are not allowed."
            )
        return text

    if not _HOSTNAME_PATTERN.match(host):
        raise UnsupportedUrlError(
            f"'{host}' is not a valid host name, so this link cannot be used. "
            "Paste the full video link or upload the file instead."
        )
    return text


#: NAT64 translation prefix (RFC 6052). It carries an IPv4 address, so the
#: embedded address must be validated instead of the wrapper being refused.
_NAT64_PREFIXES = (ipaddress.ip_network("64:ff9b::/96"),)

#: 6to4 embeds an IPv4 address in the second and third hextets (RFC 3056).
_SIX_TO_FOUR_PREFIX = ipaddress.ip_network("2002::/16")


def _unwrap_translated_address(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> ipaddress.IPv4Address | None:
    """Return the IPv4 address carried by an IPv4-mapped, NAT64 or 6to4 address."""
    if not isinstance(address, ipaddress.IPv6Address):
        return None
    if address.ipv4_mapped is not None:
        return address.ipv4_mapped
    if address in _SIX_TO_FOUR_PREFIX:
        return ipaddress.IPv4Address((int(address) >> 80) & 0xFFFFFFFF)
    for prefix in _NAT64_PREFIXES:
        if address in prefix:
            return ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
    return None


def address_is_blocked(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True when an address must never be contacted.

    Loopback, private, link-local, multicast, unspecified and reserved ranges are
    refused. IPv4-mapped, 6to4 and NAT64 addresses are unwrapped first so that
    ``::ffff:127.0.0.1`` is still recognised as loopback while a NAT64 address
    carrying a public IPv4 address is not rejected for its wrapper alone.
    """
    unwrapped = _unwrap_translated_address(address)
    if unwrapped is not None:
        return address_is_blocked(unwrapped)
    return bool(
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def _resolve_host(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as error:
        raise DownloadFailedError(
            f"The host '{host}' could not be resolved. Check the URL and your connection."
        ) from error
    return sorted({info[4][0] for info in infos})


def assert_public_destination(url: str) -> list[str]:
    """Refuse loopback, private, link-local, multicast and reserved destinations."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise UnsupportedUrlError("The URL is missing a host name.")

    if host in _BLOCKED_HOSTNAMES or host.endswith(_BLOCKED_HOST_SUFFIXES):
        raise BlockedDestinationError("Requests to local or internal addresses are not allowed.")

    addresses = _resolve_host(host)
    if not addresses:
        raise DownloadFailedError("The host did not resolve to an address.")

    for address in addresses:
        try:
            ip = ipaddress.ip_address(address.split("%")[0])
        except ValueError as error:
            raise BlockedDestinationError(
                "The URL resolved to an address that cannot be used."
            ) from error
        if address_is_blocked(ip):
            raise BlockedDestinationError(
                "Requests to private, loopback or reserved addresses are not allowed."
            )
    return addresses


def resolve_download_strategy(url: str, settings: Settings | None = None) -> str:
    """Decide how a URL should be acquired: ``direct`` or ``platform``."""
    resolved = settings or get_settings()
    suffix = normalise_extension(urlsplit(url).path)
    if suffix and suffix in resolved.allowed_media_extensions:
        return STRATEGY_DIRECT
    return STRATEGY_PLATFORM


def validate_url(value: str | None, settings: Settings | None = None) -> tuple[str, str]:
    """Full validation: syntax plus public-destination check.

    Returns ``(normalised_url, strategy)``.
    """
    resolved = settings or get_settings()
    if not resolved.url_downloads_enabled:
        raise UnsupportedUrlError(
            "URL downloads are disabled in this environment. Upload the media file instead."
        )
    text = validate_url_syntax(value, resolved)
    assert_public_destination(text)
    return text, resolve_download_strategy(text, resolved)


def build_url_source(value: str | None, settings: Settings | None = None) -> ValidatedSource:
    """Validate a URL and describe it as a pipeline source (without downloading)."""
    resolved = settings or get_settings()
    text, strategy = validate_url(value, resolved)
    # URL sources are typed as URL; whether the media carries audio/video is
    # determined by media inspection during processing.
    return ValidatedSource(
        kind=IngestionKind.URL,
        source_type=SourceType.URL,
        title=urlsplit(text).hostname or text,
        url=text,
        download_strategy=strategy,
    )


def strategy_explanation(url: str, settings: Settings | None = None) -> str:
    """Explain which acquisition path will be used, for the interface."""
    try:
        strategy = resolve_download_strategy(url, settings)
    except UnsupportedUrlError as error:
        return str(error)
    if strategy == STRATEGY_DIRECT:
        return "This looks like a direct media link and will be downloaded over HTTP."
    return (
        "This looks like a media page rather than a direct file. It will be handed to yt-dlp "
        "for supported public sources; private, paid or DRM protected material is not supported."
    )


# --------------------------------------------------------------------------- downloads
def _cache_key(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def _suffix_from_content_type(content_type: str | None) -> str:
    if not content_type:
        return ""
    base = content_type.split(";")[0].strip().lower()
    return _CONTENT_TYPE_SUFFIXES.get(base, "")


def _filename_from_url(url: str) -> str:
    path = urlsplit(url).path
    name = Path(unquote(path)).name
    return name or "download"


def _content_type_is_media(content_type: str | None) -> bool:
    if not content_type:
        return True  # servers that omit the header: let ffprobe decide
    base = content_type.split(";")[0].strip().lower()
    if base in _CONTENT_TYPE_SUFFIXES or base == "application/octet-stream":
        return True
    return base.startswith(("audio/", "video/"))


def download_media(
    url: str,
    settings: Settings | None = None,
    *,
    progress: ProgressCallback | None = None,
    directory: Path | str | None = None,
) -> DownloadedMedia:
    """Acquire media from a validated URL into a scratch directory.

    When the caller owns a per-job directory it should pass it as ``directory`` so
    the media is removed with the rest of the job's scratch files. Otherwise the
    shared media temp directory is used and a completed download may be reused for
    the same URL.
    """
    resolved = settings or get_settings()
    text, strategy = validate_url(url, resolved)
    target = ensure_directory(Path(directory) if directory else resolved.media_temp_dir)

    _notify(progress, f"Acquiring media ({strategy} download)...")
    if strategy == STRATEGY_DIRECT:
        return _download_direct(text, target, resolved, progress)
    return _download_with_ytdlp(text, target, resolved, progress)


def _notify(progress: ProgressCallback | None, message: str) -> None:
    if progress is not None:
        progress(message)


def _final_path(directory: Path, key: str, suffix: str) -> Path:
    return Path(directory) / f"{key}{suffix}"


def _existing_download(directory: Path, key: str) -> Path | None:
    """Return a previously completed download for the same URL, if any."""
    for candidate in sorted(Path(directory).glob(f"{key}.*")):
        if candidate.suffix == ".part" or not candidate.is_file():
            continue
        if candidate.stat().st_size > 0:
            return candidate
    return None


def _download_direct(
    url: str,
    directory: Path,
    settings: Settings,
    progress: ProgressCallback | None,
) -> DownloadedMedia:
    """Stream a direct media URL to disk with redirect, size and timeout guards."""
    import requests  # imported lazily so the module stays importable without it

    key = _cache_key(url)
    cached = _existing_download(directory, key)
    if cached is not None:
        logger.info("Reusing a previously completed download for the same URL.")
        return DownloadedMedia(
            path=cached,
            url=url,
            strategy=STRATEGY_DIRECT,
            source_type=SourceType.URL,
            original_filename=_filename_from_url(url),
            size_bytes=cached.stat().st_size,
            title=Path(_filename_from_url(url)).stem,
        )

    partial = Path(directory) / f"{key}.part"
    limit = settings.max_download_size_bytes
    timeout = (CONNECT_TIMEOUT_SECONDS, settings.url_download_timeout_seconds)
    headers = {"User-Agent": USER_AGENT}
    current = url

    try:
        with requests.Session() as session:
            for _ in range(MAX_REDIRECTS + 1):
                with session.get(
                    current,
                    stream=True,
                    timeout=timeout,
                    allow_redirects=False,
                    headers=headers,
                ) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        location = response.headers.get("Location")
                        if not location:
                            raise DownloadFailedError(
                                "The media server redirected the request without a destination."
                            )
                        current = urljoin(current, location)
                        validate_url_syntax(current, settings)
                        assert_public_destination(current)
                        continue
                    return _stream_response(
                        response, current, key, directory, partial, limit, settings, progress
                    )
    except requests.Timeout as error:
        partial.unlink(missing_ok=True)
        raise DownloadTimeoutError(
            "The download timed out. Check your connection, or raise "
            "URL_DOWNLOAD_TIMEOUT_SECONDS in your local .env file."
        ) from error
    except requests.RequestException as error:
        partial.unlink(missing_ok=True)
        logger.info("Direct download failed: %s", type(error).__name__)
        raise DownloadFailedError(
            "The media could not be downloaded. The link may be broken, private or blocked."
        ) from error
    except BaseException:
        partial.unlink(missing_ok=True)
        raise

    partial.unlink(missing_ok=True)
    raise DownloadFailedError("Too many redirects while downloading the media.")


def _stream_response(
    response: "object",
    url: str,
    key: str,
    directory: Path,
    partial: Path,
    limit: int,
    settings: Settings,
    progress: ProgressCallback | None,
) -> DownloadedMedia:
    """Write a streaming HTTP response to disk, enforcing the size limit."""
    status = int(getattr(response, "status_code", 0))
    if status in (401, 403):
        raise DownloadFailedError(
            f"The source refused the request (HTTP {status}). The media may be private or "
            "require sign-in - upload an authorized recording instead."
        )
    if status >= 400:
        raise DownloadFailedError(f"The media server returned HTTP {status}.")

    content_type = response.headers.get("Content-Type")
    if not _content_type_is_media(content_type):
        raise DownloadFailedError(
            "That URL returned a web page rather than a media file. Use a direct link to the "
            "media file, or upload the recording."
        )

    declared = response.headers.get("Content-Length")
    if declared is not None:
        try:
            if int(declared) > limit:
                raise DownloadTooLargeError(
                    f"The remote file is {human_readable_size(int(declared))}, which exceeds the "
                    f"{settings.max_download_size_mb} MB download limit."
                )
        except ValueError:
            declared = None

    suffix = (
        _suffix_from_content_type(content_type)
        or normalise_extension(_filename_from_url(url))
        or ".media"
    )
    target = _final_path(directory, key, suffix)
    written = 0

    try:
        with partial.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
                if not chunk:
                    continue
                written += len(chunk)
                if written > limit:
                    raise DownloadTooLargeError(
                        f"The download exceeded the {settings.max_download_size_mb} MB limit and "
                        "was stopped. Raise MAX_DOWNLOAD_SIZE_MB if you need larger recordings."
                    )
                handle.write(chunk)
        if written == 0:
            raise DownloadFailedError("The download contained no data.")
        partial.replace(target)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise

    logger.info("Direct download completed (bytes=%s)", written)
    _notify(progress, f"Downloaded {human_readable_size(written)}.")
    return DownloadedMedia(
        path=target,
        url=url,
        strategy=STRATEGY_DIRECT,
        source_type=SourceType.URL,
        original_filename=_filename_from_url(url),
        size_bytes=written,
        title=Path(_filename_from_url(url)).stem or "Downloaded media",
    )


def _download_with_ytdlp(
    url: str,
    directory: Path,
    settings: Settings,
    progress: ProgressCallback | None,
) -> DownloadedMedia:
    """Download a supported public page with yt-dlp (never bypassing access controls)."""
    try:
        from yt_dlp import YoutubeDL
        from yt_dlp.utils import DownloadError, ExtractorError
    except ImportError as error:
        raise UnsupportedUrlError(
            "yt-dlp is not installed, so media pages cannot be downloaded. Run "
            "'pip install -r requirements.txt' or upload the recording instead."
        ) from error

    key = _cache_key(url)
    cached = _existing_download(directory, key)
    if cached is not None:
        logger.info("Reusing a previously completed download for the same URL.")
        return DownloadedMedia(
            path=cached,
            url=url,
            strategy=STRATEGY_PLATFORM,
            source_type=SourceType.URL,
            original_filename=cached.name,
            size_bytes=cached.stat().st_size,
            title=cached.stem,
        )

    base_options: dict[str, object] = {
        **_cookie_options(settings, progress),
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "retries": 2,
        "socket_timeout": min(settings.url_download_timeout_seconds, 60.0),
        "max_filesize": settings.max_download_size_bytes,
        "continuedl": False,
        "noprogress": True,
        "overwrites": False,
        "outtmpl": str(Path(directory) / f"{key}.%(ext)s"),
    }

    _notify(progress, "Reading media information from the source page...")
    try:
        with YoutubeDL({**base_options, "skip_download": True}) as probe:
            info = probe.extract_info(url, download=False)
    except DownloadError as error:
        raise _translate_ytdlp_error(error) from error
    except ExtractorError as error:
        raise UnsupportedUrlError(
            "This page is not supported by yt-dlp. Upload the recording file instead."
        ) from error

    if not isinstance(info, dict):
        raise UnsupportedUrlError("The source did not return usable media information.")

    duration = info.get("duration")
    if isinstance(duration, (int, float)) and duration > settings.max_video_duration_seconds:
        raise RecordingTooLongError(
            f"This recording is {duration / 60:.1f} minutes long, which exceeds the configured "
            f"limit of {settings.max_video_duration_seconds / 60:.0f} minutes."
        )

    estimated = info.get("filesize") or info.get("filesize_approx")
    if isinstance(estimated, (int, float)) and estimated > settings.max_download_size_bytes:
        raise DownloadTooLargeError(
            f"The remote file is about {human_readable_size(estimated)}, which exceeds the "
            f"{settings.max_download_size_mb} MB download limit."
        )

    _notify(progress, f"Downloading media ({settings.max_download_size_mb} MB limit)...")
    try:
        with YoutubeDL(base_options) as downloader:
            downloaded = downloader.extract_info(url, download=True)
    except DownloadError as error:
        _remove_partial_downloads(directory, key)
        raise _translate_ytdlp_error(error) from error

    path = _downloaded_file_path(downloaded, directory, key)
    if path is None or not path.is_file() or path.stat().st_size == 0:
        _remove_partial_downloads(directory, key)
        raise DownloadFailedError("The download did not produce a usable media file.")

    size = path.stat().st_size
    if size > settings.max_download_size_bytes:
        path.unlink(missing_ok=True)
        raise DownloadTooLargeError(
            f"The downloaded file is {human_readable_size(size)}, which exceeds the "
            f"{settings.max_download_size_mb} MB download limit."
        )

    title = str((downloaded or {}).get("title") or path.stem)[:200]
    logger.info("Platform download completed (bytes=%s)", size)
    _notify(progress, f"Downloaded {human_readable_size(size)}.")
    return DownloadedMedia(
        path=path,
        url=url,
        strategy=STRATEGY_PLATFORM,
        source_type=SourceType.URL,
        original_filename=path.name,
        size_bytes=size,
        title=title,
        duration_hint=float(duration) if isinstance(duration, (int, float)) else None,
    )


def _cookie_options(
    settings: Settings, progress: ProgressCallback | None = None
) -> dict[str, object]:
    """Return yt-dlp cookie options for the user's *own* authorized access.

    Nothing is enabled by default, and a missing file is a clear local error
    instead of a provider failure. Cookies are only ever handed to yt-dlp on this
    machine: they are not logged, stored by the application, or sent anywhere else.
    """
    cookie_file = (settings.ytdlp_cookies_file or "").strip()
    browser = (settings.ytdlp_cookies_from_browser or "").strip().lower()

    if cookie_file:
        path = Path(cookie_file).expanduser()
        if not path.is_file():
            raise DownloadFailedError(
                "YTDLP_COOKIES_FILE does not point at a readable cookies file. Export "
                "your own cookies with 'yt-dlp --cookies-from-browser <browser> "
                "--cookies cookies.txt', set YTDLP_COOKIES_FILE to that path, or remove "
                "the setting to download anonymous public media again."
            )
        if browser:
            logger.info(
                "Both YTDLP_COOKIES_FILE and YTDLP_COOKIES_FROM_BROWSER are set; "
                "using the explicit cookies file."
            )
        _notify(progress, "Using the authorized cookies file for this download.")
        logger.info("Authorized cookies enabled for platform downloads (source=file).")
        return {"cookiefile": str(path)}

    if browser:
        if browser not in SUPPORTED_COOKIE_BROWSERS:
            raise DownloadFailedError(
                f"YTDLP_COOKIES_FROM_BROWSER={browser!r} is not a supported browser. "
                f"Use one of: {', '.join(SUPPORTED_COOKIE_BROWSERS)}."
            )
        _notify(progress, f"Using the signed-in {browser} profile for this download.")
        logger.info(
            "Authorized cookies enabled for platform downloads (source=browser:%s).",
            browser,
        )
        return {"cookiesfrombrowser": (browser, None, None, None)}

    return {}


def _downloaded_file_path(info: object, directory: Path, key: str) -> Path | None:
    """Find the file yt-dlp wrote for this job."""
    if isinstance(info, dict):
        requested = info.get("requested_downloads")
        if isinstance(requested, list):
            for entry in requested:
                filepath = (entry or {}).get("filepath")
                if filepath:
                    candidate = Path(filepath)
                    if candidate.is_file():
                        return candidate
    for candidate in sorted(Path(directory).glob(f"{key}.*")):
        if candidate.suffix != ".part" and candidate.is_file():
            return candidate
    return None


def _remove_partial_downloads(directory: Path, key: str) -> None:
    """Delete incomplete artefacts of a failed download."""
    for pattern in (f"{key}.part", f"{key}.part-*", f"{key}.*.part"):
        for leftover in Path(directory).glob(pattern):
            leftover.unlink(missing_ok=True)


def _translate_ytdlp_error(error: Exception) -> UrlIngestionError:
    """Map a yt-dlp failure onto a safe, actionable application error."""
    message = str(error).lower()
    logger.info("yt-dlp reported: %s", str(error)[:200].replace("\n", " "))

    # Checked before the sign-in branch: the platform's bot-check hint mentions
    # "--cookies", but the cause is proof-of-humanity, not a private recording.
    if "not a bot" in message or ("confirm" in message and "bot" in message):
        return DownloadFailedError(
            "YouTube refused the anonymous download with its \"confirm you're not a bot\" "
            "check. Nothing was bypassed. If you have authorized access to this "
            "recording, export your own cookies and set YTDLP_COOKIES_FILE (or set "
            "YTDLP_COOKIES_FROM_BROWSER=chrome/edge/firefox), then retry. Otherwise "
            "upload an authorized copy of the recording."
        )
    if "http error 403" in message or "forbidden" in message:
        return DownloadFailedError(
            "The source refused the download with HTTP 403 Forbidden. This is usually "
            "a platform access restriction rather than a local problem. If you have "
            "authorized access, configure YTDLP_COOKIES_FILE or "
            "YTDLP_COOKIES_FROM_BROWSER; otherwise upload an authorized copy."
        )
    if "cookies" in message or "decrypt" in message or "keyring" in message:
        return DownloadFailedError(
            "yt-dlp could not read the configured cookies. Close the browser, check "
            "YTDLP_COOKIES_FILE and YTDLP_COOKIES_FROM_BROWSER, or export a cookies.txt "
            "file instead. Access controls are never bypassed."
        )
    if "private" in message or "sign in" in message or "login" in message or "members-only" in message:
        return DownloadFailedError(
            "This recording is private or requires sign-in, which VideoMind AI does not bypass. "
            "Upload an authorized copy of the recording instead."
        )
    if "unavailable" in message or "removed" in message or "404" in message:
        return DownloadFailedError(
            "The recording is unavailable or has been removed from the source."
        )
    if "duration" in message and "limit" in message:
        return RecordingTooLongError(
            "The recording exceeds the configured duration limit."
        )
    if "filesize" in message or "too large" in message:
        return DownloadTooLargeError(
            "The recording exceeds the configured download size limit."
        )
    if "unsupported url" in message:
        return UnsupportedUrlError(
            "This page is not supported by yt-dlp. Upload the recording file instead."
        )
    # Observed live (2026-10-25): the page metadata was read successfully, but the
    # media CDN host did not resolve, and the generic message blamed the link.
    if (
        "getaddrinfo failed" in message
        or "failed to resolve" in message
        or "name or service not known" in message
        or "name resolution" in message
        or "nodename nor servname" in message
    ):
        return DownloadFailedError(
            "The media server's host name could not be resolved (DNS), even though the "
            "page itself was read. Check your connection, VPN or DNS filter, then retry; "
            "if it keeps failing, upload the recording instead."
        )
    if "timed out" in message or "timeout" in message:
        return DownloadTimeoutError("The download timed out before it could finish.")
    return DownloadFailedError(
        "The media could not be downloaded from this page. Check the link, or upload the "
        "recording instead."
    )
