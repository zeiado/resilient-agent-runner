import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from typing import Awaitable, Callable

import httpx
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app import config
from app.llm import LLM
from app.models import Outbox, RunStep


@dataclass
class Tool:
    # Does the work and returns the step output. May be called more than once for the
    # same step (retry, or resume after a crash), so it must not have side effects.
    run: Callable[[dict], Awaitable[dict]]
    # Side effect, executed inside the transaction that marks the step completed.
    commit: Callable[[AsyncSession, RunStep, dict], Awaitable[None]] | None = None
    needs_approval: bool = False


class NonRetryable(Exception):
    """A tool failure that can never succeed on retry; the step fails on this attempt."""


class BlockedURL(NonRetryable):
    pass


MAX_REDIRECTS = 3
# Replaced in tests so no real network is needed.
transport: httpx.AsyncBaseTransport | None = None


async def resolve(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [info[4][0] for info in infos]


async def public_address(url: httpx.URL) -> str:
    """Return an address to connect to, or raise BlockedURL if the URL could reach a non-public host."""
    if url.scheme not in ("http", "https"):
        raise BlockedURL("blocked: scheme not allowed")
    if not url.host:
        raise BlockedURL("blocked: no host")

    addresses = await resolve(url.host, url.port or (443 if url.scheme == "https" else 80))
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%")[0])
        if ip.version == 6 and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        # is_global is False for loopback, RFC 1918, link-local (169.254.0.0/16), CGNAT and reserved ranges
        if not ip.is_global:
            raise BlockedURL("blocked: private address")
    return addresses[0]


async def fetch_url(input: dict) -> dict:
    url = httpx.URL(input["url"])
    async with httpx.AsyncClient(
        timeout=config.TOOL_TIMEOUT_SECONDS, follow_redirects=False, transport=transport
    ) as client:
        for _ in range(MAX_REDIRECTS + 1):
            address = await public_address(url)
            # Connect to the address that was checked, not to the name: a second DNS lookup
            # could return something else. Host header and TLS verification keep the real name.
            resp = await client.get(
                url.copy_with(host=address),
                headers={"Host": url.netloc.decode("ascii")},
                extensions={"sni_hostname": url.host},
            )
            if resp.is_redirect and "location" in resp.headers:
                url = url.join(resp.headers["location"])
                continue
            resp.raise_for_status()
            return {"status_code": resp.status_code, "content": resp.text[:2000]}
    raise RuntimeError(f"too many redirects (max {MAX_REDIRECTS})")


async def compose_email(input: dict) -> dict:
    return {"recipient": input["recipient"], "subject": input["subject"], "body": input["body"]}


async def write_outbox(session: AsyncSession, step: RunStep, output: dict) -> None:
    # (run_id, step_no) is unique, so a repeated send for the same step is a no-op.
    await session.execute(
        insert(Outbox)
        .values(run_id=step.run_id, step_no=step.step_no, **output)
        .on_conflict_do_nothing(index_elements=["run_id", "step_no"])
    )


def build_tools(llm: LLM) -> dict[str, Tool]:
    async def summarize(input: dict) -> dict:
        return {"summary": await llm.complete(f"Summarize this in three sentences:\n\n{input['text']}")}

    return {
        "fetch_url": Tool(run=fetch_url),
        "summarize": Tool(run=summarize),
        "send_email": Tool(run=compose_email, commit=write_outbox, needs_approval=True),
    }
