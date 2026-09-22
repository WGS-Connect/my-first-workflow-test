def test_youtube_client_still_imports():
    from src.youtube.client import YouTube, publish
    assert YouTube is not None and publish is not None
