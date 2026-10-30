"""Pages of the web interface; ``register`` adds all routes to the server's router."""
from __future__ import annotations


def register(router) -> None:
    from webui.pages import (api, diag, dupes, gallery, grab, maintenance, manga, manga_get, media, nomatch, post_sets,
                             reset, settings, sites, subs, tools, trash, video, video_get, videos, visual)
    gallery.register(router)
    media.register(router)
    video.register(router)
    manga.register(router)
    manga_get.register(router)
    videos.register(router)
    video_get.register(router)
    api.register(router)
    post_sets.register(router)
    tools.register(router)
    nomatch.register(router)
    trash.register(router)
    visual.register(router)
    sites.register(router)
    subs.register(router)
    grab.register(router)
    dupes.register(router)
    diag.register(router)
    maintenance.register(router)
    reset.register(router)
    settings.register(router)
