from pocketlab import system
from pocketlab.config import Host


def test_local_stats_shape():
    s = system.host_stats(Host(name="local", stats="local"))
    assert s["host"] == "local"
    assert s["online"] is True
    assert "cpu_percent" in s and isinstance(s["cpu_percent"], (int, float))
    assert s["mem"]["total"] > 0
    assert isinstance(s["disks"], list)
    assert isinstance(s["temps"], list)
    assert isinstance(s["load"], list) and len(s["load"]) == 3


def test_parse_probe():
    sample = "\n".join(
        [
            "===STAT1===",
            "cpu  100 0 50 1000 0 0 0 0",
            "===STAT2===",
            "cpu  150 0 70 1200 0 0 0 0",
            "===HOST===",
            "nas",
            "===UPTIME===",
            "123456.78 90000.0",
            "===LOAD===",
            "0.50 0.40 0.30 1/200 1234",
            "===NCPU===",
            "8",
            "===MEM===",
            "MemTotal:       16000000 kB",
            "MemAvailable:    4000000 kB",
            "===DF===",
            "/dev/sda1 100000000 60000000 40000000 60% /",
            "===TEMP===",
            "x86_pkg_temp:54000",
        ]
    )
    s = system.parse_probe(sample)
    assert s["hostname"] == "nas"
    assert s["uptime"] == 123456
    assert s["cpu_count"] == 8
    assert s["load"] == [0.5, 0.4, 0.3]
    # mem used = (16000000-4000000)kB = 12000000 kB
    assert s["mem"]["total"] == 16000000 * 1024
    assert s["mem"]["used"] == 12000000 * 1024
    assert s["mem"]["percent"] == 75.0
    assert s["disks"][0]["mount"] == "/"
    assert s["disks"][0]["percent"] == 60.0
    assert s["temps"][0]["label"] == "x86_pkg_temp"
    assert s["temps"][0]["celsius"] == 54.0
    # cpu%: idle delta = (1200-1000)=200, total delta = (1420-1150)=270 -> ~25.9%
    assert 25.0 < s["cpu_percent"] < 27.0


def test_offline_host_does_not_raise(monkeypatch):
    def boom(*a, **k):
        from pocketlab.ssh import SSHError
        raise SSHError("nope")

    monkeypatch.setattr(system, "run_text", boom)
    s = system.host_stats(Host(name="x", stats={"ssh": "root@dead"}))
    assert s["online"] is False
    assert "nope" in s["error"]
