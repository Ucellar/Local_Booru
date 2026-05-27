# HOTFIX11 — Engine-based site adapters

## Что изменено

Раньше MD5-поиск был построен вокруг отдельных сайтов:

- `rule34.xxx_by_md5`
- `rule34.us_by_md5`
- `danbooru_by_md5`
- `gelbooru_by_md5`
- `e621_by_md5`
- `custom_by_md5`

Это плохо масштабировалось: новый сайт мог работать или не работать в зависимости от того, есть ли под него отдельный костыль.

Теперь добавлен общий engine-based слой:

```text
site config -> engine family -> generic adapter/parser -> strict MD5 verify -> tags
```

## Движки

Поддержаны семейства:

- `danbooru`
- `gelbooru`
- `moebooru`
- `e621`
- `szurubooru`
- `custom/unknown`

## Что это даёт

- новые сайты используют общий pipeline;
- custom sites не требуют отдельной функции на каждый домен;
- MD5 извлекается универсальным extractor;
- JSON/XML/HTML обрабатываются общими путями;
- теги применяются только после exact MD5 verification;
- HTML fallback общий для всех engine families, не только ATF/rule34.

## Важное правило

Мягкий поиск не добавлен.

Если сайт не подтвердил тот же MD5 — теги не применяются.
