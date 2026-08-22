from __future__ import annotations

import pytest

from cmd_audit.core.state_codec import canonical_json, content_sha256, require_closed_mapping


def test_codec_regression_vectors_preserve_ascii_and_utf8_hashes() -> None:
    value = {"z": "汉", "a": [1, True]}
    assert canonical_json(value) == '{"a":[1,true],"z":"\\u6c49"}'
    assert canonical_json(value, ensure_ascii=False) == '{"a":[1,true],"z":"汉"}'
    assert content_sha256(value) == "cba0ea7075bc672e48716201359d306328740bfbab25efc844f63bd9ffbb5994"
    assert content_sha256(value, ensure_ascii=False) == "23086711cd25c299b450fd9ab7470cfa20526540b2db883af09d86dfe2d3de9b"


def test_closed_mapping_rejects_extra_field() -> None:
    with pytest.raises(ValueError, match="closed"):
        require_closed_mapping({"a": 1, "b": 2}, {"a"})
