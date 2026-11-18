"""Fact-check numbers for Prometheus and problem lines for Loki (docs/factcheck.md, Monitoring).

kbots counts tags and replies itself, and polls the sidecar's plugin (pes/opencode/plugin/kbots/telemetry.ts) for
what happens inside opencode. Labels hold chat IDs and fixed names only. A problem line holds an error or rejection
message, never chat or answer text."""

import asyncio
import json
import time
from collections.abc import Iterator
from typing import Literal, get_args

import aiohttp
import sentry_sdk
from aiohttp import web
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Histogram, generate_latest
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily, Metric

POLL_EVERY = 15  # seconds; the plugin keeps its last 200 problems, far more than arrive in one interval
SENTRY_KINDS = {"turn_failed", "step_failed", "content_filter"}  # the other kinds repeat often; the feed shows them
Rejection = Literal[
    "shape", "too_long", "format", "sources", "already_answered", "unknown_trigger", "aimed_elsewhere", "telegram_refused"
]


def chat_of(session: str) -> str:
    return session.removeprefix("ses_tg_")  # session_id() in tmodules/factcheck.py; "" is the sidecar as a whole


def window_name(seconds: int) -> str:
    return "weekly" if seconds == 7 * 86400 else f"{seconds // 3600}h"


class Telemetry:
    def __init__(self, logger, titles: dict[int, str], silent: list[int], expire_after: float):
        self.logger = logger
        self.titles = {str(chat): title for chat, title in titles.items()}  # refreshed on every tag
        self.silent = {str(chat) for chat in silent}
        self.expire_after = expire_after  # a tag this old can no longer be answered
        self.registry = registry = CollectorRegistry()
        self.tags = Counter("kbots_factcheck_tags", "Tags of the bot in a fact-check chat.", ["chat"], registry=registry)
        self.replies = Counter(
            "kbots_factcheck_replies",
            "The agent's reply calls: sent, silent (accepted in a silent chat, never posted) or why kbots rejected one.",
            ["chat", "outcome"],
            registry=registry,
        )
        self.answers = Histogram(
            "kbots_factcheck_answer_seconds",
            "From a tag to the first reply kbots accepted for it.",
            ["chat"],
            buckets=(15, 30, 60, 120, 300, 600, 900, 1800),
            registry=registry,
        )
        registry.register(self)  # collect() adds the sidecar's counts and the gauges computed at scrape time
        for chat in self.titles:  # at 0 before the first event: increase() takes a new series' first value as its start
            self.tags.labels(chat)
            self.answers.labels(chat)
            for outcome in ("silent" if chat in self.silent else "sent", *get_args(Rejection)):
                self.replies.labels(chat, outcome)
        self.pending: dict[tuple[str, int], float] = {}  # (chat, tag message ID) -> when the tag arrived
        self.snapshot: dict | None = None
        self.sidecar_up = 0
        self.started = time.time() * 1000  # ms, as the plugin stamps problems; older ones are never logged here
        self.seen = (0, 0)  # (sidecar start, last problem seq) already logged

    def title(self, chat_id: int, title: str):
        self.titles[str(chat_id)] = title

    def tagged(self, chat_id: int, message_id: int):
        self.tags.labels(str(chat_id)).inc()
        self.pending[(str(chat_id), message_id)] = time.time()

    def replied(self, session: str, message_id: int, outcome: str):
        chat = chat_of(session)
        self.replies.labels(chat, outcome).inc()
        if (arrived := self.pending.pop((chat, message_id), None)) is not None:
            self.answers.labels(chat).observe(time.time() - arrived)

    def rejected(self, session: str, reason: Rejection, message: str):
        self.replies.labels(chat_of(session), reason).inc()
        self.problem(chat_of(session), "reply_rejected", f"{reason}: {message}")

    def problem(self, chat: str, kind: str, message: str):  # counted from these lines in Loki: each is logged once
        line = {
            "chat": chat,
            "title": self.titles.get(chat, ""),
            "kind": kind,
            "message": message[:300],
        }  # as the plugin cuts its own
        self.logger.warning("factcheck-problem %s", json.dumps(line, ensure_ascii=False))  # Loki's feed parses this

    def receive(self, snapshot: dict):
        started, seen_seq = self.seen
        after = seen_seq if snapshot["started"] == started else 0  # a restarted sidecar counts from 1
        self.seen = (snapshot["started"], max((p["seq"] for p in snapshot["problems"]), default=0))  # before logging: no repeats
        for p in snapshot["problems"]:
            if p["seq"] > after and p["time"] >= self.started:  # one host, so the sidecar's clock is this one
                self.problem(chat_of(p["session"]), p["kind"], p["message"])
                if p["kind"] in SENTRY_KINDS:
                    sentry_sdk.capture_message(f"Fact-check {p['kind']} in {chat_of(p['session'])}: {p['message']}", level="error")
        self.snapshot = snapshot

    async def poll(self, opencode: aiohttp.ClientSession):
        timeout = aiohttp.ClientTimeout(total=10)
        while True:
            try:
                async with opencode.post("/api/rpc/kbots.telemetry/snapshot", json={"input": {}}, timeout=timeout) as response:
                    self.receive((await response.json())["output"])
                self.sidecar_up = 1
            except (aiohttp.ClientError, TimeoutError):
                self.sidecar_up = 0  # down or restarting: kbots_factcheck_sidecar_up shows it, and the alert waits 5 minutes
            except Exception:  # a snapshot this code cannot read; a dead loop would leave sidecar_up at 1 for good
                self.sidecar_up = 0  # not .exception(): at ERROR, Sentry files an event on every poll
                self.logger.warning("Fact-check: cannot read the sidecar's telemetry", exc_info=True)
            await asyncio.sleep(POLL_EVERY)

    async def serve(self, host: str, port: int):
        async def metrics(_request: web.Request) -> web.Response:
            return web.Response(body=generate_latest(self.registry), headers={"Content-Type": CONTENT_TYPE_LATEST})

        app = web.Application()
        app.router.add_get("/metrics", metrics)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, host, port).start()

    def collect(self) -> Iterator[Metric]:
        now = time.time()
        self.pending = {key: arrived for key, arrived in self.pending.items() if now - arrived < self.expire_after}
        info = GaugeMetricFamily(
            "kbots_factcheck_chat_info", "Each fact-check chat's Telegram title and mode.", labels=["chat", "title", "mode"]
        )
        oldest = GaugeMetricFamily(
            "kbots_factcheck_oldest_unanswered_seconds",
            "Age of the oldest tag still without an answer, 0 when none.",
            labels=["chat"],
        )
        for chat, title in self.titles.items():
            info.add_metric([chat, title, "silent" if chat in self.silent else "live"], 1)
            oldest.add_metric([chat], max((now - arrived for (c, _), arrived in self.pending.items() if c == chat), default=0))
        yield info
        yield oldest
        yield GaugeMetricFamily(
            "kbots_factcheck_sidecar_up", "1 when the last poll of the sidecar's telemetry worked.", value=self.sidecar_up
        )
        if (s := self.snapshot) is None:
            return
        yield GaugeMetricFamily(
            "kbots_factcheck_sidecar_start_time_seconds", "When the sidecar's plugin started.", value=s["started"] / 1000
        )

        turns = CounterMetricFamily("kbots_factcheck_turns", "Agent turns, by event.", labels=["chat", "event"])
        steps = CounterMetricFamily("kbots_factcheck_steps", "Model steps, by how each ended.", labels=["chat", "finish"])
        tokens = CounterMetricFamily("kbots_factcheck_tokens", "Model tokens, by kind.", labels=["chat", "kind"])
        tools = CounterMetricFamily(
            "kbots_factcheck_tool_calls", "Tool calls, by tool and outcome.", labels=["chat", "tool", "outcome"]
        )
        retries = CounterMetricFamily("kbots_factcheck_retries", "Model requests opencode retried.", labels=["chat"])
        compactions = CounterMetricFamily("kbots_factcheck_compactions", "Session compactions.", labels=["chat"])
        for chat in self.titles:  # each event at 0 before its first, as above; a restarted sidecar's counts start empty
            seen = s["chats"].get(f"ses_tg_{chat}", {}).get("turns", {})
            for event, n in (dict.fromkeys(("started", "succeeded", "failed", "interrupted"), 0) | seen).items():
                turns.add_metric([chat, event], n)
        for session, c in s["chats"].items():
            chat = chat_of(session)
            for finish, n in c["steps"].items():
                steps.add_metric([chat, finish], n)
            for kind, n in c["tokens"].items():
                tokens.add_metric([chat, kind], n)
            for tool, outcomes in c["tools"].items():
                for outcome, n in outcomes.items():
                    tools.add_metric([chat, tool, outcome], n)
            retries.add_metric([chat], c["retries"])
            compactions.add_metric([chat], c["compactions"])
        yield from (turns, steps, tokens, tools, retries, compactions)

        search = CounterMetricFamily(
            "kbots_factcheck_search", "Keyless search requests, by provider and outcome.", labels=["provider", "outcome"]
        )
        for provider, outcomes in s["search"].items():
            for outcome, n in outcomes.items():
                search.add_metric([provider, outcome], n)
        jina = CounterMetricFamily(
            "kbots_factcheck_jina", "Pages opened through the Jina reader after webfetch failed.", labels=["outcome"]
        )
        for outcome, n in s["jina"].items():
            jina.add_metric([outcome], n)
        yield from (search, jina)

        plan = s["plan"]
        used = GaugeMetricFamily("kbots_factcheck_plan_used_ratio", "Share of the ChatGPT plan's window used.", labels=["window"])
        resets = GaugeMetricFamily("kbots_factcheck_plan_resets_at_seconds", "When the plan's window resets.", labels=["window"])
        for w in plan["windows"]:
            used.add_metric([window_name(w["seconds"])], w["used"] / 100)
            resets.add_metric([window_name(w["seconds"])], w["resets_at"])
        yield from (used, resets)
        yield GaugeMetricFamily(
            "kbots_factcheck_plan_read_time_seconds", "When the plan's usage was last read.", value=plan["updated"] / 1000
        )
