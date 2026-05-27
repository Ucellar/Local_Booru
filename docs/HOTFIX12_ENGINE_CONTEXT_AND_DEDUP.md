# HOTFIX12_ENGINE_CONTEXT_AND_DEDUP

Цель: убрать повторные запросы и сделать engine-based парсер стабильнее.

Изменено:

- Добавлен per-file request cache.
- Добавлен session cache внутри Tagger.
- API/HTML fallback больше не должен заново грузить cookies и повторять одинаковые GET в рамках одного MD5 lookup.
- `_all_enabled_site_configs()` теперь дедуплицирует сайты по `(domain, engine)`.
- Если один и тот же сайт есть в built-in и custom, конфиги сливаются, а не сканируются дважды.
- HTML MD5 verifier стал универсальнее: если точный wanted MD5 есть в post HTML/JS blob, это считается строгим подтверждением.
- `_engine_html_fallback_by_md5()` теперь использует общий cached ATF-aware GET.

Что это должно исправить:

- Повторные `ATF VERIFY PAGE DETECTED` по одному файлу.
- Повторные `COOKIES loaded` для одного и того же сайта в рамках одного файла.
- Двойной проход по `booru.allthefallen.moe`, если он включён и как built-in, и как custom.
- Более надёжную строгую HTML-MD5 проверку для новых booru/custom сайтов.

Важно:

- Мягкий поиск не добавлен.
- Теги всё ещё применяются только после точного MD5.
- Сайт остаётся конфигом, логика идёт через engine family.
