"""Screenshot / visual-capture tools: capture a part of a source as an image.

This is the pipeline's "show me, don't just tell me" feature, aimed at visual
learners (medical students, artists, anyone who learns from diagrams). It turns
a *part of a source* into an HD image that lands in the vault and is ready to
embed in the matching note, with a caption that says **what** it shows and
**where in the source** it came from.

Two sources are supported:

* ``capture_pdf_page`` — render one or more PDF pages (or a cropped region of a
  page) to a high-DPI PNG. Pure Python via PyMuPDF; no system binaries.
* ``get_youtube_frames`` — sample representative frames from a YouTube video.
  Needs the ``ffmpeg`` system binary plus ``yt-dlp`` (pip). Returns a clean
  ``{"error": ...}`` when ffmpeg is missing instead of crashing.

Quality model (per user decision): the **full-resolution** image is saved into
the vault for the reader, while a **downscaled** copy is what gets attached as
MCP image content for the model to caption — accurate captions, lower token
cost. Saved images go to one central folder (``config.get_screenshots_folder``).

Like the other tools, each returns plain data / image content and reports
failures as ``{"error": ...}`` rather than raising.
"""

import io
import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP, Image

from config import (
    find_ffmpeg,
    get_screenshots_folder,
    get_vault_path,
    resolve_image_target,
    resolve_in_vault,
)

# Default downscale width (px) for the copy the model analyses. The HD original
# is what gets saved to the vault.
_ANALYSIS_WIDTH = 1024
# Default *display* width (px) baked into the Obsidian embed (`![[img|WIDTH]]`),
# so an embedded figure shows as a readable thumbnail instead of taking over the
# whole note. The full-resolution file is untouched — readers click to enlarge.
_EMBED_WIDTH = 480
# Characters Windows/Obsidian disallow in filenames. Spaces are kept (Obsidian
# convention favours them and embeds like ![[a b.png]] resolve fine).
_ILLEGAL_FILENAME = re.compile(r'[\\/:*?"<>|]+')


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #

def _safe_stem(text: str) -> str:
    """Collapse whitespace and strip filesystem-illegal characters from a name."""
    cleaned = _ILLEGAL_FILENAME.sub(" ", text or "")
    return " ".join(cleaned.split()).strip() or "capture"


def _unique_target(folder_rel: str, filename: str) -> Path:
    """Return a vault path under ``folder_rel`` that does not collide on disk.

    Appends " 2", " 3", … before the extension if needed, so re-capturing the
    same page never silently overwrites an earlier image.
    """
    stem, _, ext = filename.rpartition(".")
    candidate = resolve_image_target(f"{folder_rel}/{filename}")
    n = 2
    while candidate.exists():
        candidate = resolve_image_target(f"{folder_rel}/{stem} {n}.{ext}")
        n += 1
    return candidate


def _encode_png(pil_image) -> bytes:
    """Encode a PIL image to PNG bytes."""
    out = io.BytesIO()
    pil_image.save(out, format="PNG")
    return out.getvalue()


def _downscale_png(png_bytes: bytes, max_width: int) -> bytes:
    """Return ``png_bytes`` resized down to ``max_width`` (keeps aspect ratio).

    Images already at or below ``max_width`` are returned unchanged. Used only
    for the model-facing copy, never for what we save to the vault.
    """
    from PIL import Image as PILImage

    with PILImage.open(io.BytesIO(png_bytes)) as im:
        if im.width <= max_width:
            return png_bytes
        height = round(im.height * (max_width / im.width))
        resized = im.resize((max_width, height), PILImage.LANCZOS)
        out = io.BytesIO()
        resized.save(out, format="PNG")
        return out.getvalue()


def _embed_snippet(
    filename: str, source_name: str, locator: str, width: int = _EMBED_WIDTH
) -> str:
    """A ready-to-paste Obsidian embed: an [!example] callout naming the source
    location (page/timestamp), the image sized to ``width`` px so it stays a
    readable thumbnail, and a caption placeholder for Claude to fill in after
    viewing the image. A width of 0 (or less) omits the size (full width)."""
    sized = f"{filename}|{width}" if width and width > 0 else filename
    return (
        f"> [!example] {source_name} — {locator}\n"
        f"> ![[{sized}]]\n"
        f"> *Caption: explain the point this illustrates.*"
    )


def _save_and_block(
    png_bytes: bytes,
    folder_rel: str,
    filename: str,
    source_name: str,
    locator: str,
    analysis_width: int,
    save: bool,
    return_image: bool,
    embed_width: int = _EMBED_WIDTH,
) -> tuple[dict[str, Any], Image | None]:
    """Save the HD PNG (if ``save``) and build the per-image record + Image block.

    Returns ``(record, image_or_none)``. ``record`` always has ``locator`` and
    an Obsidian ``embed`` snippet; ``saved_path`` appears only when saved.
    """
    record: dict[str, Any] = {"locator": locator, "filename": filename}

    if save:
        target = _unique_target(folder_rel, filename)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(png_bytes)
        rel = target.relative_to(get_vault_path()).as_posix()
        record["filename"] = target.name
        record["saved_path"] = rel
        record["embed"] = _embed_snippet(target.name, source_name, locator, embed_width)

    image = None
    if return_image:
        small = _downscale_png(png_bytes, analysis_width)
        image = Image(data=small, format="png")
    return record, image


def _result(summary: dict[str, Any], images: list[Image], return_images: bool):
    """Assemble the tool return. When returning images, the structured summary
    rides along as a trailing JSON text block so the model still gets every
    saved_path / embed / locator alongside the visuals."""
    if return_images and images:
        return [*images, json.dumps(summary, ensure_ascii=False, indent=2)]
    return summary


def _resolve_source(path: str) -> Path:
    """Resolve a *source* file to read from (read-only).

    Source files (e.g. a textbook PDF) often live outside the vault, so an
    absolute path to an existing file is accepted as-is. Otherwise the path is
    treated as vault-relative and run through the normal vault guard.
    """
    from config import resolve_in_vault

    candidate = Path(path).expanduser()
    if candidate.is_absolute():
        if not candidate.exists():
            raise ValueError(f"Source file not found: {path!r}")
        return candidate.resolve()
    return resolve_in_vault(path)


def _parse_pages(spec: str, page_count: int) -> list[int]:
    """Parse a 1-based page spec (``"12"``, ``"12-14"``, ``"3,5,9"``) into a
    sorted list of 0-based indices, clamped to the document's range."""
    indices: set[int] = set()
    for part in str(spec).replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            lo, _, hi = part.partition("-")
            for p in range(int(lo), int(hi) + 1):
                indices.add(p - 1)
        else:
            indices.add(int(part) - 1)
    return sorted(i for i in indices if 0 <= i < page_count)


def _page_reference(page, pdf_page: int, offset: int) -> tuple[str, str | None]:
    """Resolve the page number a *reader* would recognise for ``page``.

    A PDF's page index (1 = first sheet) often differs from the number printed
    in the book, because of front matter (cover, title, TOC). So a capture of
    "PDF page 47" may really be printed page 35 — naming it ``p.47`` sends the
    reader to the wrong place.

    Resolution order:
      1. The page's own embedded label (``page.get_label()``) when present and
         different from the raw index — this is the authoritative printed page
         (e.g. ``"iii"``, ``"35"``), used automatically with no configuration.
      2. Otherwise, if ``offset`` is given, the index shifted by it
         (``pdf_page + offset``) — a manual fallback for PDFs that embed no
         labels (like many ebook exports).
      3. Otherwise the raw PDF index.

    Returns ``(page_ref, embedded_label)`` where ``page_ref`` is the string to
    show, and ``embedded_label`` is the label from step 1 if one was used (else
    ``None``), so callers can record where the number came from.
    """
    try:
        label = (page.get_label() or "").strip()
    except Exception:  # noqa: BLE001 — older/edge PDFs may not support labels
        label = ""
    if label and label != str(pdf_page):
        return label, label
    if offset:
        return str(pdf_page + offset), None
    return str(pdf_page), None


def _parse_clock(value: str | None) -> float | None:
    """Parse ``"mm:ss"``, ``"hh:mm:ss"``, or a bare seconds number into seconds."""
    if value is None or str(value).strip() == "":
        return None
    text = str(value).strip()
    if ":" in text:
        parts = [float(p) for p in text.split(":")]
        seconds = 0.0
        for p in parts:
            seconds = seconds * 60 + p
        return seconds
    return float(text)


def _fmt_clock(seconds: float) -> str:
    """Seconds → ``mm:ss`` (or ``h:mm:ss``) for captions and filenames."""
    total = int(round(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


# --------------------------------------------------------------------------- #
# Tool registration
# --------------------------------------------------------------------------- #

def register_media_tools(mcp: FastMCP) -> None:
    """Register the screenshot tools on the given FastMCP instance."""

    @mcp.tool()
    def capture_pdf_page(
        pdf_path: str,
        pages: str = "1",
        source_name: str = "",
        region: str | None = None,
        dpi: int = 300,
        page_label_offset: int = 0,
        analysis_width: int = _ANALYSIS_WIDTH,
        embed_width: int = _EMBED_WIDTH,
        save: bool = True,
        return_images: bool = True,
    ) -> Any:
        """Capture one or more PDF pages (or a region of a page) as HD images.

        Renders each page to a high-DPI PNG saved into the vault's central
        screenshots folder, and (by default) returns a downscaled copy as image
        content so you can see it and write an accurate caption. Each saved
        image comes with a ready-to-paste Obsidian ``embed`` snippet that names
        the source page, so the reader knows exactly where it came from.

        Args:
            pdf_path: Path to the source PDF. An absolute path is read directly
                (textbooks usually live outside the vault); a relative path is
                resolved inside the vault.
            pages: 1-based page selector — a single page ``"12"``, a range
                ``"12-14"``, or a list ``"3,5,9"`` (default ``"1"``).
            source_name: Human label used in filenames and captions (e.g.
                ``"Gray's Anatomy"``). Defaults to the PDF filename.
            region: Optional crop within each page as comma-separated fractions
                ``"x0,y0,x1,y1"`` in 0–1 (e.g. ``"0,0,0.5,0.5"`` = top-left
                quarter) to grab just one figure/diagram. Omit for the whole page.
            dpi: Render resolution for the saved HD image (default ``300``).
            page_label_offset: Fallback for PDFs that embed no page labels. The
                printed page is taken automatically from the PDF's own labels
                when present; otherwise captions use the raw PDF index shifted by
                this number (e.g. ``-12`` if 12 pages of front matter precede
                printed page 1). Default ``0`` (use the raw index).
            analysis_width: Width (px) of the downscaled copy sent to the model
                (default ``1024``) to control token cost.
            embed_width: Display width (px) baked into the Obsidian embed so the
                figure shows as a thumbnail, not full-screen (default ``480``;
                ``0`` = full width). The saved file's real resolution is unchanged.
            save: Write the HD PNG into the vault (default ``true``).
            return_images: Attach the downscaled copy as image content
                (default ``true``).
        """
        import fitz  # PyMuPDF

        try:
            source = _resolve_source(pdf_path)
        except ValueError as exc:
            return {"error": str(exc)}
        if source.suffix.lower() != ".pdf":
            return {"error": f"Not a PDF file: {pdf_path!r}"}

        try:
            doc = fitz.open(source)
        except Exception as exc:  # noqa: BLE001 — surface any open/parse failure
            return {"error": f"Could not open PDF: {exc}"}

        page_count = doc.page_count
        try:
            page_indices = _parse_pages(pages, page_count)
        except ValueError:
            doc.close()
            return {"error": f"Could not parse page selector: {pages!r}"}
        if not page_indices:
            doc.close()
            return {
                "error": (
                    f"No valid pages in {pages!r}; the document has "
                    f"{page_count} page(s)."
                )
            }

        clip_fractions = None
        if region:
            try:
                clip_fractions = [float(v) for v in region.split(",")]
                if len(clip_fractions) != 4:
                    raise ValueError
            except ValueError:
                doc.close()
                return {
                    "error": (
                        f"region must be four comma-separated fractions "
                        f"'x0,y0,x1,y1' in 0-1, got: {region!r}"
                    )
                }

        label = _safe_stem(source_name) if source_name else _safe_stem(source.stem)
        folder = get_screenshots_folder()
        if save:
            try:  # fail fast on a misconfigured/ignored screenshots folder
                resolve_image_target(f"{folder}/probe.png")
            except ValueError as exc:
                doc.close()
                return {"error": str(exc)}
        records: list[dict[str, Any]] = []
        images: list[Image] = []

        for idx in page_indices:
            page = doc.load_page(idx)
            clip = None
            if clip_fractions:
                rect = page.rect
                x0, y0, x1, y1 = clip_fractions
                clip = fitz.Rect(
                    rect.x0 + x0 * rect.width,
                    rect.y0 + y0 * rect.height,
                    rect.x0 + x1 * rect.width,
                    rect.y0 + y1 * rect.height,
                )
            try:
                pix = page.get_pixmap(dpi=dpi, clip=clip)
                png_bytes = pix.tobytes("png")
            except Exception as exc:  # noqa: BLE001
                doc.close()
                return {"error": f"Failed to render page {idx + 1}: {exc}"}

            # Prefer the page number a reader recognises (printed label) over the
            # raw PDF index, so captions point to the right place in the source.
            pdf_page = idx + 1
            page_ref, page_label = _page_reference(page, pdf_page, page_label_offset)
            differs = page_ref != str(pdf_page)

            # A cropped region is marked "(detail)" so it is never confused with —
            # or silently collision-renamed against — a full-page capture of the
            # same page (the reader can tell a zoomed-in figure from the whole page).
            detail = " (detail)" if clip else ""
            # When the printed page differs from the PDF index, name both so the
            # reader can find it in the book or in a PDF reader.
            locator = (
                f"p.{page_ref} (PDF p.{pdf_page}){detail}"
                if differs
                else f"p.{pdf_page}{detail}"
            )
            filename = f"{label} p.{page_ref}{' detail' if clip else ''}.png"
            record, image = _save_and_block(
                png_bytes, folder, filename, label, locator,
                analysis_width, save, return_images, embed_width,
            )
            record["page"] = pdf_page
            if differs:
                record["page_ref"] = page_ref
            if page_label is not None:
                record["page_label"] = page_label
            records.append(record)
            if image is not None:
                images.append(image)

        doc.close()
        summary = {
            "source": label,
            "source_path": str(source),
            "page_count": page_count,
            "captured": len(records),
            "saved_folder": folder if save else None,
            "images": records,
        }
        return _result(summary, images, return_images)

    @mcp.tool()
    def get_youtube_frames(
        url: str,
        mode: str = "scene",
        interval_seconds: int = 30,
        scene_threshold: float = 0.4,
        max_frames: int = 12,
        start: str | None = None,
        end: str | None = None,
        source_name: str = "",
        analysis_width: int = _ANALYSIS_WIDTH,
        embed_width: int = _EMBED_WIDTH,
        save: bool = True,
        return_images: bool = True,
    ) -> Any:
        """Sample frames from a YouTube video as HD images (slides, charts, code).

        Downloads the stream (≤720p) with yt-dlp and extracts frames with
        ffmpeg, either where the picture changes (``scene``) or at a fixed
        cadence (``interval``). Saved HD frames land in the vault's central
        screenshots folder with an Obsidian ``embed`` snippet that names the
        timestamp, and a downscaled copy is returned for captioning.

        Requires the ``ffmpeg`` system binary on PATH; without it you get a
        clear error, not a crash.

        Args:
            url: YouTube video URL (any common form).
            mode: ``"scene"`` (default) grabs frames at scene changes;
                ``"interval"`` grabs one every ``interval_seconds``.
            interval_seconds: Seconds between frames for ``mode="interval"``.
            scene_threshold: ffmpeg scene-change sensitivity 0–1 for
                ``mode="scene"`` (default ``0.4``; lower = more frames).
            max_frames: Hard cap on frames (default ``12``) to bound cost.
            start: Optional clip start (``"mm:ss"`` or seconds).
            end: Optional clip end (``"mm:ss"`` or seconds).
            source_name: Label for filenames/captions; defaults to the video id.
            analysis_width: Width (px) of the downscaled model copy (default 1024).
            embed_width: Display width (px) baked into the Obsidian embed so frames
                show as thumbnails, not full-screen (default ``480``; ``0`` = full).
            save: Write HD frames into the vault (default ``true``).
            return_images: Attach downscaled copies as image content (default true).
        """
        from tools.youtube import parse_video_id

        video_id = parse_video_id(url)
        if not video_id:
            return {"error": f"Could not parse a YouTube video id from URL: {url!r}"}

        ffmpeg = find_ffmpeg()
        if not ffmpeg:
            return {
                "error": (
                    "ffmpeg is required for get_youtube_frames but could not be "
                    "found. It normally ships with the package via imageio-ffmpeg; "
                    "reinstall deps (`uv sync` or `pip install -r requirements.txt`), "
                    "or install a system ffmpeg (https://ffmpeg.org/download.html). "
                    "The PDF path (capture_pdf_page) needs no system binaries."
                )
            }
        if mode not in ("scene", "interval"):
            return {"error": f"mode must be 'scene' or 'interval', got {mode!r}"}

        start_s = _parse_clock(start)
        end_s = _parse_clock(end)
        label = _safe_stem(source_name) if source_name else f"YouTube {video_id}"
        folder = get_screenshots_folder()
        if save:
            try:  # fail fast (before downloading) on a bad screenshots folder
                resolve_image_target(f"{folder}/probe.png")
            except ValueError as exc:
                return {"error": str(exc)}

        with tempfile.TemporaryDirectory(prefix="ytframes-") as tmp:
            tmpdir = Path(tmp)
            try:
                video_file = _download_youtube(url, tmpdir)
            except Exception as exc:  # noqa: BLE001
                return {"error": f"Could not download video {video_id}: {exc}"}

            # Build the ffmpeg filter. For scene mode we append `showinfo`, which
            # logs each kept frame's pts_time to stderr — recovered afterwards. We
            # deliberately avoid metadata=print:file=... because a Windows path's
            # drive colon collides with ffmpeg's filtergraph option syntax.
            if mode == "scene":
                vf = f"select='gt(scene,{scene_threshold})',showinfo"
                loglevel = "info"  # showinfo prints at info level
            else:
                vf = f"fps=1/{max(1, interval_seconds)}"
                loglevel = "error"

            out_pattern = tmpdir / "frame-%04d.png"
            cmd = [ffmpeg, "-hide_banner", "-loglevel", loglevel]
            if start_s is not None:
                cmd += ["-ss", str(start_s)]
            cmd += ["-i", str(video_file)]
            if end_s is not None:
                duration = end_s - (start_s or 0)
                if duration > 0:
                    cmd += ["-t", str(duration)]
            cmd += ["-vf", vf, "-vsync", "vfr", str(out_pattern)]

            try:
                proc = subprocess.run(cmd, check=True, capture_output=True, timeout=900)
            except subprocess.CalledProcessError as exc:
                return {"error": f"ffmpeg failed: {exc.stderr.decode(errors='replace')[:500]}"}
            except subprocess.TimeoutExpired:
                return {"error": "ffmpeg timed out extracting frames (video too long?)."}

            frame_files = sorted(tmpdir.glob("frame-*.png"))
            if not frame_files:
                return {
                    "error": (
                        "No frames were extracted. For a low-motion video try a "
                        "lower scene_threshold, or use mode='interval'."
                    )
                }

            # Evenly sample down to max_frames if ffmpeg produced more.
            timestamps = (
                _showinfo_timestamps(proc.stderr.decode(errors="replace"))
                if mode == "scene"
                else None
            )
            if len(frame_files) > max_frames:
                step = len(frame_files) / max_frames
                keep = [round(i * step) for i in range(max_frames)]
                frame_files = [frame_files[min(i, len(frame_files) - 1)] for i in keep]
                if timestamps:
                    timestamps = [timestamps[min(i, len(timestamps) - 1)] for i in keep]

            records: list[dict[str, Any]] = []
            images: list[Image] = []
            for i, fpath in enumerate(frame_files):
                if mode == "scene" and timestamps and i < len(timestamps):
                    secs = timestamps[i]
                elif mode == "interval":
                    secs = (start_s or 0) + i * interval_seconds
                else:
                    secs = start_s or 0
                clock = _fmt_clock(secs)
                locator = f"@ {clock}"
                filename = f"{label} {clock.replace(':', 'm', 1)}.png"
                png_bytes = fpath.read_bytes()
                record, image = _save_and_block(
                    png_bytes, folder, filename, label, locator,
                    analysis_width, save, return_images, embed_width,
                )
                record["timestamp_seconds"] = round(secs, 1)
                record["timestamp"] = clock
                records.append(record)
                if image is not None:
                    images.append(image)

        summary = {
            "video_id": video_id,
            "source": label,
            "mode": mode,
            "frame_count": len(records),
            "saved_folder": folder if save else None,
            "images": records,
        }
        return _result(summary, images, return_images)

    @mcp.tool()
    def crop_screenshot(
        image_path: str,
        region: str,
        replace: bool = False,
        analysis_width: int = _ANALYSIS_WIDTH,
        embed_width: int = _EMBED_WIDTH,
        return_image: bool = True,
    ) -> Any:
        """Crop an already-saved screenshot down to just the part you want.

        The "fix it after the fact" companion to the capture tools: when a grab
        kept too much (margins, a header, a second figure), view the saved image
        and crop to the region worth keeping — no need to re-render the PDF or
        re-download the video. Iterate as needed (crop, look, crop again).

        Args:
            image_path: Vault-relative path to the saved image — the
                ``saved_path`` a capture returned, e.g.
                ``"2 - Source Material/Screenshots/Gray's Anatomy p.12.png"``.
            region: The part to KEEP, as comma-separated fractions
                ``"x0,y0,x1,y1"`` in 0–1 of the current image (e.g.
                ``"0,0.1,1,0.6"`` keeps a horizontal band; ``"0.25,0.25,0.75,0.75"``
                keeps the centre).
            replace: If true, overwrite the original file. If false (default),
                write a new ``"<name> cropped.png"`` next to it and keep the original.
            analysis_width: Width (px) of the downscaled copy returned for review
                (default ``1024``).
            embed_width: Display width (px) for the Obsidian embed (default ``480``).
            return_image: Attach the cropped result as image content (default ``true``).
        """
        from PIL import Image as PILImage

        try:
            src = resolve_in_vault(image_path)
        except ValueError as exc:
            return {"error": str(exc)}
        if not src.exists() or not src.is_file():
            return {"error": f"Image not found: {image_path!r}"}
        if src.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
            return {"error": f"Not an image file: {image_path!r}"}

        try:
            fractions = [float(v) for v in region.split(",")]
            if len(fractions) != 4:
                raise ValueError
        except ValueError:
            return {
                "error": (
                    f"region must be four comma-separated fractions 'x0,y0,x1,y1' "
                    f"in 0-1, got: {region!r}"
                )
            }
        x0, y0, x1, y1 = fractions
        if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
            return {
                "error": (
                    "region fractions must satisfy 0<=x0<x1<=1 and 0<=y0<y1<=1 "
                    f"(x0,y0 = top-left corner to keep), got: {region!r}"
                )
            }

        try:
            with PILImage.open(src) as im:
                w, h = im.size
                box = (round(x0 * w), round(y0 * h), round(x1 * w), round(y1 * h))
                png_bytes = _encode_png(im.crop(box))
        except Exception as exc:  # noqa: BLE001
            return {"error": f"Could not crop image: {exc}"}

        if replace:
            try:
                target = resolve_image_target(image_path)
            except ValueError as exc:
                return {"error": str(exc)}
        else:
            folder = src.parent.relative_to(get_vault_path()).as_posix()
            try:
                target = _unique_target(folder, f"{src.stem} cropped.png")
            except ValueError as exc:
                return {"error": str(exc)}
        target.write_bytes(png_bytes)

        rel = target.relative_to(get_vault_path()).as_posix()
        record: dict[str, Any] = {
            "saved_path": rel,
            "filename": target.name,
            "cropped_from": image_path,
            "replaced_original": replace,
            "embed": _embed_snippet(target.name, src.stem, "cropped", embed_width),
        }
        image = None
        if return_image:
            image = Image(data=_downscale_png(png_bytes, analysis_width), format="png")
        return _result({"cropped": True, "images": [record]},
                       [image] if image else [], return_image)


# --------------------------------------------------------------------------- #
# YouTube-frame internals (kept module-level so the tool body stays readable)
# --------------------------------------------------------------------------- #

def _download_youtube(url: str, tmpdir: Path) -> Path:
    """Download ``url`` (≤720p) into ``tmpdir`` and return the video file path."""
    import yt_dlp

    opts = {
        "format": "bestvideo[height<=720]/best[height<=720]/best",
        "outtmpl": str(tmpdir / "source.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    files = [p for p in tmpdir.iterdir() if p.name.startswith("source.")]
    if not files:
        raise RuntimeError("yt-dlp produced no output file.")
    return files[0]


def _showinfo_timestamps(stderr: str) -> list[float]:
    """Parse each kept frame's ``pts_time`` from ffmpeg ``showinfo`` log output.

    Used by scene mode so every saved frame's caption can name the exact
    timestamp it was taken from. Order matches the emitted frames.
    """
    return [float(m) for m in re.findall(r"pts_time:([0-9.]+)", stderr)]
