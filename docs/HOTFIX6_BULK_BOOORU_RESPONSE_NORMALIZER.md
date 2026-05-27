# Hotfix6: общий нормализатор ответов booru

## Что исправлено

Проблема `MD5 ERROR: ... 'str' object has no attribute 'get'` была не одним сайтом, а классом ошибок:
разные booru API возвращают `dict`, `list`, вложенные `posts`, `post`, `data`, `items`, XML или мусор/HTML.

Теперь добавлен единый нормализатор:

- `_post_dicts_from_data(data)`
- обновлён `_posts_from_dapi_response(...)`
- обновлён `_custom_response_posts(...)`
- e621 MD5 lookup использует общий нормализатор
- custom sites и tags_from_url защищены от старых/битых config entries

## Главный принцип

Ни один MD5-поиск не должен напрямую ходить по сырому JSON и ожидать конкретную форму ответа.
Сначала данные превращаются в список `dict`-постов, потом уже идёт `post.get(...)`.

## Что проверять

1. АПТ.
2. MD5 поиск e621.
3. MD5 поиск rule34.xxx/rule34.us.
4. Custom sites / ATF.
5. Отсутствие `str object has no attribute get`.
