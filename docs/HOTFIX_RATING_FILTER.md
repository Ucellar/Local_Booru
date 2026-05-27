# Hotfix: star rating save/filter

Fixed a bug where the star rating widget could appear to accept a rating, but the
rating was not saved for posts opened without a resolved SQLite image id.

Changes:
- Post view now loads rating every time a post is rendered.
- Rating save now resolves/creates the SQLite image row by path if needed.
- Gallery rating filter reads the same `images.rating` value that the post view writes.
- Added public `ensure_image()` helper in `core.database.storage`.

Test:
1. Open a post from Gallery.
2. Set 5 stars.
3. Return to Gallery.
4. Select the 5-star filter.
5. The rated image should appear.
