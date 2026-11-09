"""Read current reviews consistently and retain superseded review attempts."""
import json
from pathlib import Path
import re
import shutil

import yaml

from artifact_utils import read_frontmatter_validated, ValidationError


def load_review(path):
    """Return (passed/failed/error, metadata), never approving invalid input.

    Technical errors belong to the error-retry batch; a valid quality failure
    belongs to the bounded revision loop. This is not a mid-write poller.
    """
    try:
        data, _ = read_frontmatter_validated(str(path), "decomp-review")
    except (OSError, ValueError, TypeError, AttributeError,
            yaml.YAMLError, ValidationError) as exc:
        return "error", {"error": f"Cannot read review: {exc}"}
    if data.get("error"):
        return "error", data
    path = Path(path)
    match = re.fullmatch(r"(RHAISTRAT-\d+)-decomp-review\.md", path.name)
    expected_id = match.group(1) if match else None
    if path.parent.parent.name == "history":
        expected_id = path.parent.name
    if expected_id and data["strat_id"] != expected_id:
        return "error", {**data, "error": "Review strategy does not match its artifact path"}
    score = data["score"]
    if type(score) is not int or not 0 <= score <= 14:
        return "error", {**data, "error": "Review score must be an integer from 0 to 14"}
    passed = (data["pass"] is True and score >= 10
              and data["recommendation"] == "accept")
    return "passed" if passed else "failed", data


def archive_review(strat_id, revision_changed=None, artifacts_dir="artifacts"):
    """Move the canonical review aside so polling requires a fresh result.

    Attempt numbers are local to a strategy and never reused across batches or
    the error-retry batch. None means no revision outcome is known.
    """
    review = Path(artifacts_dir) / "epic-reviews" / f"{strat_id}-decomp-review.md"
    if not review.exists():
        return None
    status, _ = load_review(review)
    history = review.parent / "history" / strat_id
    history.mkdir(parents=True, exist_ok=True)
    attempt = 1
    while ((history / f"attempt-{attempt:04d}.md").exists()
           or (history / f"attempt-{attempt:04d}.json").exists()):
        attempt += 1
    destination = history / f"attempt-{attempt:04d}.md"
    # Record the intervening revision, not a claim of byte-identical inputs.
    metadata = {"status": status, "revision_changed": revision_changed}
    destination.with_suffix(".json").write_text(json.dumps(metadata) + "\n")
    shutil.move(str(review), destination)
    return destination


def unchanged_review_recoveries(strat_id, artifacts_dir="artifacts"):
    """Count consecutive quality-failed -> passed attempts without reported edits.

    These events do not prove a reviewer false negative: model judgments or
    concurrently written inputs may differ even if revision reported no edits.
    """
    directory = Path(artifacts_dir) / "epic-reviews"
    attempts = sorted((directory / "history" / strat_id).glob("attempt-*.md"))
    current = directory / f"{strat_id}-decomp-review.md"
    count = 0
    for index, previous in enumerate(attempts):
        try:
            metadata = json.loads(previous.with_suffix(".json").read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(metadata, dict):
            continue
        following = attempts[index + 1] if index + 1 < len(attempts) else current
        if (metadata.get("revision_changed") is False
                and load_review(previous)[0] == "failed"
                and load_review(following)[0] == "passed"):
            count += 1
    return count
