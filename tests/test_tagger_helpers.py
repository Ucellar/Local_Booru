from core.tagger.hashing import is_md5
from core.tagger.tag_groups import merge_tag_groups, groups_to_tags, group_from_tag_type
from core.tagger.atf_html import atf_parse_post_view_html


def test_is_md5_accepts_only_32_hex():
    assert is_md5('0' * 32)
    assert not is_md5('g' * 32)
    assert not is_md5('0' * 31)


def test_tag_group_merge_deduplicates_case_normalized():
    merged = merge_tag_groups([
        {'artist': ['Foo Bar', 'foo_bar'], 'general': ['tag']},
        {'artist': ['foo bar'], 'meta': ['artist_request']},
    ])
    assert merged['artist'] == ['Foo_Bar']
    assert 'tag' in groups_to_tags(merged)


def test_group_from_tag_type_danbooru_numbers():
    assert group_from_tag_type(1) == 'artist'
    assert group_from_tag_type('4') == 'character'
    assert group_from_tag_type('unknown') == 'general'


def test_atf_parser_uses_href_tags_not_visible_counts():
    html = """
    <li class='category-1'><a class='search-tag' href='/posts?tags=artist_name'>12</a></li>
    <li class='category-4'><a class='search-tag' href='/posts?tags=char_name'>Char Name</a></li>
    """
    tags, groups = atf_parse_post_view_html(html)
    assert 'artist_name' in tags
    assert 'char_name' in tags
    assert groups['artist'] == ['artist_name']
    assert groups['character'] == ['char_name']
