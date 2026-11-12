"""Pages of the web interface; ``register`` adds all routes to the server's router."""
from __future__ import annotations


def register(router) -> None:
    from webui.pages import (api, diag, dupes, gallery, grab, maintenance, manga, manga_get, manhwa, manhwa_get, media,
                             nomatch, phone, post_sets, reset, settings, sites, subs, tools, trash, video, video_get,
                             videos, visual)
    gallery.register(router)
    media.register(router)
    video.register(router)
    manga.register(router)
    manga_get.register(router)
    manhwa.register(router)                      # (v5.44.59) «Манхва»: long strips read by scrolling
    manhwa_get.register(router)                  # (v5.44.60) «Скачать манхву» from Remanga, MangaBuff
    videos.register(router)
    video_get.register(router)
    api.register(router)
    post_sets.register(router)
    tools.register(router)
    from webui.pages import parser_manga         # (v5.44.125) «Парсер» → «Манга в исходной папке»
    parser_manga.register(router)
    nomatch.register(router)
    trash.register(router)
    visual.register(router)
    sites.register(router)
    subs.register(router)
    grab.register(router)
    from webui.pages import get_hub              # (v5.44.75) «Скачать»: the grabber and the downloads of the tabs
    get_hub.register(router)
    from webui.pages import manga_archive        # (v5.44.76) «Скачать → Из архива»: the titles of the folders
    manga_archive.register(router)
    from webui.pages import manga_sites          # (v5.44.78) «Настройки → Манга и манхва»: addresses and sign-in
    manga_sites.register(router)
    from webui.pages import manga_online         # (v5.44.82) «Читать» a title of the sites without downloading it
    manga_online.register(router)
    from webui.pages import gallery_read         # (v5.44.148) …and a gallery of nhentai, in the same reader
    gallery_read.register(router)
    from webui.pages import reader               # (v5.44.117) the one reader: a folder's title's chapters on its site
    reader.register(router)
    from webui.pages import catalog              # (v5.44.90) «Лента» of «Манга», «Манхва», «Видео»: the sites' titles
    catalog.register(router)
    from webui.pages import title                # (v5.44.102) a title of «Лента»: its page in the tab of the feed
    title.register(router)
    from webui.pages import video_watch          # (v5.44.106) «Смотреть» a series of a site in the program
    video_watch.register(router)
    from webui.pages import covers               # (v5.44.123) the menu of covers of a title and of a series
    covers.register(router)
    dupes.register(router)
    diag.register(router)
    maintenance.register(router)
    reset.register(router)
    settings.register(router)
    from webui.pages import help                 # (v5.44.153) «Справка»: how to use the program, a «?» on every page
    help.register(router)
    phone.register(router)                       # (v5.44.64) the owner's phone through Tailscale; the phone's pages
    from webui import content_mode               # (v5.44.53) the switch «SFW / Всё / NSFW» and the marks of titles
    content_mode.register(router)
