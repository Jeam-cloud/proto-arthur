"""Calendar / Teams-meeting creation — request shape and join-link handling."""

from tools.base import ToolContext
from tools.email_calendar import CreateEventArgs, CreateEventTool

CTX = ToolContext(conversation_id="c1")


class FakeGraph:
    def __init__(self, response=None):
        self.requests = []
        self.response = response or {}

    async def request(self, method, path, json_body=None, params=None):
        self.requests.append({"method": method, "path": path, "body": json_body})
        return self.response


def make_args(**over):
    base = dict(subject="Sync", start_iso="2026-07-21T15:00:00",
                end_iso="2026-07-21T15:30:00", timezone_name="UTC")
    base.update(over)
    return CreateEventArgs(**base)


async def test_plain_event_has_no_teams_flags():
    graph = FakeGraph()
    await CreateEventTool(graph).execute(make_args(), CTX)
    body = graph.requests[0]["body"]
    assert "isOnlineMeeting" not in body and "attendees" not in body


async def test_teams_meeting_sets_graph_flags_and_returns_join_link():
    graph = FakeGraph(response={"onlineMeeting": {"joinUrl": "https://teams.microsoft.com/l/meetup-join/abc"}})
    result = await CreateEventTool(graph).execute(
        make_args(teams_meeting=True, attendees=["jane@work.com"]), CTX
    )
    body = graph.requests[0]["body"]
    assert body["isOnlineMeeting"] is True
    assert body["onlineMeetingProvider"] == "teamsForBusiness"
    assert body["attendees"] == [{"emailAddress": {"address": "jane@work.com"}, "type": "required"}]
    assert "teams.microsoft.com" in result.content  # link surfaced to the model/user
    assert "jane@work.com" in result.content        # invite confirmation


async def test_missing_join_link_degrades_with_explanation():
    """Some tenants return the event without onlineMeeting populated yet."""
    graph = FakeGraph(response={})
    result = await CreateEventTool(graph).execute(make_args(teams_meeting=True), CTX)
    assert result.ok and "may take a moment" in result.content


def test_approval_summary_names_it_a_teams_meeting():
    tool = CreateEventTool(FakeGraph())
    summary = tool.approval_summary(make_args(teams_meeting=True, attendees=["jane@work.com"]))
    assert "Teams meeting" in summary and "jane@work.com" in summary
