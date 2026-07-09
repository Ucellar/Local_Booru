from __future__ import annotations

import html
import re
from urllib.parse import parse_qs, urlparse, unquote as _url_unquote

from bs4 import BeautifulSoup


def atf_find_post_view_url_from_html(html_text, base_url="https://booru.allthefallen.moe", md5_hash=""):
    """Find an ATF post-view URL from search/card HTML without trusting visible labels."""
    base_url = (base_url or "https://booru.allthefallen.moe").rstrip("/")
    html_text = html_text or ""

    def build_url(post_id):
        post_id = str(post_id).strip()
        if not post_id or not post_id.isdigit():
            return ""
        url = f"{base_url}/posts/{post_id}"
        if md5_hash:
            url += "?q=md5%3A" + str(md5_hash)
        return url

    try:
        variants = []
        for v in [html_text, html.unescape(html_text), _url_unquote(html_text)]:
            if v and v not in variants:
                variants.append(v)
        candidates = []
        for variant in variants:
            soup = BeautifulSoup(variant, "html.parser")
            for a in soup.find_all("a", href=True):
                href = str(a.get("href", ""))
                m = re.search(r"/posts/(\d+)(?:[/?#][^\"\' <]*)?", href)
                if m:
                    candidates.append(m.group(1))
            for el in soup.find_all(True):
                attrs = getattr(el, "attrs", {}) or {}
                cls = " ".join(attrs.get("class", [])) if isinstance(attrs.get("class"), list) else str(attrs.get("class", ""))
                for key, val in attrs.items():
                    k = str(key).lower()
                    v = str(val)
                    if "post" in k and "id" in k:
                        m = re.search(r"\d{2,}", v)
                        if m:
                            candidates.append(m.group(0))
                    if k in {"data-id", "id"} or "post" in cls.lower():
                        m = re.search(r"(?:post[_-]?)?(\d{2,})", v)
                        if m:
                            candidates.append(m.group(1))
            raw_patterns = [
                r"/posts/(\d+)(?:[/?#][^\"\' <]*)?", r"\\/posts\\/(\d+)", r"%2Fposts%2F(\d+)",
                r"posts\\?/(\d+)", r"post[_-]?id[\"\'\s:=]+(\d{2,})", r"data-post-id[\"\'\s:=]+(\d{2,})",
                r"data-id[\"\'\s:=]+(\d{2,})", r"id=[\"']post[_-](\d{2,})[\"']", r"post\D{0,20}(\d{5,})",
            ]
            for pat in raw_patterns:
                for m in re.finditer(pat, variant, flags=re.I):
                    candidates.append(m.group(1))
        seen = set()
        for cid in candidates:
            cid = str(cid).strip()
            if not cid.isdigit() or int(cid) < 100 or cid in seen:
                continue
            seen.add(cid)
            return build_url(cid)
    except Exception:
        pass
    return ""


def atf_parse_post_view_html(html_text):
    """Parse an ATF post sidebar, accepting only tag-search href values."""
    groups = {
        "artist": [], "character": [], "copyright": [], "species": [], "general": [], "meta": [],
    }

    def tag_from_href(anchor):
        try:
            href = html.unescape(str(anchor.get("href", "")))
            vals = parse_qs(urlparse(href).query).get("tags", [])
            if not vals:
                return ""
            tag = html.unescape(str(vals[0])).strip().replace(" ", "_")
            if not tag or tag.startswith(("rating:", "sort:", "md5:", "user:", "score:")):
                return ""
            if tag.lower() in {"?", "posts", "post", "all"}:
                return ""
            return tag
        except Exception:
            return ""

    try:
        soup = BeautifulSoup(html_text or "", "html.parser")
        category_map = {"0": "general", "1": "artist", "3": "copyright", "4": "character", "5": "meta"}
        for cls_num, group_name in category_map.items():
            for el in soup.select(f".category-{cls_num} a.search-tag[href*='tags='], .category-{cls_num} a[href*='tags=']"):
                tag = tag_from_href(el)
                if tag and tag not in groups[group_name]:
                    groups[group_name].append(tag)
        named_classes = {
            "artist": "artist", "character": "character", "copyright": "copyright",
            "general": "general", "metadata": "meta", "meta": "meta", "species": "species",
        }
        for cls_part, group_name in named_classes.items():
            for el in soup.select(f".tag-type-{cls_part} a.search-tag[href*='tags='], .tag-type-{cls_part} a[href*='tags=']"):
                tag = tag_from_href(el)
                if tag and tag not in groups[group_name]:
                    groups[group_name].append(tag)
        all_tags = []
        for group_name in ("artist", "copyright", "character", "species", "general", "meta"):
            for tag in groups[group_name]:
                if tag not in all_tags:
                    all_tags.append(tag)
        return all_tags, {k: v for k, v in groups.items() if v}
    except Exception:
        return [], {}
