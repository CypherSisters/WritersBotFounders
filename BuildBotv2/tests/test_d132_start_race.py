"""A Caretaker check in the half second before Steward says "started" is no failed start (D132)."""

import time
from types import SimpleNamespace

from puffpai_core.caretaker.caretaker import Caretaker


class Registry:
    def __init__(self):
        self.row = {"id": "a", "status": "running", "name": "A"}
        self.health = None

    def get(self, id):
        return self.row

    def set_health(self, id, ok, reason):
        self.health = (ok, reason)


class Steward:
    def __init__(self, started_at):
        self.started_at = started_at
        self.restarts = 0
        self.on_start, self.on_broken = [], []

    def info(self, id):
        return SimpleNamespace(started_at=self.started_at, job=None)

    def alive(self, id):
        return True

    def restart(self, id):
        self.restarts += 1


def _caretaker(steward, registry):
    c = Caretaker(registry, steward, bus=None, start_grace_s=60.0)
    c.probe = lambda id: (False, "Connection refused.")
    return c


def test_a_check_right_after_the_start_never_restarts_it():
    steward, registry = Steward(time.time()), Registry()
    _caretaker(steward, registry).check("a")
    assert steward.restarts == 0 and registry.health == (False, "Starting.")


def test_an_app_that_stopped_answering_later_is_still_restarted():
    steward, registry = Steward(time.time() - 3600), Registry()
    _caretaker(steward, registry).check("a")
    assert steward.restarts == 1
