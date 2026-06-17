# Local Booru Companion для Chrome / Chromium

Расширение визуально скрывает на booru-сайтах карточки, которые уже есть в Local Booru.
Парсер и тэггер не блокируются: скрытие относится только к браузерной выдаче и граббер-preview.

## Установка в Chrome

1. Запусти Local Booru v233 или новее.
2. Открой `chrome://extensions/`.
3. Включи «Режим разработчика».
4. Нажми «Загрузить распакованное расширение».
5. Выбери папку:
   `extensions/chrome_local_booru_companion`
6. Открой rule34 / gelbooru / danbooru / e621 / ATF.

## Проверка

Нажми на иконку расширения → «Проверить связь».
Должно быть: `Связь есть: v233`.

## Поддерживаемые сайты

- rule34.xxx
- gelbooru.com
- danbooru.donmai.us
- e621.net / e926.net
- booru.allthefallen.moe

## Что скрывается

- MD5 уже есть в SQLite Local Booru.
- post_url/file_url уже есть в источниках.
- карточка вручную скрыта через ПКМ.
- MD5 есть в активном deleted_media_rules.

## Что НЕ происходит

- Парсер не блокируется.
- Тэггер не блокируется.
- exact-MD5 fanout не блокируется.
- Source merge не блокируется.
- no_match/брак не блокируется.

## Firefox

Код почти переносимый, но Firefox-сборку нужно делать отдельным manifest-файлом и отдельно проверять permissions/CORS.
