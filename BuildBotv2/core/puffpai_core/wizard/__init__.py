"""The wizard engine and the terminal renderer (lane L5, 03 Part 1).

Interface:
- ``Engine(state, services, ...)`` with ``overview()`` and
  ``submit(step_id, answers, renderer)``: one screen per call, for both
  renderers (``engine.py``).
- ``Terminal(engine).run()``: ``puffpai setup`` (``terminal.py``). The
  browser renderer is the Desktop's Setup page (``packages/desktop``), over
  ``GET /v1/wizard`` and ``POST /v1/wizard/{step}``.
- ``Services`` and ``default_services(core)``: what the wizard needs from
  lanes L2, L3, L4 and L6 (``services.py``).
- ``load_steps``, ``check_order``, ``apply_bundle`` (``steps.py``);
  ``visible``, ``expand_path``, ``example`` (``fields.py``); ``run_census``
  (``census.py``, harvested).
"""

from .engine import Engine, brain_line
from .fields import PathError, example, expand_path, visible
from .services import Services, default_services
from .steps import StepListError, apply_bundle, check_order, load_steps
from .terminal import Terminal

__all__ = ["Engine", "PathError", "Services", "StepListError", "Terminal", "apply_bundle", "brain_line",
           "check_order", "default_services", "example", "expand_path", "load_steps", "visible"]
