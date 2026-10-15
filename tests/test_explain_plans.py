from fastapi.testclient import TestClient
from app.main import app, ensure_indexes

client = TestClient(app)

def find_stages(obj, stages=None):
    if stages is None:
        stages = []
    if isinstance(obj, dict):
        if "stage" in obj:
            stages.append(obj["stage"])
        for v in obj.values():
            find_stages(v, stages)
    elif isinstance(obj, list):
        for item in obj:
            find_stages(item, stages)
    return stages

def test_explain_index_scans():
    ensure_indexes()

    endpoints = [
        ("attendance_list", "page=1"),
        ("employee_monthly", "emp_code=EMP0001&month=2026-07"),
        ("department_summary", "month=2026-07"),
        ("late_leaderboard", "month=2026-07"),
        ("department_trend", "department=Engineering&from=2026-07-01&to=2026-07-10"),
    ]

    for ep, q in endpoints:
        res = client.get(f"/admin/explain/{ep}?{q}")
        assert res.status_code == 200
        data = res.json()
        assert data["endpoint"] == ep
        explain_doc = data["explain"]
        stages = find_stages(explain_doc)
        print(f"Endpoint {ep} stages:", stages)
        # Check that IXSCAN is present or no COLLSCAN on main query
        assert "IXSCAN" in stages, f"Endpoint {ep} did not use an IXSCAN! Stages: {stages}"
        assert "COLLSCAN" not in stages, f"Endpoint {ep} contained a COLLSCAN! Stages: {stages}"
