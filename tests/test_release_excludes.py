from tools.release.make_release_zip import excluded


def test_release_excludes_private_runtime_files():
    assert excluded('AI_MEMORY.dev.md')
    assert excluded('startup_console.log')
    assert excluded('data/settings/foo.json')
    assert excluded('foo/local_booru_index.sqlite3')
    assert excluded('ui/tagger/__pycache__/workers.cpython-313.pyc')
    assert excluded('some/nested/browser_cookies/rule34.xxx.json')
    assert excluded('.pytest_cache/v/cache/nodeids')
    assert not excluded('core/tagger/engine.py')
