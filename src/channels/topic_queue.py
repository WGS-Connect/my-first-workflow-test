"""Persistent queue for the audiobook channel.

Each leaf topic folder is one complete YouTube production.

Queue behaviour:
- New topics start as ``pending``.
- A claimed topic becomes ``in_progress``.
- A stale ``in_progress`` claim can be recycled.
- Failed topics normally remain failed after max_attempts.
- If a book has no pending/in-progress topics but does contain valid
  failed topics, the oldest failed topic can be automatically requeued.
  This prevents a single-topic book from becoming permanently blocked
  after a previous failed GitHub Actions run.
- Queue state is persisted locally and can also be synchronized with Drive.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

from src.utils.io import read_json, write_json
from src.utils.locks import file_lock


STATE_FILE = "queue_state.json"
COMPLETED_FILE = "completed_topics.json"

COVER_EXTS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".bmp",
    ".tif",
    ".tiff",
    ".avif",
    ".svg",
    ".pdf",
}


def _lock_path(book: Path) -> Path:
    return Path(book) / ".topic_queue.lock"


def topic_dirs(book: Path) -> list[Path]:
    """Return valid audiobook topic directories."""

    book = Path(book)

    if not book.is_dir():
        return []

    out: list[Path] = []

    for p in sorted(book.iterdir()):
        if not p.is_dir():
            continue

        if p.name.startswith("."):
            continue

        if p.name.lower() in {"__pycache__", "_archive"}:
            continue

        # A topic folder must contain a topic marker, title, description,
        # hashtags, or a recognised cover file.
        has_metadata = any(
            x.is_file()
            and x.name.lower()
            in {
                "title.txt",
                "type.txt",
                "description.txt",
                "hashtags.txt",
            }
            for x in p.iterdir()
        )

        has_cover = any(
            x.is_file()
            and x.suffix.lower() in COVER_EXTS
            and x.stem.lower() in {
                "cover",
                "book-cover",
                "book_cover",
            }
            for x in p.iterdir()
        )

        if has_metadata or has_cover:
            out.append(p)

    return out


def topic_key(topic_dir: Path) -> str:
    return Path(topic_dir).name


def normalize(raw) -> dict:
    """Normalize queue JSON into the current queue format."""

    data = dict(raw) if isinstance(raw, dict) else {}

    items = {}

    for topic, entry in (data.get("topics") or {}).items():
        e = dict(entry)

        e.setdefault("status", "pending")
        e.setdefault("attempts", 0)
        e.setdefault("rev", 0)

        e.setdefault(
            "updated_at",
            e.get("last_failed_at")
            or e.get("claimed_at")
            or e.get("completed_at")
            or 0,
        )

        items[topic] = e

    return {
        "version": 4,
        "topics": items,
    }


def merge_queue(local, remote):
    """Merge local and Drive queue states safely."""

    a = normalize(local)
    b = normalize(remote)

    merged = {}

    for topic in set(a["topics"]) | set(b["topics"]):
        x = a["topics"].get(topic)
        y = b["topics"].get(topic)

        if x is None or y is None:
            merged[topic] = dict(x or y)
            continue

        # Completed always wins over a non-completed state.
        if x["status"] == "completed" or y["status"] == "completed":
            completed_entries = [
                e
                for e in (x, y)
                if e["status"] == "completed"
            ]

            winner = min(
                completed_entries,
                key=lambda e: e.get("completed_at", 0) or 1e18,
            )

        else:
            winner = max(
                (x, y),
                key=lambda e: (
                    e.get("rev", 0),
                    e.get("updated_at", 0),
                ),
            )

        entry = dict(winner)

        # Preserve the highest attempt count from either side.
        entry["attempts"] = max(
            int(x.get("attempts", 0)),
            int(y.get("attempts", 0)),
        )

        entry["rev"] = max(
            int(x.get("rev", 0)),
            int(y.get("rev", 0)),
        )

        merged[topic] = entry

    return {
        "version": 4,
        "topics": merged,
    }


def _load(book):
    """Load and normalize queue state."""

    return normalize(
        read_json(
            Path(book) / STATE_FILE,
            None,
        )
    )


def _save(book, data):
    """Save queue state and completed-topic index."""

    data = normalize(data)

    write_json(
        Path(book) / STATE_FILE,
        data,
    )

    write_json(
        Path(book) / COMPLETED_FILE,
        sorted(
            topic
            for topic, entry in data["topics"].items()
            if entry.get("status") == "completed"
        ),
    )


def _touch(entry, now):
    """Update queue revision and timestamp."""

    entry["rev"] = int(entry.get("rev", 0)) + 1
    entry["updated_at"] = now


def job_id(book: Path, topic: str) -> str:
    """Generate a stable job ID for a book/topic pair."""

    digest = hashlib.sha1(
        f"{Path(book).name}\0{topic}".encode()
    ).hexdigest()[:10]

    safe = "".join(
        c.lower() if c.isalnum() else "-"
        for c in f"{Path(book).name}-{topic}"
    ).strip("-")[:62]

    return f"audio-{safe}-{digest}"


def _requeue_failed_topic(entry, now, reason):
    """Reset a failed topic so it can be attempted again."""

    entry.update(
        status="pending",
        attempts=0,
        last_error=None,
        requeued_at=now,
        requeue_reason=reason,
    )

    _touch(entry, now)


def claim_book_topic(
    book,
    max_attempts=3,
    stale_seconds=21600,
    now=None,
):
    """Claim the next available topic from a book.

    The important recovery behaviour is:

    If all topics are currently failed/completed and there is at least
    one failed topic that is still physically present, the failed topic
    is automatically reset to pending.

    This prevents a single-topic book from becoming permanently blocked
    because of stale queue state from an earlier GitHub Actions run.
    """

    book = Path(book)
    now = time.time() if now is None else now

    with file_lock(_lock_path(book)):
        data = _load(book)
        dirs = topic_dirs(book)
        names = [topic_key(x) for x in dirs]

        if not names:
            _save(book, data)
            return None

        # ------------------------------------------------------------------
        # STEP 1
        # Recover stale in-progress jobs.
        # ------------------------------------------------------------------

        chosen = None

        for topic in names:
            e = data["topics"].get(topic)

            if e is None:
                continue

            status = e.get("status", "pending")
            attempts = int(e.get("attempts", 0))

            if status != "in_progress":
                continue

            updated_at = float(
                e.get("updated_at")
                or e.get("claimed_at")
                or 0
            )

            age = now - updated_at

            if age > stale_seconds:
                attempts += 1

                e["attempts"] = attempts
                e["recycled"] = int(
                    e.get("recycled", 0)
                ) + 1

                e["last_error"] = "recycled stale claim"

                if attempts >= max_attempts:
                    e["status"] = "failed"
                    _touch(e, now)
                    continue

                e.update(
                    status="in_progress",
                    claimed_at=now,
                    last_error=None,
                )

                _touch(e, now)

            elif attempts >= max_attempts:
                # A previous run exhausted its attempts.
                e["status"] = "failed"
                _touch(e, now)
                continue

            # Existing active job is still claimable.
            chosen = topic
            break

        # ------------------------------------------------------------------
        # STEP 2
        # Claim a normal pending topic.
        # ------------------------------------------------------------------

        if chosen is None:
            for topic in names:
                e = data["topics"].get(topic)

                if e is None:
                    e = {
                        "status": "pending",
                        "attempts": 0,
                        "rev": 0,
                        "updated_at": now,
                    }

                if e.get("status", "pending") != "pending":
                    data["topics"][topic] = e
                    continue

                e.update(
                    status="in_progress",
                    job_id=job_id(book, topic),
                    claimed_at=now,
                    last_error=None,
                )

                _touch(e, now)

                data["topics"][topic] = e
                chosen = topic
                break

        # ------------------------------------------------------------------
        # STEP 3
        # IMPORTANT RECOVERY
        #
        # If there are no pending/in-progress topics but valid failed topics
        # exist, automatically requeue one.
        #
        # This is what fixes:
        #
        #   ConfigError: No claimable topic in ...
        #
        # for books such as Rich Dad Poor Dad that contain only Topic 001.
        # ------------------------------------------------------------------

        if chosen is None:
            failed = []

            for topic in names:
                e = data["topics"].get(topic)

                if not e:
                    continue

                if e.get("status") != "failed":
                    continue

                failed.append(
                    (
                        float(
                            e.get("updated_at")
                            or e.get("last_failed_at")
                            or 0
                        ),
                        topic,
                        e,
                    )
                )

            if failed:
                # Requeue the oldest failed topic first.
                failed.sort(key=lambda item: (item[0], item[1]))

                _, topic, e = failed[0]

                _requeue_failed_topic(
                    e,
                    now,
                    "automatic recovery of previously failed topic",
                )

                e.update(
                    status="in_progress",
                    job_id=job_id(book, topic),
                    claimed_at=now,
                    last_error=None,
                )

                _touch(e, now)

                data["topics"][topic] = e
                chosen = topic

        # ------------------------------------------------------------------
        # STEP 4
        # Save the resulting queue state.
        # ------------------------------------------------------------------

        _save(book, data)

        return chosen


def pick_book(root):
    """Return a book containing work that can be processed."""

    root = Path(root)

    if not root.is_dir():
        return None

    for book in sorted(
        p
        for p in root.iterdir()
        if p.is_dir() and not p.name.startswith(".")
    ):
        dirs = topic_dirs(book)

        if not dirs:
            continue

        data = _load(book)

        statuses = [
            data["topics"]
            .get(topic_key(topic), {})
            .get("status", "pending")
            for topic in dirs
        ]

        # Normal queue states.
        if any(
            status in ("pending", "in_progress")
            for status in statuses
        ):
            return book

        # Recovery:
        # If the book contains valid topics but all of them are failed,
        # return the book so claim_book_topic() can automatically requeue
        # one failed topic.
        if any(status == "failed" for status in statuses):
            return book

    return None


def topic_path(book: Path, topic: str) -> Path:
    """Return the filesystem path of a topic."""

    p = Path(book) / topic

    if not p.is_dir():
        raise FileNotFoundError(
            f"Topic folder not found: {p}"
        )

    return p


def mark_done(book, topic, now=None):
    """Mark a topic as successfully completed."""

    book = Path(book)
    now = time.time() if now is None else now

    with file_lock(_lock_path(book)):
        data = _load(book)

        e = data["topics"].setdefault(
            topic,
            {
                "attempts": 0,
                "rev": 0,
            },
        )

        e.update(
            status="completed",
            completed_at=now,
            last_error=None,
        )

        _touch(e, now)

        _save(book, data)


def mark_failed(
    book,
    topic,
    error,
    max_attempts=3,
    now=None,
):
    """Record a failed production attempt."""

    book = Path(book)
    now = time.time() if now is None else now

    with file_lock(_lock_path(book)):
        data = _load(book)

        e = data["topics"].setdefault(
            topic,
            {
                "attempts": 0,
                "rev": 0,
                "status": "in_progress",
            },
        )

        e["attempts"] = int(
            e.get("attempts", 0)
        ) + 1

        e["last_error"] = str(error)[:2000]
        e["last_failed_at"] = now

        if e["attempts"] >= max_attempts:
            e["status"] = "failed"
        else:
            # Keep it eligible for another attempt.
            e["status"] = "in_progress"

        _touch(e, now)

        _save(book, data)

        return e["status"]


def requeue(book, topic=None, now=None):
    """Manually reset failed topics to pending."""

    book = Path(book)
    now = time.time() if now is None else now

    revived = []

    with file_lock(_lock_path(book)):
        data = _load(book)

        for name, e in data["topics"].items():
            if e.get("status") != "failed":
                continue

            if topic not in (None, name):
                continue

            e.update(
                status="pending",
                attempts=0,
                last_error=None,
                requeued_at=now,
                requeue_reason="manual requeue",
            )

            _touch(e, now)

            revived.append(name)

        _save(book, data)

    return revived


def sync_queue(book, drive, channel="audiobook"):
    """Synchronize local queue state with the persistent Drive queue."""

    book = Path(book)

    with file_lock(_lock_path(book)):
        merged = drive.sync_json(
            (
                "queue",
                channel,
                f"{book.name}.json",
            ),
            book / STATE_FILE,
            merge_queue,
        )

        normalized = normalize(merged)

        _save(
            book,
            normalized,
        )

        return normalized
