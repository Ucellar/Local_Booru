# Hotfix: ATF strict/fallback-only tag protection

Problem:
`booru.allthefallen.moe` sometimes returned a post object or HTML page without a verifiable MD5. In some cases this could add noisy/unwanted ATF tags to files that were already correctly matched by cleaner sources such as Danbooru/Gelbooru/e621.

Fix:
- ATF now requires an explicit API/JSON MD5 match.
- ATF HTML verification is not allowed to rescue missing/wrong MD5 results.
- ATF is sorted last in MD5 lookup.
- ATF is fallback-only by default: if trusted tags were already found from other sites, ATF is skipped.
- Confirmed ATF exact-MD5 matches still work when no previous source found tags.

Settings:
- `strict_atf_md5 = True` keeps hard MD5 verification for ATF.
- `atf_fallback_only = True` makes ATF a last-resort tag source.
