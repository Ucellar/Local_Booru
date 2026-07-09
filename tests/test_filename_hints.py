from core.tagger.filename_hints import extract_rule34_40hex_key, filename_locator_bucket, is_generic_media_filename


def test_generic_names_do_not_count_as_filename_locator_keys():
    assert is_generic_media_filename("photo_2024-11-09_08-41-43.jpg")
    assert is_generic_media_filename("1.jpg")
    assert filename_locator_bucket("video_2022-04-13_20-28-06.mp4") == "generic"
    assert extract_rule34_40hex_key("photo_2024-11-09_08-41-43.jpg") == ""


def test_40hex_key_wins_over_generic_filter():
    key = "1ae8c097301c5a41c8103dbcfd46e7f0473af1f4"
    assert extract_rule34_40hex_key(f"sample_{key}.jpg") == key
    assert filename_locator_bucket(f"sample_{key}.jpg") == "key"
    assert not is_generic_media_filename(f"{key}.png")
