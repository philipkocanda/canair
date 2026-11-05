"""Reading the ``sensitive: true`` flag off PID and signal definitions."""

from canair.ecus.sensitivity import param_sensitive, pid_sensitive


class TestFlags:
    def test_a_pid_is_sensitive_by_its_own_flag_or_any_signal(self):
        assert pid_sensitive({"sensitive": True})
        assert pid_sensitive({"parameters": {"A": {"sensitive": True}, "B": {}}})
        assert not pid_sensitive({"parameters": {"A": {}}})
        assert not pid_sensitive({"sensitive": "yes"})  # only a real boolean

    def test_a_signal_is_sensitive_by_its_own_flag_or_its_pids(self):
        assert param_sensitive({"sensitive": True}, {})
        assert param_sensitive({}, {"sensitive": True})
        assert not param_sensitive({}, {})
