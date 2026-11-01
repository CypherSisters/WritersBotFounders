"""FreshTable's own server (packages/freshtable/server.py): the data document,
the page push, recipe import, the widget and the MCP endpoint's token.

Every server here listens on a free port on 127.0.0.1 and is stopped by the
test that started it; nothing is left running."""

import asyncio
import base64
import contextlib
import importlib.util
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from puffpai_core.packages import manifest

PKG = Path(__file__).resolve().parents[2] / "packages" / "freshtable"
TOKEN = "test-token-" + "x" * 20

_spec = importlib.util.spec_from_file_location("freshtable_server", PKG / "server.py")
ft = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ft)

_open = urllib.request.build_opener(urllib.request.ProxyHandler({})).open


@contextlib.contextmanager
def running(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        assert not thread.is_alive()


@pytest.fixture
def data(tmp_path):
    return tmp_path / "data" / "freshtable"


@pytest.fixture
def base(data):
    with running(ft.make_server(0, data, TOKEN, None)) as url:
        yield url


def call(url, method="GET", body=None, token=TOKEN, headers=None):
    h = {"Content-Type": "application/json", **(headers or {})}
    if token:
        h["Authorization"] = f"Bearer {token}"
    raw = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=raw, method=method, headers=h)
    try:
        with _open(req, timeout=10) as r:
            text = r.read().decode()
            return r.status, (json.loads(text) if text and "json" in r.headers.get("Content-Type", "") else text)
    except urllib.error.HTTPError as e:
        text = e.read().decode()
        return e.code, (json.loads(text) if text.startswith("{") else text)


def mcp(base, method, params=None, token=TOKEN, id=1):
    return call(base + "/mcp", "POST", {"jsonrpc": "2.0", "id": id, "method": method, "params": params or {}}, token)


def tool(base, tool_name, /, **args):
    code, out = mcp(base, "tools/call", {"name": tool_name, "arguments": args})
    assert code == 200 and not out["result"]["isError"], out
    return out["result"]["content"][0]["text"]


# ------------------------------------------------------------ page and data

def test_health_page_and_empty_widget(base):
    assert call(base + "/health") == (200, "ok")
    code, page = call(base + "/")
    assert code == 200 and "FreshTable" in page and "BASE+'data'" in page and "/api/pai" not in page
    assert call(base + "/widget/list") == (200, {"items": []})


def test_add_to_list_shows_in_the_widget_and_the_file(base, data):
    assert "Added milk" in tool(base, "add_shopping", name="milk", quantity=2)
    tool(base, "add_shopping", name="eggs")
    assert call(base + "/widget/list")[1] == {"items": [{"text": "milk (2)"}, {"text": "eggs"}]}
    saved = json.loads((data / "freshtable.json").read_text(encoding="utf-8"))
    assert [i["name"] for i in saved["shoppingList"]] == ["milk", "eggs"] and saved["last_updated"]
    assert "milk: 2" in tool(base, "list_shopping")
    assert "Checked off eggs" in tool(base, "check_off_shopping", name="Eggs")
    assert "Removed milk" in tool(base, "remove_shopping", name="milk")
    assert call(base + "/widget/list")[1] == {"items": []}


def test_page_save_keeps_recipes(base):
    """v1 bug: the page pushed only pantry, shoppingList and mealPlan, and every edit deleted recipes."""
    tool(base, "add_recipe", name="Pancakes", ingredients=["flour", "milk", "eggs"], instructions="Mix and fry.")
    stamp = call(base + "/updated")[1]["last_updated"]
    code, out = call(base + "/data", "PUT", {"pantry": [], "shoppingList": [{"id": "a", "name": "rice", "checked": False}],
                                            "mealPlan": [], "last_updated": stamp})
    assert code == 200 and out["last_updated"] != stamp
    doc = call(base + "/data")[1]
    assert [r["name"] for r in doc["recipes"]] == ["Pancakes"]
    assert doc["recipes"][0]["ingredients"] == "flour, milk, eggs"
    assert [i["name"] for i in doc["shoppingList"]] == ["rice"]


def test_page_save_from_an_old_copy_is_refused(base):
    first = call(base + "/data", "PUT", {"pantry": [], "last_updated": ""})[1]["last_updated"]
    tool(base, "add_shopping", name="milk")                 # the brain changes it meanwhile
    code, out = call(base + "/data", "PUT", {"shoppingList": [], "last_updated": first})
    assert code == 409 and [i["name"] for i in out["data"]["shoppingList"]] == ["milk"]
    assert [i["name"] for i in call(base + "/data")[1]["shoppingList"]] == ["milk"]


def test_unreadable_file_is_never_written_over(data):
    data.mkdir(parents=True)
    (data / "freshtable.json").write_text("{not json", encoding="utf-8")
    with running(ft.make_server(0, data, TOKEN, None)) as base:
        assert call(base + "/data")[0] == 500
        assert call(base + "/data", "PUT", {"pantry": []})[0] == 409
        code, out = mcp(base, "tools/call", {"name": "add_shopping", "arguments": {"name": "milk"}})
        assert out["result"]["isError"]
    assert (data / "freshtable.json").read_text(encoding="utf-8") == "{not json"


def test_pantry(base):
    tool(base, "add_pantry", name="Rice", quantity=4, unit="bags")
    assert "now 5 bags" in tool(base, "add_pantry", name="rice")
    assert "Rice: 5 bags (Grain)" in tool(base, "list_pantry")
    assert tool(base, "list_pantry", filter="low") == "Nothing is running low in the pantry."
    tool(base, "add_meal", day="Monday", name="Fajitas")
    assert "Monday: Fajitas" in tool(base, "list_meal_plan")


def test_use_up_and_remove_pantry_items(base):
    tool(base, "add_pantry", name="Milk", quantity=2, unit="gallons")
    assert tool(base, "use_pantry", name="milk", quantity=1) == "Milk: 1 gallons left in the pantry."
    out = tool(base, "use_pantry", name="milk")                      # "we ran out of milk"
    assert "Milk: 0 gallons left" in out and "on the shopping list" in out
    assert "Milk" in tool(base, "list_shopping")
    assert tool(base, "remove_pantry", name="milk") == "Took Milk out of the pantry."
    assert tool(base, "list_pantry") == "The pantry is empty."
    tool(base, "add_pantry", name="Rice", quantity=4, unit="bags")
    assert "Rice: 4 bags" in tool(base, "list_pantry", filter="rice")  # "do we have rice"


def test_shopping_keeps_amounts_and_units_and_clears_bought(base):
    tool(base, "add_shopping", name="chicken", quantity=2, unit="lbs")
    assert "now 3.5 lbs" in tool(base, "add_shopping", name="Chicken", quantity=1.5, unit="lbs")
    tool(base, "add_shopping", name="eggs")
    assert "chicken: 3.5 lbs" in tool(base, "list_shopping")
    assert call(base + "/widget/list")[1] == {"items": [{"text": "chicken (3.5 lbs)"}, {"text": "eggs"}]}
    tool(base, "check_off_shopping", name="eggs")
    assert tool(base, "clear_bought_shopping") == "Cleared 1 bought item from the shopping list."
    assert [i["name"] for i in call(base + "/data")[1]["shoppingList"]] == ["chicken"]
    assert tool(base, "clear_bought_shopping") == "Nothing on the shopping list is ticked off."


def test_recipe_ingredients_go_on_the_list_and_meals_and_recipes_come_off(base):
    tool(base, "add_recipe", name="Pancakes", ingredients=["flour", "milk", "eggs"], instructions="Mix.\nFry.")
    tool(base, "add_pantry", name="Flour", quantity=5)
    tool(base, "add_shopping", name="milk")
    out = tool(base, "add_ingredients_to_shopping", recipe="pancakes")
    assert out == "Added 1 ingredient for Pancakes to the shopping list: eggs. Already in the pantry: flour."
    assert "Planned Pancakes for Tuesday from your saved recipe" in tool(base, "add_meal", day="tue", name="pancakes")
    meal = call(base + "/data")[1]["mealPlan"][0]
    assert meal["day"] == "Tuesday" and meal["ingredients"] == ["flour", "milk", "eggs"] and meal["instructions"] == ["Mix.", "Fry."]
    assert "Instructions:\nMix.\nFry." in tool(base, "list_recipes", filter="pancakes")
    assert tool(base, "remove_meal", day="Tuesday") == "Took off the meal plan: Pancakes (Tuesday)."
    assert tool(base, "remove_recipe", name="pancakes") == "Deleted the recipe Pancakes."
    assert tool(base, "list_recipes") == "No recipes are saved."


def test_plan_week_without_the_brain_uses_saved_recipes_and_keeps_planned_days(base):
    assert "nothing changed" in tool(base, "plan_week")                 # no brain, no recipes
    tool(base, "add_recipe", name="Tacos", ingredients=["tortillas", "beef"])
    tool(base, "add_recipe", name="Soup", ingredients=["leeks"])
    tool(base, "add_meal", day="Monday", name="Leftovers")
    out = tool(base, "plan_week", days=3)
    assert "Tuesday: Tacos; Wednesday: Soup" in out and "Kept what was already planned for Monday" in out
    assert "saved recipes take turns" in out
    plan = call(base + "/data")[1]["mealPlan"]
    assert [m["name"] for m in plan] == ["Leftovers", "Tacos", "Soup"] and plan[1]["ingredients"] == ["tortillas", "beef"]
    assert "nothing changed" in tool(base, "plan_week", days=3)
    assert "Monday: Tacos" in tool(base, "plan_week", days=1, replace=True)
    assert "Leftovers" not in tool(base, "list_meal_plan")


def test_plan_week_with_the_brain(data):
    answer = '{"meals": [{"day": "Friday", "name": "Curry", "ingredients": ["rice", "chickpeas"]}]}'
    with fake_core(answer) as (api, seen), running(ft.make_server(0, data, TOKEN, api)) as base:
        tool(base, "add_recipe", name="Curry", ingredients=["rice"], instructions="Simmer.")
        out = tool(base, "plan_week", days=1, preferences="vegetarian")
        assert out.startswith("Planned 1 dinner: Monday: Curry.") and "take turns" not in out
        assert "Curry" in seen[0]["body"]["messages"][-1]["content"] and "vegetarian" in seen[0]["body"]["messages"][-1]["content"]
        meal = call(base + "/data")[1]["mealPlan"][0]
        assert meal["day"] == "Monday" and meal["ingredients"] == ["rice", "chickpeas"] and meal["instructions"] == ["Simmer."]


def test_two_saves_on_one_clock_tick_get_different_stamps(data, monkeypatch):
    """The page compares stamps; a shared stamp would let it write over the brain's change."""
    monkeypatch.setattr(ft, "_now", lambda: "2026-10-30T12:00:00")
    store = ft.Store(data)
    first = store.save(store.load())
    assert store.save(store.load()) != first


def test_page_save_after_the_page_reloaded_wins_and_the_brain_keeps_it(base):
    tool(base, "add_shopping", name="milk")
    stamp = call(base + "/updated")[1]["last_updated"]
    doc = call(base + "/data")[1]
    doc["shoppingList"].append({"id": "p1", "name": "bread", "quantity": 1, "unit": "pcs", "checked": False})
    code, out = call(base + "/data", "PUT", {**doc, "last_updated": stamp})
    assert code == 200
    tool(base, "add_shopping", name="eggs")                          # the brain reads the page's save fresh
    assert [i["name"] for i in call(base + "/data")[1]["shoppingList"]] == ["milk", "bread", "eggs"]


# ------------------------------------------------------------ recipe import

RECIPE = "Pancakes\nIngredients\n- flour\n- milk\n- 2 eggs\nInstructions\n1. Mix.\n2. Fry.\n"


def test_import_works_without_the_brain(base):
    """v1 bug: the import was refused (403). Here it is FreshTable's own tool."""
    b64 = base64.b64encode(RECIPE.encode()).decode()
    out = tool(base, "import_recipes", file_name="pancakes.txt", mime="text/plain", content_b64=b64)
    assert out == "Saved 1 recipe from pancakes.txt: Pancakes."
    r = call(base + "/data")[1]["recipes"][0]
    assert r["ingredients"] == "flour, milk, 2 eggs" and r["instructions"] == "Mix.\nFry."
    out = tool(base, "import_recipes", file_name="toast.txt", mime="text/plain", text="Toast\nIngredients\nbread")
    assert out == "Saved 1 recipe from toast.txt: Toast."
    code, out = call(base + "/api/import", "POST", {"file_name": "x.txt", "mime": "text/plain", "text": "Tea\nIngredients\nwater"})
    assert code == 200 and "Tea" in out["message"]


@contextlib.contextmanager
def fake_core(answer):
    """A stand-in for the Core API brain route; it records what FreshTable sent."""
    seen = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
            out = json.dumps({"text": answer, "usage": {"in": 10, "out": 20}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

    server = ThreadingHTTPServer(("127.0.0.1", 0), H)
    with running(server) as url:
        yield url, seen


def test_import_a_photo_through_the_core_brain_route(data):
    answer = 'Here: {"recipes": [{"name": "Soup", "ingredients": ["water", "leeks"], "instructions": "Boil."}]}'
    with fake_core(answer) as (api, seen), running(ft.make_server(0, data, TOKEN, api)) as base:
        img = base64.b64encode(b"\x89PNG fake").decode()
        out = tool(base, "import_recipes", file_name="card.jpg", mime="image/jpeg", content_b64=img)
        assert out == "Saved 1 recipe from card.jpg: Soup."
        assert seen[0]["path"] == "/v1/brain/chat" and seen[0]["auth"] == f"Bearer {TOKEN}"
        assert seen[0]["body"]["images"] == [img]
        code, gen = call(base + "/api/generate", "POST", {"prompt": "ideas?"})
        assert code == 200 and gen["response"] == answer


def test_pdf_is_refused_plainly(base):
    out = tool(base, "import_recipes", file_name="book.pdf", mime="application/pdf",
               content_b64=base64.b64encode(b"%PDF-1.7 \x00\x01").decode())
    assert "cannot read PDF files yet" in out
    assert call(base + "/data")[1]["recipes"] == []


def test_no_brain_says_so_plainly(base):
    code, out = call(base + "/api/generate", "POST", {"prompt": "hi"})
    assert code == 503 and "not running under PuffPAI" in out["error"]


def test_brain_reply_without_text_is_plain(data):
    with fake_core(None) as (api, _), running(ft.make_server(0, data, TOKEN, api)) as base:
        code, out = call(base + "/api/generate", "POST", {"prompt": "hi"})
        assert code == 503 and "without text" in out["error"]


# ------------------------------------------------------------ MCP and its token

@pytest.mark.parametrize("method,path,body", [
    ("GET", "/", None), ("GET", "/health", None), ("GET", "/data", None), ("GET", "/updated", None),
    ("GET", "/widget/list", None), ("PUT", "/data", {"shoppingList": []}),
    ("POST", "/api/import", {"file_name": "a.txt", "mime": "text/plain", "text": "Toast"}),
])
def test_every_route_refuses_without_the_token(base, method, path, body):
    """D48d: not only /mcp. The Desktop reaches the page through /pkg/, which adds the token."""
    assert call(base + path, method, body, token=None)[0] == 401
    assert call(base + path, method, body, token="wrong")[0] == 401


def test_data_without_the_token_is_refused_and_unchanged(base):
    tool(base, "add_shopping", name="milk")
    code, out = call(base + "/data", token=None)
    assert code == 401 and "milk" not in json.dumps(out)
    assert call(base + "/data", "PUT", {"shoppingList": []}, token=None)[0] == 401
    assert [i["name"] for i in call(base + "/data")[1]["shoppingList"]] == ["milk"]


def test_mcp_refuses_without_the_token(base):
    assert mcp(base, "tools/list", token=None)[0] == 401
    assert mcp(base, "tools/list", token="wrong")[0] == 401
    assert call(base + "/mcp", token=None)[0] == 401
    code, _ = call(base + "/mcp", "POST", {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                         "params": {"name": "add_shopping", "arguments": {"name": "milk"}}}, token=None)
    assert code == 401
    assert call(base + "/data")[1]["shoppingList"] == []


def test_mcp_refuses_everything_when_started_without_a_token(data):
    with running(ft.make_server(0, data, None, None)) as base:
        assert mcp(base, "tools/list", token=None)[0] == 401
        assert call(base + "/mcp", "POST", {"jsonrpc": "2.0", "id": 1, "method": "ping"},
                    token=None, headers={"Authorization": "Bearer "})[0] == 401
        assert call(base + "/data", token=None, headers={"Authorization": "Bearer "})[0] == 401


def test_mcp_works_with_the_token(base):
    code, out = mcp(base, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                         "clientInfo": {"name": "t", "version": "0"}})
    assert code == 200 and out["result"]["serverInfo"]["name"] == "freshtable"
    assert call(base + "/mcp", "POST", {"jsonrpc": "2.0", "method": "notifications/initialized"},
                token=None, headers={"X-PuffPAI-Key": TOKEN})[0] == 202
    names = [t["name"] for t in mcp(base, "tools/list")[1]["result"]["tools"]]
    assert {"add_shopping", "import_recipes", "list_recipes"} <= set(names)
    assert mcp(base, "tools/call", {"name": "nope"})[1]["error"]["code"] == -32602


def test_manifest_matches_the_live_tool_list(base):
    """Level 2 item 10 and 11: changes, examples and accepts name real tools,
    and every tool that writes is listed in changes."""
    m, _ = manifest.load(PKG)
    tools = mcp(base, "tools/list")[1]["result"]["tools"]
    names = {t["name"] for t in tools}
    named = set(m["abilities"]["changes"]) | set(m["abilities"]["examples"]) | {a["tool"] for a in m["accepts"]}
    assert named <= names
    writers = {t["name"] for t in tools if not t["annotations"]["readOnlyHint"]}
    assert writers == set(m["abilities"]["changes"])
    assert all(t["description"] and t["inputSchema"]["type"] == "object" for t in tools)


def test_the_official_mcp_client_can_use_it(base):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    from mcp.shared._httpx_utils import create_mcp_http_client

    async def go():
        client = create_mcp_http_client(headers={"Authorization": f"Bearer {TOKEN}"})
        async with client, streamable_http_client(base + "/mcp", http_client=client) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                listed = await s.list_tools()
                res = await s.call_tool("add_shopping", {"name": "bread"})
                return [t.name for t in listed.tools], res.content[0].text

    names, text = asyncio.run(go())
    assert "add_shopping" in names and "Added bread" in text
    assert call(base + "/widget/list")[1] == {"items": [{"text": "bread"}]}


# ------------------------------------------------------------ started like Steward starts it

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_starts_from_its_run_block_and_stops_clean(tmp_path):
    m, _ = manifest.load(PKG)
    port = _free_port()
    start = [a.replace("{python}", sys.executable).replace("{port}", str(port)) for a in m["run"]["default"]["start"]]
    env = {**os.environ, "PUFFPAI_PORT": str(port), "PUFFPAI_DATA": str(tmp_path / "data"),
           "PUFFPAI_TOKEN": TOKEN, "PUFFPAI_API": "http://127.0.0.1:1"}
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.Popen(start, cwd=PKG, env=env, creationflags=flags,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        health = m["health"]["url"].replace("{port}", str(port))
        for _ in range(100):
            try:
                if call(health)[0] == m["health"]["expect"]:
                    break
            except urllib.error.URLError:
                time.sleep(0.1)
        else:
            pytest.fail("FreshTable did not answer its health check")
        assert proc.poll() is None
        base = f"http://127.0.0.1:{port}"
        assert mcp(base, "tools/list", token=None)[0] == 401
        assert call(base + "/data", token=None)[0] == 401
        assert "Added tea" in tool(base, "add_shopping", name="tea")
        assert (tmp_path / "data" / "freshtable.json").is_file()
    finally:
        proc.terminate()
        proc.wait(10)
        proc.stderr.close()
    assert proc.poll() is not None
