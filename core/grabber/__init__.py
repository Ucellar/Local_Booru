"""The grabber of the new interface: search booru sites by tags with the parser's own site clients
(``core/parser/sites``), merge the posts of one file found on several sites into one card, mark
what the library already has, show previews through the local server, download through the
subscription import (originals only, tags from every site of the card).

- ``cards``  — a card from a post: media URLs (preview / sample / original), size, kind;
- ``search`` — one search over several sites, page after page (``GrabberSearch``), kept for the page;
- ``media``  — previews and samples of the cards of a search, through a disk cache.
"""
