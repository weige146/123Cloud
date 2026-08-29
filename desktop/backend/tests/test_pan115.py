from __future__ import annotations

from app.pan115 import extract_pan115_offline_links, offline_submit_chunks


def test_extract_pan115_offline_links_only_magnet_and_ed2k() -> None:
    text = "\n".join(
        [
            "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567&dn=demo",
            "ed2k://|file|demo.mkv|12345|0123456789ABCDEF0123456789ABCDEF|/",
            "https://example.com/demo.torrent",
        ]
    )

    links = extract_pan115_offline_links(text)

    assert len(links) == 2
    assert links[0].startswith("magnet:?xt=urn:btih:")
    assert links[1].startswith("ed2k://|file|")


def test_offline_submit_chunks_batches_ed2k_with_other_links() -> None:
    urls = [
        "ed2k://|file|a.mkv|1|0123456789ABCDEF0123456789ABCDEF|/",
        "ed2k://|file|b.mkv|2|0123456789ABCDEF0123456789ABCDEF|/",
        "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567",
    ]

    assert offline_submit_chunks(urls) == [urls]
