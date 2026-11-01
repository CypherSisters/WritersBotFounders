"""The Core API (lane L0 skeleton; each group's lane fills in its routes).

Interface: ``create_app(state=None, registry=None, tokens=None, bus=None, port=8420,
pages_port=8422, pin_check=None)``, ``serve(port=8420, pages_port=8422)`` (the Core's
own address and the pages address, D49); route helpers ``routing.route``, ``routing.stub``,
``routing.current_core``; ``auth.TokenStore`` and the access levels
(``PUBLIC``, ``OWNER``, ``OWNER_DEVICE``, ``PACKAGE_OK``, ``OWN_ROW``, ``CORE``).
Routes: ``contracts/openapi.yaml``.
"""

from .app import HOST, PAGES_PORT, PORT, Core, create_app, serve

__all__ = ["Core", "HOST", "PAGES_PORT", "PORT", "create_app", "serve"]
