

def test_a_request_to_stay_is_recognised():
    """Only an explicit "leave it open" keeps the app in front."""
    from phoneagent.server import _wants_to_stay

    assert _wants_to_stay("在小红书搜索香港旅游攻略") is False
    assert _wants_to_stay("打开蓝牙设置") is False
    assert _wants_to_stay("打开小红书停在那个页面") is True
    assert _wants_to_stay("搜完留在小红书别关") is True
    assert _wants_to_stay("keep it open on the note") is True
