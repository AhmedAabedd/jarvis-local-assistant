"""Media agent — self-contained multimodal specialist.

The orchestrator calls run(task) and gets back a plain-text report describing
what's in a piece of media: an image, a PDF, an audio clip, or a video. The
heavy lifting (actually *seeing* and *hearing*) is done by an omni model; the
tools here only LOCATE media and FEED its bytes to that model. Understanding is
the model's job, not a tool's.

Multimodal tool results use LangChain content blocks, so ``ToolNode`` preserves
the loaded image/audio parts in the matching tool result. The model sees the
media on its next graph step and reasons over it.

Media handling is ISOLATED to this agent. The supervisor delegates here and only
the final text report crosses back — raw bytes never bloat the orchestrator.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Annotated, Literal

from langchain_core.tools import tool

from .. import config, graph_runtime, llm

MAX_TOOL_ROUNDS = 8

# What we recognise, by extension.
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac"}
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
PDF_EXT = {".pdf"}

# NVIDIA inline images must stay small (large ones need a separate upload API),
# so we downscale to fit. Longest side in px and a rough base64 byte ceiling.
IMAGE_MAX_SIDE = 1568
IMAGE_MAX_BYTES = 180_000

PDF_TEXT_MAX_CHARS = 12_000   # how much extracted PDF text we feed at most
PDF_MIN_TEXT_CHARS = 80       # below this we treat the PDF as scanned (images)
PDF_RENDER_MAX_PAGES = 5      # cap rendered pages for a scanned PDF
VIDEO_DEFAULT_FRAMES = 6      # keyframes sampled from a video by default


SYSTEM_PROMPT = """\
You are the dedicated media specialist. You read and understand media —
images, PDFs, audio clips, and video — and report what's in them.

Your tools:
- load_media(path): load an image, PDF, or audio file so you can see/hear it.
  The bytes are attached to the conversation; on your next turn you can analyse
  them directly. For PDFs with selectable text you get the text; for scanned
  PDFs you get page images.
- sample_frames(path, count): pull keyframes from a VIDEO as images so you can
  describe what happens in it.
- find_media(directory, kind): list media files in a folder when you weren't
  given an exact path (kind = image|audio|video|pdf|any).

METHOD
- If the task names an exact file, load_media (or sample_frames for video) it
  right away, then analyse what you see/hear.
- If it only describes the file ("the latest invoice in Downloads"), find_media
  first, pick the right one, then load it.
- Read every readable detail: text in images/PDFs, speakers and words in audio,
  actions and on-screen text in video frames. Quote exact text when it matters.
- Don't guess at content you couldn't actually load. If a tool says a dependency
  is missing or a file is unreadable, say so plainly.

FINAL REPORT (MANDATORY)
Your last message is read by the SUPERVISOR, not the user. Make it a direct,
self-contained answer to the task: what the media is, the key content, and any
exact text/figures that matter. Keep it tight — no fluff, no "certainly!". If you
could not analyse the media, say exactly why.
"""


# ---------------------------------------------------------------------------
# Loading helpers (each degrades gracefully if an optional dep is missing)
# ---------------------------------------------------------------------------

def _kind(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext in PDF_EXT:
        return "pdf"
    if ext in AUDIO_EXT:
        return "audio"
    if ext in VIDEO_EXT:
        return "video"
    return "unknown"


def _image_part_from_bytes(data: bytes, mime: str) -> dict:
    b64 = base64.b64encode(data).decode()
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}


def _shrink_image(data: bytes) -> tuple[bytes, str]:
    """Downscale/recompress an image to fit NVIDIA's inline limits.

    Returns (bytes, mime). Falls back to the raw bytes if Pillow isn't around;
    callers handle an over-limit raw image by reporting it.
    """
    try:
        import io

        from PIL import Image
    except ImportError:
        return data, "image/jpeg"

    img = Image.open(io.BytesIO(data))
    if img.mode in ("RGBA", "P", "LA"):
        img = img.convert("RGB")
    img.thumbnail((IMAGE_MAX_SIDE, IMAGE_MAX_SIDE))
    quality = 85
    while True:
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        out = buf.getvalue()
        if len(out) <= IMAGE_MAX_BYTES or quality <= 35:
            return out, "image/jpeg"
        quality -= 15


def _load_image(path: Path) -> tuple[str, list[dict]]:
    data, _ = _shrink_image(path.read_bytes())
    if len(base64.b64encode(data)) > IMAGE_MAX_BYTES * 1.4:
        return (f"{path.name} is too large to inline; install Pillow so I can "
                f"downscale it.", [])
    return f"Loaded image {path.name} — attached below.", [_image_part_from_bytes(data, "image/jpeg")]


def _load_pdf(path: Path) -> tuple[str, list[dict]]:
    try:
        from pypdf import PdfReader
    except ImportError:
        return "Can't read PDFs: install 'pypdf'.", []

    try:
        reader = PdfReader(str(path))
        text = "\n".join((page.extract_text() or "") for page in reader.pages).strip()
    except Exception as exc:
        return f"Could not open {path.name}: {exc}", []

    if len(text) >= PDF_MIN_TEXT_CHARS:
        body = text[:PDF_TEXT_MAX_CHARS]
        if len(text) > PDF_TEXT_MAX_CHARS:
            body += " … [truncated]"
        return f"Extracted text from {path.name} ({len(reader.pages)} pages):\n\n{body}", []

    # Little/no text → scanned PDF; render pages to images if we can.
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return (f"{path.name} has no selectable text (looks scanned). Install "
                f"'PyMuPDF' so I can read it as images.", [])

    doc = fitz.open(str(path))
    parts: list[dict] = []
    for page in doc[:PDF_RENDER_MAX_PAGES]:
        pix = page.get_pixmap(dpi=150)
        data, _ = _shrink_image(pix.tobytes("png"))
        parts.append(_image_part_from_bytes(data, "image/jpeg"))
    return (f"{path.name} is scanned — attached the first {len(parts)} page "
            f"image(s) below.", parts)


def _load_audio(path: Path) -> tuple[str, list[dict]]:
    fmt = path.suffix.lower().lstrip(".")
    if fmt == "m4a":
        fmt = "mp4"
    b64 = base64.b64encode(path.read_bytes()).decode()
    part = {"type": "input_audio", "input_audio": {"data": b64, "format": fmt}}
    return f"Loaded audio {path.name} — attached below.", [part]


def _resolve(path_or_url: str) -> Path | None:
    """Local path only for now (URLs would download first). Returns None if missing."""
    p = Path(path_or_url).expanduser()
    return p if p.exists() else None


# ---------------------------------------------------------------------------
# Tool implementations return ``(summary_text, media_parts)``. Typed wrappers
# below expose those parts as standard LangChain tool-message content blocks.
# ---------------------------------------------------------------------------

def load_media(path: str) -> tuple[str, list[dict]]:
    """Load an image, PDF, or audio file and attach it for analysis."""
    resolved = _resolve(path)
    if resolved is None:
        return f"No such file: {path}", []
    kind = _kind(resolved)
    if kind == "image":
        return _load_image(resolved)
    if kind == "pdf":
        return _load_pdf(resolved)
    if kind == "audio":
        return _load_audio(resolved)
    if kind == "video":
        return ("That's a video — use sample_frames to pull keyframes from it.", [])
    return f"Unsupported file type: {resolved.suffix or '(none)'}", []


def sample_frames(path: str, count: int = VIDEO_DEFAULT_FRAMES) -> tuple[str, list[dict]]:
    """Pull `count` evenly-spaced keyframes from a video and attach them."""
    resolved = _resolve(path)
    if resolved is None:
        return f"No such file: {path}", []
    if _kind(resolved) != "video":
        return f"{resolved.name} isn't a video.", []
    try:
        import cv2
    except ImportError:
        return "Can't sample video frames: install 'opencv-python'.", []

    cap = cv2.VideoCapture(str(resolved))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    if total <= 0:
        cap.release()
        return f"Could not read frames from {resolved.name}.", []

    count = max(1, min(count, 12))
    step = max(1, total // count)
    parts: list[dict] = []
    for idx in range(0, total, step):
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap.read()
        if not ok:
            continue
        ok, buf = cv2.imencode(".jpg", frame)
        if not ok:
            continue
        data, _ = _shrink_image(buf.tobytes())
        parts.append(_image_part_from_bytes(data, "image/jpeg"))
        if len(parts) >= count:
            break
    cap.release()
    if not parts:
        return f"Got no usable frames from {resolved.name}.", []
    return f"Sampled {len(parts)} frame(s) from {resolved.name} — attached below.", parts


def find_media(directory: str, kind: str = "any") -> tuple[str, list[dict]]:
    """List media files in a folder. kind = image|audio|video|pdf|any."""
    folder = Path(directory).expanduser()
    if not folder.is_dir():
        return f"Not a directory: {directory}", []
    exts = {
        "image": IMAGE_EXT, "audio": AUDIO_EXT, "video": VIDEO_EXT, "pdf": PDF_EXT,
        "any": IMAGE_EXT | AUDIO_EXT | VIDEO_EXT | PDF_EXT,
    }.get(kind.lower(), IMAGE_EXT | AUDIO_EXT | VIDEO_EXT | PDF_EXT)

    hits = sorted(
        (p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in exts),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not hits:
        return f"No {kind} files in {folder}.", []
    lines = [f"{kind} files in {folder} (newest first):"]
    lines += [f"  {p.name}  ({_kind(p)})" for p in hits[:30]]
    return "\n".join(lines), []


def _media_content(result: tuple[str, list[dict]]) -> list[dict]:
    summary, parts = result
    return [{"type": "text", "text": summary}, *parts]


@tool("load_media")
def load_media_tool(
    path: Annotated[str, "Absolute path or ~ path to an image, PDF, or audio file."]
) -> list[dict]:
    """Load media into the conversation so its contents can be analysed."""

    return _media_content(load_media(path))


@tool("sample_frames")
def sample_frames_tool(
    path: Annotated[str, "Video file path."],
    count: Annotated[int, "Number of evenly spaced frames, from 1 through 12."] = 6,
) -> list[dict]:
    """Extract evenly spaced video frames for visual analysis."""

    return _media_content(sample_frames(path, count))


@tool("find_media")
def find_media_tool(
    directory: Annotated[str, "Folder to inspect, such as ~/Downloads."],
    kind: Literal["image", "audio", "video", "pdf", "any"] = "any",
) -> list[dict]:
    """List recent media files when the exact path is not known."""

    return _media_content(find_media(directory, kind))


TOOLS = [load_media_tool, sample_frames_tool, find_media_tool]


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run(task: str, allowed_tools: list[str] | None = None) -> str:
    """Run the media analyst on a task. Returns a plain-text report."""
    from .. import db
    runtime = db.get_builtin_agent_runtime(
        "media",
        fallback_model=config.MEDIA_MODEL,
        fallback_base_url=config.NVIDIA_BASE_URL,
        fallback_api_key=config.NVIDIA_API_KEY,
        fallback_provider="NVIDIA",
    )

    selected_tools = graph_runtime.select_tools(TOOLS, allowed_tools)

    messages = [
        {"role": "system", "content": config.specialist_system_prompt(SYSTEM_PROMPT)},
        {"role": "user", "content": task},
    ]

    return graph_runtime.run_tool_agent(
        messages,
        selected_tools,
        lambda history, schemas: llm.openai_chat(
            history,
            tools=schemas,
            model=runtime["model"],
            provider=runtime["provider"],
            base_url=runtime["base_url"],
            api_key=runtime["api_key"],
        ),
        max_rounds=MAX_TOOL_ROUNDS,
        empty_response="Nothing to report.",
        exhausted_response="Media agent reached max tool rounds — partial analysis only.",
        error_formatter=lambda _executed, error: f"Media agent failed: {error}",
    )
