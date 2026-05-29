from datetime import UTC

from exlab_wizard.sync.manifest import RemoteEntry, parse_lsjson

SAMPLE = """[
  {"Path":"sub/a.txt","Name":"a.txt","Size":3,"ModTime":"2026-05-28T10:00:00.000Z","IsDir":false},
  {"Path":"sub","Name":"sub","Size":-1,"ModTime":"2026-05-28T09:00:00Z","IsDir":true},
  {"Path":"b.bin","Name":"b.bin","Size":1024,"ModTime":"2026-05-28T10:01:00Z","IsDir":false}
]"""


def test_parse_drops_dirs_and_keys_by_path():
    m = parse_lsjson(SAMPLE)
    assert set(m.entries) == {"sub/a.txt", "b.bin"}
    assert m.entries["b.bin"] == RemoteEntry(
        size=1024, mod_time="2026-05-28T10:01:00Z", is_dir=False
    )


def test_parse_strips_prefix():
    raw = '[{"Path":"EQ/run/x.txt","Name":"x.txt","Size":1,"ModTime":"t","IsDir":false}]'
    m = parse_lsjson(raw, strip_prefix="EQ/run")
    assert "x.txt" in m.entries


def test_parse_empty_and_malformed():
    assert parse_lsjson("[]").entries == {}
    assert parse_lsjson("").entries == {}
    assert parse_lsjson("not json").entries == {}


def test_manifest_size_match_helper():
    m = parse_lsjson(SAMPLE)
    assert m.size_matches("b.bin", 1024) is True
    assert m.size_matches("b.bin", 999) is False
    assert m.size_matches("missing.txt", 1) is False


def test_manifest_matches_size_and_mtime_within_tolerance():
    from datetime import datetime

    m = parse_lsjson(SAMPLE)
    epoch = datetime(2026, 5, 28, 10, 1, 0, tzinfo=UTC).timestamp()  # b.bin ModTime
    assert m.matches("b.bin", 1024, epoch + 1, tolerance_s=2) is True  # within tol
    assert m.matches("b.bin", 1024, epoch + 10, tolerance_s=2) is False  # mtime drift
    assert m.matches("b.bin", 999, epoch, tolerance_s=2) is False  # size mismatch
    assert m.matches("missing.txt", 1, epoch, tolerance_s=2) is False  # absent


def test_manifest_to_epoch_handles_fractional_and_z():
    m = parse_lsjson(SAMPLE)
    # nanosecond precision + Z must parse (fromisoformat rejects 9 frac digits raw)
    assert m._to_epoch("2026-05-28T10:00:00.123456789Z") is not None
    assert m._to_epoch("") is None
    assert m._to_epoch("garbage") is None


def test_manifest_matches_false_when_modtime_unparseable():
    # Present + size-equal, but an unparseable ModTime cannot be credited.
    raw = '[{"Path":"x.txt","Name":"x.txt","Size":5,"ModTime":"garbage","IsDir":false}]'
    m = parse_lsjson(raw)
    assert m.has("x.txt") is True
    assert m.size_matches("x.txt", 5) is True
    assert m.matches("x.txt", 5, 1000.0, tolerance_s=2) is False


def test_parse_non_list_json_yields_empty():
    # Valid JSON that is not an array (object / scalar) must not raise.
    assert parse_lsjson('{"Path": "x"}').entries == {}
    assert parse_lsjson("5").entries == {}


def test_parse_skips_rows_with_empty_path():
    raw = (
        '[{"Path":"","Name":"","Size":1,"ModTime":"t","IsDir":false},'
        '{"Name":"noPath","Size":2,"ModTime":"t","IsDir":false},'
        '{"Path":"keep.txt","Name":"keep.txt","Size":3,"ModTime":"t","IsDir":false}]'
    )
    m = parse_lsjson(raw)
    assert set(m.entries) == {"keep.txt"}


def test_parse_skips_rows_with_non_numeric_size():
    raw = (
        '[{"Path":"bad.txt","Name":"bad.txt","Size":"NaN","ModTime":"t","IsDir":false},'
        '{"Path":"null.txt","Name":"null.txt","Size":null,"ModTime":"t","IsDir":false},'
        '{"Path":"ok.txt","Name":"ok.txt","Size":7,"ModTime":"t","IsDir":false}]'
    )
    m = parse_lsjson(raw)
    assert set(m.entries) == {"ok.txt"}
    assert m.entries["ok.txt"].size == 7
