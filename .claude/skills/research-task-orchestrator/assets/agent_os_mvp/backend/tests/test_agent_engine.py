from app.services.agent_engine import planner_breakdown


def test_planner_breakdown_generates_fixed_flow():
    tasks = planner_breakdown("Improve onboarding", "Create a lighter internal onboarding flow")
    roles = [task["agent_role"] for task in tasks]
    assert roles == ["Research", "Dev", "QA", "Reviewer"]
    assert len(tasks) == 4
