"""Plain data objects passed between sites, reverse services and the engine."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from core.tagger.tag_groups import empty_tag_groups, groups_to_tags


@dataclass
class Post:
    """One post on one booru, with tags already split by category."""

    site: str                 # canonical host, e.g. "danbooru.donmai.us"
    label: str                # what the user sees in logs, usually the host
    post_id: str
    url: str                  # concrete post page URL (stored as the source)
    md5: str = ""             # original file MD5 published by the site
    groups: dict = field(default_factory=empty_tag_groups)
    rating: str = ""
    sources: list = field(default_factory=list)
    preview_url: str = ""
    file_url: str = ""
    meta: dict = field(default_factory=dict)   # parent, uploader, source links (core/site_posts.py ``fields``)

    @property
    def tags(self) -> list:
        return groups_to_tags(self.groups)


@dataclass
class Match:
    """A post accepted for a local file, with the reason it was accepted."""

    post: Post
    method: str               # md5, filename_md5, md5_relay, pixiv_relay, saucenao, iqdb, ...
    similarity: float = 100.0
    verified: str = ""        # "md5", "visual d=3", "unverified", ...


@dataclass
class Candidate:
    """A reverse-search hit before it is resolved to a booru post."""

    url: str
    similarity: float
    service: str
    title: str = ""
    pixiv_id: str = ""
    preview_url: str = ""     # thumbnail of the hit, when the service shows one
    # (v5.44.36) under the run's similarity (``ReverseService.weak_floor`` and up): only a video's hit, and only with
    # a preview nearly the same as one of its frames (``engine._accept`` ``strict``) — e621 IQDB scores a video by the
    # frame of its preview, and the sites' previews of old posts were made of other frames (68–75 % for true finds)
    weak: bool = False


@dataclass
class ReverseOutcome:
    """Result of one reverse-search service for one file."""

    status: str               # complete | deferred | failed | bad_input | disabled (see reverse/base.py)
    candidates: list = field(default_factory=list)
    source_only: list = field(default_factory=list)   # dicts: url, label, similarity
    retry_after: float = 0.0
    detail: str = ""


@dataclass
class FileResult:
    path: Path
    status: str               # tagged | nomatch | skip | deferred | error
    reason: str = ""
    matches: list = field(default_factory=list)
    tag_count: int = 0
    retry_at: float = 0.0     # deferred: not worth asking again before this time (epoch seconds; 0 — after pauses)
    md5: str = ""             # the file's MD5, when it was read (found and Брак: the page links the card by it)
