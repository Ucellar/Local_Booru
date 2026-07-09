"""Architecture constants for Local Booru Next.

This module is intentionally boring. All high-level layers import these limits
instead of inventing their own values. The goal is to keep 100k-500k libraries
stable by default:

* UI never scans folders or hashes files directly.
* SQLite is the source of truth.
* Full-file MD5 is opt-in and task-based.
* Gallery loads rows in pages, not as one giant Python list.
"""

SQL_PAGE_DEFAULT = 120
SQL_PAGE_MAX = 500
DB_BATCH_SIZE = 500
INDEX_BATCH_COMMIT = 300
THUMBNAIL_MEMORY_CACHE = 320
MAX_IMAGE_PIXELS = 300_000_000
DEFAULT_TASK_WORKERS = 2

# Expensive operations are disabled unless a user presses a button that clearly
# starts a background job.
AUTO_INDEX_ON_GALLERY_OPEN = False
AUTO_MD5_DURING_INDEX = False
