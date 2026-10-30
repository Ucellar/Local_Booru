"""VP-tree for fast perceptual hash similarity search.

Adapted from Hydrus Network's ClientDBSimilarFiles approach.
Stores tree in SQLite, supports O(log n) nearest-neighbor search.

Hamming distance on 64-bit phash: 0 = identical, 64 = completely different.
Typical "similar" threshold: distance <= 10 (Hydrus default).
"""
from __future__ import annotations
import struct
from typing import Iterator


# ── Hamming distance (Hydrus algorithm) ──────────────────────────────────────

def hamming(a: str, b: str) -> int:
    """Hamming distance between two hex phash strings (0-64)."""
    try:
        ia = int(a, 16)
        ib = int(b, 16)
        return bin(ia ^ ib).count("1")
    except Exception:
        return 64


def similarity_pct(a: str, b: str) -> float:
    """0-100% similarity from phash strings."""
    return max(0.0, 100.0 * (1.0 - hamming(a, b) / 64.0))


# ── VP-tree operations ────────────────────────────────────────────────────────

class VPTree:
    """VP-tree backed by SQLite ``vp_tree`` table.

    The earlier experimental version stored nodes but did not wire child
    pointers, so searches silently degraded.  This implementation keeps the
    table small and deterministic: one root, recursive insert, explicit
    inner/outer child links, and safe fallback rebuild helpers.
    """

    def __init__(self, conn):
        self.conn = conn

    def _execute(self, sql, params=()):
        return self.conn.execute(sql, params)

    def _fetch(self, sql, params=()):
        return self._execute(sql, params).fetchone()

    def _main_writes_enabled(self) -> bool:
        try:
            from core.derived_cache_db import main_cache_writes_enabled
            return bool(main_cache_writes_enabled(self.conn, "vp_tree"))
        except Exception:
            return True

    def insert(self, image_id: int, phash: str) -> None:
        """Add image to the legacy main-DB tree when that backend is selected.

        External generation files are immutable.  Primary writes only advance
        ``derived_source_revision``; the next maintenance rebuild publishes a
        new generation instead of mutating cache.db inside the primary txn.
        """
        if not self._main_writes_enabled():
            return
        if not phash or len(phash) != 16:
            return
        existing = self._fetch("SELECT node_id, phash FROM vp_tree WHERE image_id=?", (image_id,))
        if existing:
            if str(existing[1] or "") != str(phash):
                # A changed image cannot be updated in-place without breaking VP partitions.
                # Rebuild is rare (only changed content) and keeps similarity results correct.
                self.rebuild()
            return
        root = self._fetch("SELECT node_id FROM vp_tree WHERE parent_id IS NULL LIMIT 1")
        if root is None:
            self._execute(
                "INSERT INTO vp_tree (image_id, phash, parent_id, inner_pop, outer_pop) VALUES(?,?,NULL,0,0)",
                (image_id, phash),
            )
            return
        self._insert_under(int(root[0]), image_id, phash)

    def _new_leaf(self, image_id: int, phash: str, parent_id: int) -> int:
        cur = self._execute(
            "INSERT INTO vp_tree (image_id, phash, parent_id, inner_pop, outer_pop) VALUES(?,?,?,?,?)",
            (image_id, phash, parent_id, 0, 0),
        )
        return int(cur.lastrowid)

    def _insert_under(self, node_id: int, image_id: int, phash: str) -> None:
        while True:
            row = self._fetch(
                "SELECT phash, radius, inner_id, outer_id FROM vp_tree WHERE node_id=?",
                (node_id,),
            )
            if row is None:
                return
            node_phash, radius, inner_id, outer_id = row
            dist = hamming(phash, node_phash)

            # First child defines the split radius at this vantage point.
            if radius is None:
                radius = float(max(1, dist))
                self._execute("UPDATE vp_tree SET radius=? WHERE node_id=?", (radius, node_id))

            if dist <= float(radius):
                if inner_id is None:
                    child = self._new_leaf(image_id, phash, node_id)
                    self._execute(
                        "UPDATE vp_tree SET inner_id=?, inner_pop=inner_pop+1 WHERE node_id=?",
                        (child, node_id),
                    )
                    return
                self._execute("UPDATE vp_tree SET inner_pop=inner_pop+1 WHERE node_id=?", (node_id,))
                node_id = int(inner_id)
            else:
                if outer_id is None:
                    child = self._new_leaf(image_id, phash, node_id)
                    self._execute(
                        "UPDATE vp_tree SET outer_id=?, outer_pop=outer_pop+1 WHERE node_id=?",
                        (child, node_id),
                    )
                    return
                self._execute("UPDATE vp_tree SET outer_pop=outer_pop+1 WHERE node_id=?", (node_id,))
                node_id = int(outer_id)

    def search(self, phash: str, max_distance: int = 10) -> list[tuple[int, int]]:
        """Return ``[(image_id, distance)]`` without recursive Python calls.

        A sequentially built VP-tree can become deep for adversarially ordered
        hashes.  Candidate scans may traverse thousands of nodes, so an explicit
        stack avoids ``RecursionError`` while preserving the same pruning rules.
        """
        if not phash or len(phash) != 16:
            return []
        root = self._fetch("SELECT node_id FROM vp_tree WHERE parent_id IS NULL LIMIT 1")
        if root is None:
            return []
        max_dist = max(0, min(64, int(max_distance)))
        results: list[tuple[int, int]] = []
        stack = [int(root[0])]
        visited: set[int] = set()
        while stack:
            node_id = int(stack.pop())
            if node_id in visited:
                # Structural validation should prevent loops, but never let a
                # damaged cache hang a foreground lookup or maintenance scan.
                continue
            visited.add(node_id)
            row = self._fetch(
                "SELECT image_id, phash, radius, inner_id, outer_id FROM vp_tree WHERE node_id=?",
                (node_id,),
            )
            if row is None:
                continue
            img_id, node_phash, radius, inner_id, outer_id = row
            dist = hamming(phash, node_phash)
            if dist <= max_dist:
                results.append((int(img_id), int(dist)))
            if radius is None:
                continue
            radius = float(radius)
            # Push outer first so inner is visited first, matching the old
            # recursive traversal. Final sorting makes result order deterministic.
            if outer_id is not None and dist >= radius - max_dist:
                stack.append(int(outer_id))
            if inner_id is not None and dist <= radius + max_dist:
                stack.append(int(inner_id))
        results.sort(key=lambda x: (x[1], x[0]))
        return results

    def rebuild(self, progress_cb=None) -> int:
        """Rebuild the legacy main-DB tree when its backend is selected."""
        if not self._main_writes_enabled():
            return 0
        self._execute("DELETE FROM vp_tree")
        rows = self._execute(
            "SELECT id, hash_phash FROM images WHERE deleted=0 AND hash_phash IS NOT NULL AND hash_phash != ''"
        ).fetchall()
        for i, (img_id, ph) in enumerate(rows):
            self.insert(int(img_id), str(ph))
            if progress_cb and i % 500 == 0:
                progress_cb(i, len(rows))
        return len(rows)

    def ensure_fresh_enough(self) -> None:
        """Cheap self-heal for the legacy main-DB backend only."""
        if not self._main_writes_enabled():
            return
        try:
            img_count = int(self._fetch("SELECT COUNT(*) FROM images WHERE deleted=0 AND hash_phash IS NOT NULL AND hash_phash != ''")[0] or 0)
            tree_count = int(self._fetch("SELECT COUNT(*) FROM vp_tree")[0] or 0)
            if img_count != tree_count:
                self.rebuild()
                return
            mismatch = self._fetch(
                "SELECT 1 FROM images i JOIN vp_tree v ON v.image_id=i.id "
                "WHERE i.deleted=0 AND i.hash_phash IS NOT NULL AND i.hash_phash!='' "
                "AND v.phash != i.hash_phash LIMIT 1"
            )
            if mismatch is not None:
                self.rebuild()
        except Exception:
            pass


# ── Duplicate groups ──────────────────────────────────────────────────────────

def build_duplicate_groups(conn, max_distance: int = 10,
                            progress_cb=None) -> int:
    """Find all duplicate groups using VP-tree search.

    Uses connected-components algorithm:
    - For each image, find similar images
    - Merge into groups (union-find)
    Returns number of groups created.
    """
    tree = VPTree(conn)

    # Get all images with phash
    rows = conn.execute(
        "SELECT id, hash_phash FROM images WHERE hash_phash IS NOT NULL AND hash_phash != ''"
    ).fetchall()

    # Union-Find
    parent: dict[int, int] = {}

    def find(x):
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent.get(x, x), parent.get(x, x))
            x = parent.get(x, x)
        return x

    def union(a, b):
        a, b = find(a), find(b)
        if a != b:
            parent[b] = a

    edges: list[tuple[int, int, float]] = []

    for i, (img_id, phash) in enumerate(rows):
        if progress_cb and i % 200 == 0:
            progress_cb(i, len(rows))
        matches = tree.search(phash, max_distance)
        for other_id, dist in matches:
            if other_id != img_id:
                sim = max(0.0, 100.0 * (1.0 - dist / 64.0))
                edges.append((img_id, other_id, sim))
                union(img_id, other_id)

    # Assign group IDs
    root_to_gid: dict[int, int] = {}
    gid = 0
    conn.execute("DELETE FROM dup_groups")
    for img_id, _ in rows:
        root = find(img_id)
        if root not in root_to_gid:
            root_to_gid[root] = gid
            gid += 1
    # Only store groups with >1 member
    root_count: dict[int, int] = {}
    for img_id, _ in rows:
        r = find(img_id)
        root_count[r] = root_count.get(r, 0) + 1

    inserted = 0
    for img_id, _ in rows:
        r = find(img_id)
        if root_count.get(r, 1) > 1:
            g = root_to_gid[r]
            # Find best similarity for this member
            best_sim = 100.0
            for a, b, sim in edges:
                if (a == img_id or b == img_id):
                    best_sim = min(best_sim, 100.0 - (100.0 - sim))
            conn.execute(
                "INSERT OR REPLACE INTO dup_groups (group_id, image_id, similarity) VALUES (?,?,?)",
                (g, img_id, best_sim)
            )
            inserted += 1

    conn.commit()
    return gid


# ── Tag siblings ──────────────────────────────────────────────────────────────

def resolve_tag(conn, tag: str) -> str:
    """Resolve tag to its canonical form via siblings table."""
    row = conn.execute(
        "SELECT canonical FROM tag_siblings WHERE tag=?", (tag,)
    ).fetchone()
    return row[0] if row else tag


def add_sibling(conn, tag: str, canonical: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO tag_siblings (tag, canonical) VALUES (?,?)",
        (tag.strip().lower(), canonical.strip().lower())
    )
    conn.commit()


def remove_sibling(conn, tag: str) -> None:
    conn.execute("DELETE FROM tag_siblings WHERE tag=?", (tag.strip().lower(),))
    conn.commit()


def get_all_siblings(conn) -> list[tuple[str, str]]:
    return conn.execute("SELECT tag, canonical FROM tag_siblings ORDER BY canonical, tag").fetchall()
