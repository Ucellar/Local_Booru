# Hotfix8 — universal booru parser/MD5 verifier

Цель: убрать сайт-специфичные костыли и сделать единый слой для всех booru/custom источников.

## Изменено

- `_post_md5_value()` теперь извлекает MD5 универсально:
  - `md5`
  - `file_md5`
  - `image_md5`
  - `hash`
  - `file.md5`
  - `file.hash`
  - URL-поля `file_url/source/image_url/sample_url/original_url`
  - `attrs/attributes/properties`
- добавлен `_md5_from_urlish()`;
- `_tags_from_post_dict()` теперь поддерживает:
  - строковые tags
  - списки tags
  - dict-группы tags
  - списки dict-объектов `{name: ...}`
- добавлен `_groups_from_post_dict_general()`;
- `danbooru_by_md5`, `gelbooru_by_md5`, `e621_by_md5` переведены на общий post-normalizer;
- e621 больше не особый случай в логике групп тегов, он проходит через общий слой;
- Gelbooru/Danbooru/e621 теперь используют единый механизм проверки remote MD5.

## Принцип

Любой сайт должен пройти через цепочку:

`raw response -> normalized post dicts -> universal md5 extraction -> universal tag/group extraction`

Нельзя брать теги без точного подтверждения MD5.
