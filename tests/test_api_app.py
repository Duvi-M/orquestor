from computer_use_demo.api.main import _parse_sse_block, app


def test_api_app_imports():
    assert app.title == "Computer Use Backend (Challenge)"


def test_parse_sse_block_with_json_data():
    event, data, event_id = _parse_sse_block(
        'id: 42\nevent: assistant_block\ndata: {"type": "text", "text": "hello"}'
    )

    assert event == "assistant_block"
    assert data == {"type": "text", "text": "hello"}
    assert event_id == "42"


def test_parse_sse_block_with_raw_data():
    event, data, event_id = _parse_sse_block("event: debug\ndata: not json")

    assert event == "debug"
    assert data == {"raw": "not json"}
    assert event_id is None
