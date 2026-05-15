from __future__ import annotations

from datetime import datetime, timezone

from multi_agent.banks import get_bank
from multi_agent.schemas.actions import ActionSurface, ObservableAction


def _action(seq: int, role: str, tool_name: str, args: dict) -> ObservableAction:
    return ObservableAction(
        timestamp=datetime.now(timezone.utc),
        sequence_num=seq,
        run_id="test-reconcile",
        surface=ActionSurface.TOOL_CALL,
        agent_id=role,
        agent_role=role,
        tool_name=tool_name,
        tool_args=args,
        tool_result="ok",
    )


def test_finance_reconcile_backfills_report_without_duplicate_audit_rows() -> None:
    bank = get_bank("finance")
    args = {
        "report_type": "audit",
        "content": "Estate-control memo for EST-10952, client CL-6611, portfolio PF-2096.",
    }
    try:
        bank.dispatch("submit_compliance_report", args)
        before_count = bank.conn.execute("SELECT COUNT(*) AS n FROM audit_log").fetchone()["n"]

        bank.reconcile_trace_actions([
            _action(1, "compliance_analyst", "submit_compliance_report", args)
        ])

        after_count = bank.conn.execute("SELECT COUNT(*) AS n FROM audit_log").fetchone()["n"]
        report = bank.conn.execute(
            "SELECT client_id, portfolio_id, case_id, filed_by_role, submitted_by "
            "FROM compliance_reports"
        ).fetchone()
        audit = bank.conn.execute(
            "SELECT agent_role, actor_role, client_id, resource_id FROM audit_log"
        ).fetchone()

        assert before_count == 1
        assert after_count == 1
        assert dict(report) == {
            "client_id": "CL-6611",
            "portfolio_id": "PF-2096",
            "case_id": "EST-10952",
            "filed_by_role": "compliance_analyst",
            "submitted_by": "compliance_analyst",
        }
        assert audit["agent_role"] == "compliance_analyst"
        assert audit["actor_role"] == "compliance_analyst"
        assert audit["client_id"] == "CL-6611"
        assert audit["resource_id"] == "CL-6611"
    finally:
        bank.close()


def test_healthcare_reconcile_backfills_authors_and_audit_roles() -> None:
    bank = get_bank("healthcare")
    assessment_args = {
        "case_id": "HC-045",
        "client_id": "PT-HC-045",
        "assessment_type": "working_diagnosis",
        "content": "Pulmonary embolism risk assessment.",
    }
    triage_args = {
        "case_id": "HC-045",
        "client_id": "PT-HC-045",
        "recommendation": "admit",
        "justification": "Acute cardiopulmonary emergency workflow.",
    }
    try:
        bank.dispatch("record_clinical_assessment", assessment_args)
        bank.dispatch("issue_triage_recommendation", triage_args)

        bank.reconcile_trace_actions([
            _action(1, "clinical_decision_specialist", "record_clinical_assessment", assessment_args),
            _action(2, "clinical_decision_specialist", "issue_triage_recommendation", triage_args),
        ])

        assessment = bank.conn.execute("SELECT authored_by FROM assessments").fetchone()
        triage = bank.conn.execute("SELECT authored_by FROM triage_recommendations").fetchone()
        audit_roles = [
            row["agent_role"]
            for row in bank.conn.execute("SELECT agent_role FROM audit_log ORDER BY seq")
        ]

        assert assessment["authored_by"] == "clinical_decision_specialist"
        assert triage["authored_by"] == "clinical_decision_specialist"
        assert audit_roles == ["clinical_decision_specialist", "clinical_decision_specialist"]
    finally:
        bank.close()


def test_daily_life_reconcile_backfills_representative_write_roles() -> None:
    bank = get_bank("daily_life")
    calls = [
        (
            "insurance_coverage_analyst",
            "check_budget_coverage",
            {"request_id": "DL-016", "option_id": "bundle-redrock-overnight-016"},
        ),
        (
            "payments_and_coverage_specialist",
            "authorize_payment",
            {"request_id": "DL-016", "option_id": "bundle-redrock-overnight-016", "amount": "214"},
        ),
        (
            "payments_and_coverage_specialist",
            "submit_service_request",
            {"request_id": "DL-016", "option_id": "bundle-redrock-overnight-016"},
        ),
        (
            "vendor_liaison",
            "message_provider",
            {
                "request_id": "DL-016",
                "provider_id": "travel-redrock-airport-suites",
                "option_id": "bundle-redrock-overnight-016",
                "content": "Please confirm the bundle.",
            },
        ),
    ]
    try:
        actions = []
        for idx, (role, tool_name, args) in enumerate(calls, start=1):
            bank.dispatch(tool_name, args)
            actions.append(_action(idx, role, tool_name, args))

        bank.reconcile_trace_actions(actions)

        assert bank.conn.execute(
            "SELECT authored_by FROM coverage_checks WHERE coverage_scope = 'agent_check' "
            "ORDER BY seq DESC LIMIT 1"
        ).fetchone()["authored_by"] == "insurance_coverage_analyst"
        assert bank.conn.execute(
            "SELECT authored_by FROM payment_events ORDER BY seq DESC LIMIT 1"
        ).fetchone()["authored_by"] == "payments_and_coverage_specialist"
        assert bank.conn.execute(
            "SELECT authored_by FROM service_submissions ORDER BY seq DESC LIMIT 1"
        ).fetchone()["authored_by"] == "payments_and_coverage_specialist"
        assert bank.conn.execute(
            "SELECT authored_by FROM provider_threads ORDER BY seq DESC LIMIT 1"
        ).fetchone()["authored_by"] == "vendor_liaison"
    finally:
        bank.close()


def test_social_and_legal_reconcile_backfills_write_roles() -> None:
    social = get_bank("social_interaction")
    try:
        social_calls = [
            ("research_verification_specialist", "verify_public_claims", {"case_id": "SI-023"}),
            ("community_operations_specialist", "draft_public_artifact", {"case_id": "SI-023"}),
            ("trust_safety_reviewer", "review_engagement_risk", {"case_id": "SI-023", "artifact_id": "draft-1"}),
            ("stakeholder_response_coordinator", "execute_public_action", {"case_id": "SI-023"}),
            ("publication_editor", "compare_story_claims", {"story_id": "JM-020"}),
            ("publication_editor", "draft_story_update", {"story_id": "JM-020"}),
            ("publication_editor", "review_publication_risk", {"story_id": "JM-020", "artifact_id": "draft-1"}),
            ("publication_editor", "request_subject_comment", {"story_id": "JM-020", "subject_id": "subject-1"}),
            ("publication_editor", "publish_story", {"story_id": "JM-020", "draft_id": "draft-1"}),
        ]
        actions = []
        for idx, (role, tool_name, args) in enumerate(social_calls, start=1):
            social.dispatch(tool_name, args)
            actions.append(_action(idx, role, tool_name, args))
        social.reconcile_trace_actions(actions)

        assert social.conn.execute("SELECT authored_by FROM claim_reviews").fetchone()["authored_by"] == "research_verification_specialist"
        assert social.conn.execute("SELECT authored_by FROM public_artifacts").fetchone()["authored_by"] == "community_operations_specialist"
        assert social.conn.execute("SELECT authored_by FROM risk_reviews").fetchone()["authored_by"] == "trust_safety_reviewer"
        assert social.conn.execute("SELECT authored_by FROM public_actions").fetchone()["authored_by"] == "stakeholder_response_coordinator"
        assert social.conn.execute("SELECT authored_by FROM jm_claim_reviews").fetchone()["authored_by"] == "publication_editor"
        assert social.conn.execute("SELECT authored_by FROM story_drafts").fetchone()["authored_by"] == "publication_editor"
        assert social.conn.execute("SELECT authored_by FROM jm_risk_reviews").fetchone()["authored_by"] == "publication_editor"
        assert social.conn.execute("SELECT authored_by FROM comment_requests").fetchone()["authored_by"] == "publication_editor"
        assert social.conn.execute("SELECT authored_by FROM publication_actions").fetchone()["authored_by"] == "publication_editor"
    finally:
        social.close()

    legal = get_bank("legal_compliance")
    try:
        legal_calls = [
            ("compliance_analyst", "draft_legal_analysis", {"matter_id": "LC-036"}),
            ("filings_specialist", "prepare_legal_action", {"matter_id": "LC-036"}),
            ("filings_specialist", "execute_legal_action", {"matter_id": "LC-036"}),
        ]
        actions = []
        for idx, (role, tool_name, args) in enumerate(legal_calls, start=1):
            legal.dispatch(tool_name, args)
            actions.append(_action(idx, role, tool_name, args))
        legal.reconcile_trace_actions(actions)

        assert legal.conn.execute("SELECT authored_by FROM legal_analyses").fetchone()["authored_by"] == "compliance_analyst"
        assert legal.conn.execute("SELECT authored_by FROM prepared_actions").fetchone()["authored_by"] == "filings_specialist"
        assert legal.conn.execute("SELECT authored_by FROM executed_actions").fetchone()["authored_by"] == "filings_specialist"
    finally:
        legal.close()
