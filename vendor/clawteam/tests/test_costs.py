"""Tests for clawteam.team.costs — CostStore report/list/summary."""

from pathlib import Path

from clawteam.team.costs import CostEvent, CostStore, CostSummary


class TestCostEvent:
    def test_defaults(self):
        event = CostEvent(agent_name="w1")
        assert event.agent_name == "w1"
        assert event.input_tokens == 0
        assert event.output_tokens == 0
        assert event.cost_cents == 0.0
        assert event.id  # auto-generated

    def test_alias_roundtrip(self):
        event = CostEvent(agent_name="w1", input_tokens=100, cost_cents=5.0)
        data = event.model_dump(by_alias=True)
        assert data["agentName"] == "w1"
        assert data["inputTokens"] == 100
        assert data["costCents"] == 5.0
        restored = CostEvent.model_validate(data)
        assert restored.agent_name == "w1"


class TestCostStoreReport:
    def test_report_creates_file(self, team_name, isolated_data_dir):
        store = CostStore(team_name)
        event = store.report(agent_name="a1", input_tokens=500, output_tokens=200, cost_cents=1.5)
        assert event.agent_name == "a1"
        assert event.input_tokens == 500
        costs_dir = isolated_data_dir / "costs" / team_name
        assert len(list(costs_dir.glob("cost-*.json"))) == 1

    def test_report_multiple(self, team_name):
        store = CostStore(team_name)
        store.report(agent_name="a1", cost_cents=1.0)
        store.report(agent_name="a2", cost_cents=2.0)
        store.report(agent_name="a1", cost_cents=3.0)
        events = store.list_events()
        assert len(events) == 3


class TestCostStoreListEvents:
    def test_list_empty(self, team_name):
        store = CostStore(team_name)
        assert store.list_events() == []

    def test_list_filter_by_agent(self, team_name):
        store = CostStore(team_name)
        store.report(agent_name="a1", cost_cents=1.0)
        store.report(agent_name="a2", cost_cents=2.0)
        a1_events = store.list_events(agent_name="a1")
        assert len(a1_events) == 1
        assert a1_events[0].agent_name == "a1"

    def test_list_skips_corrupt_files(self, team_name, isolated_data_dir):
        store = CostStore(team_name)
        store.report(agent_name="good", cost_cents=1.0)
        costs_dir = isolated_data_dir / "costs" / team_name
        (costs_dir / "cost-9999-corrupt.json").write_text("not json")
        events = store.list_events()
        assert len(events) == 1
        assert events[0].agent_name == "good"


class TestCostStoreSummary:
    def test_summary_empty(self, team_name):
        store = CostStore(team_name)
        summary = store.summary()
        assert summary.total_cost_cents == 0.0
        assert summary.event_count == 0
        assert summary.by_agent == {}

    def test_summary_aggregates(self, team_name):
        store = CostStore(team_name)
        store.report(agent_name="a1", input_tokens=100, output_tokens=50, cost_cents=1.0)
        store.report(agent_name="a2", input_tokens=200, output_tokens=100, cost_cents=2.0)
        store.report(agent_name="a1", input_tokens=300, output_tokens=150, cost_cents=3.0)

        summary = store.summary()
        assert summary.total_cost_cents == 6.0
        assert summary.total_input_tokens == 600
        assert summary.total_output_tokens == 300
        assert summary.event_count == 3
        assert summary.by_agent["a1"] == 4.0
        assert summary.by_agent["a2"] == 2.0

    def test_summary_uses_cached_totals_without_rereading_event_files(
        self, team_name, monkeypatch
    ):
        store = CostStore(team_name)
        store.report(agent_name="a1", input_tokens=100, output_tokens=50, cost_cents=1.0)
        store.report(agent_name="a2", input_tokens=200, output_tokens=100, cost_cents=2.0)

        event_payload_reads: list[str] = []
        original_read_text = Path.read_text

        def tracked_read_text(path: Path, *args, **kwargs):
            if path.name.startswith("cost-") and path.suffix == ".json":
                event_payload_reads.append(path.name)
            return original_read_text(path, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", tracked_read_text)

        summary = CostStore(team_name).summary()

        assert summary.total_cost_cents == 3.0
        assert summary.total_input_tokens == 300
        assert summary.total_output_tokens == 150
        assert summary.event_count == 2
        assert summary.by_agent == {"a1": 1.0, "a2": 2.0}
        assert event_payload_reads == []


class TestCostSummaryModel:
    def test_alias_serialization(self):
        summary = CostSummary(
            team_name="t",
            total_cost_cents=10.0,
            total_input_tokens=1000,
            total_output_tokens=500,
            by_agent={"a1": 10.0},
            event_count=2,
        )
        data = summary.model_dump(by_alias=True)
        assert data["teamName"] == "t"
        assert data["totalCostCents"] == 10.0
        assert data["eventCount"] == 2
