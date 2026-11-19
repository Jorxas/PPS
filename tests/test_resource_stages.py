import pytest
from pps.errors import PPSError
from pps.workers import guarded_load


def test_guard_after_engine_imports_prevents_weights(cfg, monkeypatch):
    events = []
    class Adapter:
        def import_dependencies(self):
            events.append("imports")
        def load(self):
            pytest.fail("Weights must not load when engine imports consume the reserve")
    def reject(cfg):
        events.append("guard")
        raise PPSError("RESOURCE_BUSY", "RAM", 503)
    monkeypatch.setattr("pps.resources.guard", reject)
    with pytest.raises(PPSError):
        guarded_load(Adapter(), cfg, lambda s:events.append(s))
    assert events == ["imports", "after_engine_imports_before_weights", "guard"]


def test_import_guard_load_order(cfg, monkeypatch):
    events = []
    class Adapter:
        def import_dependencies(self): events.append("imports")
        def load(self): events.append("weights")
    monkeypatch.setattr("pps.resources.guard", lambda c:events.append("guard"))
    guarded_load(Adapter(), cfg, lambda s:None)
    assert events == ["imports", "guard", "weights"]


@pytest.mark.parametrize("margin,allowed", [(.1, True), (-.1, False)])
def test_guard_uses_available_physical_ram_not_free_pages_or_commit(cfg, monkeypatch, margin, allowed):
    from types import SimpleNamespace
    from pps.resources import guard, GIB
    available = cfg["resources"]["min_ram_gib"] + margin  # relative to the configured load threshold
    monkeypatch.setattr("pps.resources.psutil.virtual_memory", lambda:SimpleNamespace(
        total=32*GIB, available=available*GIB, free=.01*GIB))
    monkeypatch.setattr("pps.resources.shutil.disk_usage", lambda p:SimpleNamespace(free=100*GIB))
    monkeypatch.setattr("pps.resources.subprocess.run", lambda *a, **k:SimpleNamespace(stdout="GPU,8192,7168,driver\n"))
    if allowed:
        assert guard(cfg)["ram_available_gib"] == available
    else:
        with pytest.raises(PPSError) as e: guard(cfg)
        assert e.value.code == "RESOURCE_BUSY"
