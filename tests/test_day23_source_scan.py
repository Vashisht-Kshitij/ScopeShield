from scripts.scan_source_secrets import findings


def test_marker_only_fixture_is_distinguished_from_a_key_body():
    marker='-----BEGIN '+'OPENSSH PRIVATE KEY-----'
    reviewed=[]
    assert findings('fixture.py','value = '+repr(marker),reviewed)==[]
    assert len(reviewed)==1
    assert findings('fixture.py','value = '+repr(marker+'\nSYNTHETIC-BODY'))


def test_scanner_reports_locations_without_echoing_values():
    synthetic='AIza'+'x'*35
    result=findings('synthetic.py','api_key = '+repr(synthetic))
    assert result and synthetic not in str(result)
