

def test_current_app_reads_display_zero_when_hands_off():
    """Hands-off drives display 0; answering "unknown" made every screen foreign."""
    from phoneagent.devices.virtual import VirtualDisplayDevice

    dump = """Display #0 (activities from top to bottom):
  * Task{1a2b #42 type=standard A=10326:com.sina.weibo}
    topResumedActivity=ActivityRecord{9 u0 com.sina.weibo/.MainTabActivity t42}
"""

    class Fake(VirtualDisplayDevice):
        def __init__(self):
            self.display_id = None

        def _shell(self, command):
            return dump

    assert Fake().current_app() == "com.sina.weibo"
