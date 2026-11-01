"""Floor X6, X8, X9, F9, F10: the gate never passes secrets to the model; whole files; fail closed."""

import json

import pytest

from puffpai_core.zone1 import classify as classify_mod
from puffpai_core.zone1 import gate
from puffpai_core.zone1.classify import classify
from puffpai_core.zone1.folders import Library
from z1kit import canary_key, make

PHONE = "415-555-0137"


def _turn(key):
    return f"install Immich, my key is {key} and my phone is {PHONE}. my password is Wobble#Tree77"


@pytest.mark.parametrize("vault_open", [True, False])
def test_secrets_never_reach_the_model(state, vault_open):
    a, _, events, _ = make(state, open_=vault_open)
    key = canary_key()
    model, handles = gate.prepare(_turn(key), True, archivist=a)
    assert key not in model and "Wobble#Tree77" not in model
    assert key not in handles.store_text and "Wobble#Tree77" not in handles.store_text
    assert "{{vault:" in model and "(an API key)" in model
    assert key not in json.dumps(events)


def test_personal_values_only_to_local_runtime_while_open(state):
    a, *_ = make(state)
    model, handles = gate.prepare(_turn(canary_key()), True, archivist=a)
    assert PHONE in model                                          # vault open, local runtime: real value (D25)
    assert PHONE not in handles.store_text                         # plain stores: handle only
    model, _ = gate.prepare(_turn(canary_key()), True, archivist=a, local_runtime=False)
    assert PHONE not in model                                      # a brain on another computer: handles only
    model, _ = gate.prepare(_turn(canary_key()), False, archivist=a)
    assert PHONE not in model                                      # caller says locked: handles


def test_caller_cannot_claim_open_when_locked(state):
    a, *_ = make(state, open_=False)
    model, handles = gate.prepare(_turn(canary_key()), True, archivist=a)
    assert PHONE not in model and all(h.kept == "inbox" for h in handles if h.ref)
    assert a.inbox.pending() >= 3


def test_held_values_resolve_from_their_handles(state):
    a, *_ = make(state)
    _, handles = gate.prepare(f"call me on {PHONE}", True, archivist=a)
    (h,) = [h for h in handles if h.label == "person.phone"]
    assert a.resolve(h.ref) == PHONE


def test_one_time_codes_are_scrubbed_never_stored(state):
    a, *_ = make(state)
    model, handles = gate.prepare("my verification code is 884213", True, archivist=a)
    assert "884213" not in model and "884213" not in handles.store_text
    assert [h.kept for h in handles] == ["scrubbed"]


def test_names_stay_open(state):
    a, *_ = make(state)
    model, handles = gate.prepare("my name is Leo Marlow", False, archivist=a)
    assert "Leo Marlow" in model and "Leo Marlow" in handles.store_text


def test_no_archivist_means_closed():
    gate.bind(None)
    with pytest.raises(gate.GateClosed):
        gate.prepare("hello", True)


def test_floor_engine_failure_closes_the_gate(state, monkeypatch):
    a, *_ = make(state)

    def broken(_text):
        raise RuntimeError("floor down")
    monkeypatch.setattr(classify_mod.secret_detector, "find", broken)
    with pytest.raises(gate.GateClosed):
        gate.prepare("anything at all", True, archivist=a)


def test_floor_failure_means_not_indexed(state, monkeypatch, tmp_path):
    a, *_ = make(state)
    lib = Library(a)
    lib.data_folder.mkdir(parents=True, exist_ok=True)
    (lib.data_folder / "notes.txt").write_text("plain note about tomatoes", encoding="utf-8")
    monkeypatch.setattr(classify_mod.secret_detector, "find", lambda _t: (_ for _ in ()).throw(RuntimeError()))
    report = lib.scan()
    assert report["indexed"] == 0 and len(report["not_indexed"]) == 1
    assert lib.search("tomatoes") == []


def test_file_scanned_past_500k_chars(state, tmp_path):
    """The v1 500,000-character cut is gone: an SSN at the end of a 3 MB file is found."""
    f = tmp_path / "big.txt"
    f.write_text(("lorem ipsum dolor sit amet " * 120_000) + "\nSSN 219-09-9999\n", encoding="utf-8")
    assert f.stat().st_size > 3_000_000
    found = classify(file=f)
    assert [h.label for h in found.hits if h.label == "id.ssn"] == ["id.ssn"]
    assert found.hits[-1].start > 3_000_000


def test_child_data_is_p4(state):
    people = [{"name": "Sam Lee", "names": ["Sam"], "role": "child"}]
    found = classify("Sam has a dentist visit, call 415-555-0137", people=people)
    assert {h.tier for h in found.hits} == {"P4"}
    assert "child.any" in {h.label for h in found.hits}


def test_floor_labels_always_run_on_every_path():
    for path in ("chat", "files", "package_data", "outbound"):
        found = classify("token ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8" + " ssn 219-09-9999", path=path)
        assert {"secret.token", "id.ssn"} <= {h.label for h in found.hits}


def test_child_reach_crosses_a_chunk_edge():
    """Verifier item 8: a child mention and an address on either side of a 2,000-character edge."""
    people = [{"name": "Sam Lee", "names": ["Sam"], "role": "child"}]
    before = ("a " * 995) + "Sam visits 12 Oak Street"              # the mention before the edge, the address after
    found = classify(before, people=people)
    assert [h.start for h in found.hits if h.label == "child.any"][0] < classify_mod.CHUNK
    assert [h.start for h in found.hits if h.label == "place.address"][0] >= classify_mod.CHUNK
    assert [h.tier for h in found.hits if h.label == "place.address"] == ["P4"]
    after = "12 Oak Street is where we go. " + ("b " * 990) + " Sam likes it"
    found = classify(after, people=people)
    assert [h.tier for h in found.hits if h.label == "place.address"] == ["P4"]
    far = "12 Oak Street. " + ("c " * 2100) + " Sam"
    assert [h.tier for h in classify(far, people=people).hits if h.label == "place.address"] == ["P3"]
