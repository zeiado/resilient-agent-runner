import httpx
import pytest

from app import tools
from app.agent import execute_run
from app.llm import FINISH, Action
from app.tools import BlockedURL, Tool, fetch_url

from tests.helpers import get_run, get_steps, make_run

PUBLIC_IP = "93.184.216.34"
HOSTS = {"public.example": PUBLIC_IP, "api1": "172.18.0.5"}


@pytest.fixture
def network(monkeypatch):
    """Fake DNS for the names in HOSTS and a fake HTTP transport that records every request."""
    requests = []
    routes = {}

    async def resolve(host, port):
        return [HOSTS.get(host, host)]

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return routes[request.headers["host"] + request.url.path]

    monkeypatch.setattr(tools, "resolve", resolve)
    monkeypatch.setattr(tools, "transport", httpx.MockTransport(handler))
    return routes, requests


@pytest.mark.parametrize(
    "url, message",
    [
        ("http://api1:8000", "blocked: private address"),
        ("http://127.0.0.1", "blocked: private address"),
        ("http://169.254.169.254/latest/meta-data/", "blocked: private address"),
        ("http://10.0.0.5", "blocked: private address"),
        ("http://[::1]/", "blocked: private address"),
        ("http://[::ffff:10.0.0.5]/", "blocked: private address"),
        ("file:///etc/passwd", "blocked: scheme not allowed"),
    ],
)
async def test_internal_urls_are_blocked_without_any_request(network, url, message):
    _, requests = network
    with pytest.raises(BlockedURL, match=message):
        await fetch_url({"url": url})
    assert requests == []


async def test_localhost_is_blocked_using_real_name_resolution(monkeypatch):
    requests = []
    monkeypatch.setattr(tools, "transport", httpx.MockTransport(lambda r: requests.append(r)))
    with pytest.raises(BlockedURL, match="blocked: private address"):
        await fetch_url({"url": "http://localhost"})
    assert requests == []


async def test_name_with_one_private_address_among_public_ones_is_blocked(network, monkeypatch):
    async def resolve(host, port):
        return [PUBLIC_IP, "10.0.0.5"]

    monkeypatch.setattr(tools, "resolve", resolve)
    with pytest.raises(BlockedURL):
        await fetch_url({"url": "http://mixed.example"})


async def test_redirect_from_public_url_to_private_address_is_blocked(network):
    routes, requests = network
    routes["public.example/start"] = httpx.Response(302, headers={"location": "http://169.254.169.254/secret"})

    with pytest.raises(BlockedURL, match="blocked: private address"):
        await fetch_url({"url": "http://public.example/start"})

    assert [r.url.host for r in requests] == [PUBLIC_IP]


async def test_more_than_three_redirects_fails(network):
    routes, requests = network
    routes["public.example/loop"] = httpx.Response(302, headers={"location": "/loop"})

    with pytest.raises(RuntimeError, match="too many redirects"):
        await fetch_url({"url": "http://public.example/loop"})
    assert len(requests) == 4


async def test_public_url_is_fetched_from_the_checked_address(network):
    routes, requests = network
    routes["public.example/old"] = httpx.Response(301, headers={"location": "/page"})
    routes["public.example/page"] = httpx.Response(200, text="x" * 5000)

    result = await fetch_url({"url": "http://public.example/old"})

    assert result == {"status_code": 200, "content": "x" * 2000}
    # the connection goes to the resolved IP, with the original name in the Host header
    assert [(r.url.host, r.headers["host"], r.url.path) for r in requests] == [
        (PUBLIC_IP, "public.example", "/old"),
        (PUBLIC_IP, "public.example", "/page"),
    ]


async def test_blocked_url_fails_the_run_on_the_first_attempt():
    class InternalFetchLLM:
        async def next_action(self, task, steps):
            return Action("fetch_url", {"url": "http://10.0.0.5/admin"}) if not steps else Action(FINISH, {"result": "r"})

    run = await make_run()
    await execute_run(run.id, InternalFetchLLM(), {"fetch_url": Tool(run=fetch_url)})

    run = await get_run(run.id)
    (step,) = await get_steps(run.id)
    assert run.status == "failed"
    assert "blocked: private address" in run.error
    assert (step.status, step.attempts, step.error) == ("failed", 1, "BlockedURL: blocked: private address")
