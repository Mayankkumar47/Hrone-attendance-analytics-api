import os
import asyncio
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient
import pytest
from app.main import app, db, IST, ensure_indexes

client = TestClient(app)

@pytest.fixture(autouse=True)
def setup_db():
    # Reload sample seed or clean state
    ensure_indexes()


def test_health():
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_employee_crud():
    # Unique emp_code
    emp_code = "EMP9999"
    db.employees.delete_one({"emp_code": emp_code})
    
    payload = {
        "emp_code": emp_code,
        "name": "Test User",
        "email": "test@example.com",
        "department": "Engineering",
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-06-01"
    }
    res = client.post("/employees", json=payload)
    assert res.status_code == 201
    data = res.json()
    assert data["emp_code"] == emp_code
    assert isinstance(data["created_at"], int)
    assert data["created_at"] >= 100000000000

    # Duplicate should return 409
    res_dup = client.post("/employees", json=payload)
    assert res_dup.status_code == 409

    # Equal shift_start and shift_end should return 422
    payload_bad = dict(payload)
    payload_bad["emp_code"] = "EMP9998"
    payload_bad["shift_end"] = "09:30"
    res_bad = client.post("/employees", json=payload_bad)
    assert res_bad.status_code == 422

    # List employees with pagination and sorting
    res_list = client.get("/employees?page=1&page_size=10")
    assert res_list.status_code == 200
    page = res_list.json()
    assert "items" in page
    assert page["total"] >= 1
    codes = [e["emp_code"] for e in page["items"]]
    assert codes == sorted(codes)


def test_punch_in_and_late_calculation():
    emp_code = "EMP9991"
    db.employees.delete_one({"emp_code": emp_code})
    db.attendance_logs.delete_many({"emp_code": emp_code})

    client.post("/employees", json={
        "emp_code": emp_code,
        "name": "Punch Tester",
        "email": "punch@example.com",
        "department": "Testing",
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-07-01"
    })

    # Exact 09:40:00 IST -> inside grace period (<= 10 mins) -> late_minutes = 0
    # 2026-07-06 09:40:00 IST is 2026-07-06 04:10:00 UTC
    dt_grace = datetime(2026, 7, 6, 4, 10, 0, tzinfo=timezone.utc)
    res = client.post("/attendance/punch-in", json={
        "emp_code": emp_code,
        "punched_at": int(dt_grace.timestamp() * 1000)
    })
    assert res.status_code == 201
    rec = res.json()
    assert rec["date"] == "2026-07-06"
    assert rec["late_minutes"] == 0
    assert rec["work_hours"] is None
    assert rec["half_day"] is False

    # Second punch-in for same date should be 409
    res_dup = client.post("/attendance/punch-in", json={
        "emp_code": emp_code,
        "punched_at": int(dt_grace.timestamp() * 1000)
    })
    assert res_dup.status_code == 409

    # Day 2: 09:40:01 IST -> strictly > 10:00 -> late_minutes = 10
    # 2026-07-07 09:40:01 IST is 2026-07-07 04:10:01 UTC
    dt_late = datetime(2026, 7, 7, 4, 10, 1, tzinfo=timezone.utc)
    res_late = client.post("/attendance/punch-in", json={
        "emp_code": emp_code,
        "punched_at": int(dt_late.timestamp() * 1000)
    })
    assert res_late.status_code == 201
    assert res_late.json()["late_minutes"] == 10

    # Day 3: 10:15:59 IST -> late_minutes = 45
    # 2026-07-08 10:15:59 IST is 2026-07-08 04:45:59 UTC
    dt_late2 = datetime(2026, 7, 8, 4, 45, 59, tzinfo=timezone.utc)
    res_late2 = client.post("/attendance/punch-in", json={
        "emp_code": emp_code,
        "punched_at": int(dt_late2.timestamp() * 1000)
    })
    assert res_late2.status_code == 201
    assert res_late2.json()["late_minutes"] == 45


def test_punch_out_and_work_hours():
    emp_code = "EMP9992"
    db.employees.delete_one({"emp_code": emp_code})
    db.attendance_logs.delete_many({"emp_code": emp_code})

    client.post("/employees", json={
        "emp_code": emp_code,
        "name": "Out Tester",
        "email": "out@example.com",
        "department": "Testing",
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-07-01"
    })

    # Punch-in at 09:30 IST (04:00 UTC) on 2026-07-06
    in_dt = datetime(2026, 7, 6, 4, 0, 0, tzinfo=timezone.utc)
    res_in = client.post("/attendance/punch-in", json={
        "emp_code": emp_code,
        "punched_at": int(in_dt.timestamp() * 1000)
    })
    assert res_in.status_code == 201

    # Punch-out before punch-in -> 422
    out_early = datetime(2026, 7, 6, 3, 50, 0, tzinfo=timezone.utc)
    res_out_bad = client.post("/attendance/punch-out", json={
        "emp_code": emp_code,
        "punched_at": int(out_early.timestamp() * 1000)
    })
    assert res_out_bad.status_code == 422

    # Punch-out at 13:00 IST (07:30 UTC): 3.50 hours worked (< 4.50) -> half_day = True
    out_half = datetime(2026, 7, 6, 7, 30, 0, tzinfo=timezone.utc)
    res_out = client.post("/attendance/punch-out", json={
        "emp_code": emp_code,
        "punched_at": int(out_half.timestamp() * 1000)
    })
    assert res_out.status_code == 200
    data = res_out.json()
    assert data["work_hours"] == 3.5
    assert data["half_day"] is True
    assert data["overtime_minutes"] == 0

    # Repeat punch-out -> 409
    res_dup = client.post("/attendance/punch-out", json={
        "emp_code": emp_code,
        "punched_at": int(out_half.timestamp() * 1000)
    })
    assert res_dup.status_code == 409


def test_overnight_shift():
    emp_code = "EMP9993"
    db.employees.delete_one({"emp_code": emp_code})
    db.attendance_logs.delete_many({"emp_code": emp_code})

    client.post("/employees", json={
        "emp_code": emp_code,
        "name": "Night Owl",
        "email": "night@example.com",
        "department": "Support",
        "shift_start": "22:00",
        "shift_end": "06:00",
        "joined_on": "2026-07-01"
    })

    # Punch in on 2026-07-07 at 01:00 IST (before shift_end 06:00)
    # 01:00 IST = 2026-07-06 19:30 UTC
    # R1: Must belong to previous day (2026-07-06)
    in_dt = datetime(2026, 7, 6, 19, 30, 0, tzinfo=timezone.utc)
    res_in = client.post("/attendance/punch-in", json={
        "emp_code": emp_code,
        "punched_at": int(in_dt.timestamp() * 1000)
    })
    assert res_in.status_code == 201
    assert res_in.json()["date"] == "2026-07-06"

    # Punch out at 06:40 IST on 2026-07-07 (01:10 UTC) -> 40 min overtime
    out_dt = datetime(2026, 7, 7, 1, 10, 0, tzinfo=timezone.utc)
    res_out = client.post("/attendance/punch-out", json={
        "emp_code": emp_code,
        "punched_at": int(out_dt.timestamp() * 1000)
    })
    assert res_out.status_code == 200
    data = res_out.json()
    assert data["overtime_minutes"] == 40


def test_regularization_patch():
    emp_code = "EMP9994"
    db.employees.delete_one({"emp_code": emp_code})
    db.attendance_logs.delete_many({"emp_code": emp_code})

    client.post("/employees", json={
        "emp_code": emp_code,
        "name": "Reg Tester",
        "email": "reg@example.com",
        "department": "Engineering",
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-07-01"
    })

    # Punch-in at 10:05 IST (04:35 UTC) on 2026-07-06 -> late 35 min
    in_dt = datetime(2026, 7, 6, 4, 35, 0, tzinfo=timezone.utc)
    client.post("/attendance/punch-in", json={
        "emp_code": emp_code,
        "punched_at": int(in_dt.timestamp() * 1000)
    })

    # Punch out at 18:30 IST (13:00 UTC)
    out_dt = datetime(2026, 7, 6, 13, 0, 0, tzinfo=timezone.utc)
    client.post("/attendance/punch-out", json={
        "emp_code": emp_code,
        "punched_at": int(out_dt.timestamp() * 1000)
    })

    # Regularize punch_in to 09:28 IST (03:58 UTC)
    new_in = datetime(2026, 7, 6, 3, 58, 0, tzinfo=timezone.utc)
    res_patch = client.patch(f"/attendance/{emp_code}/2026-07-06", json={
        "punch_in": int(new_in.timestamp() * 1000),
        "reason": "Traffic biometric malfunction",
        "regularized_by": "hr_manager"
    })
    assert res_patch.status_code == 200
    data = res_patch.json()
    assert data["late_minutes"] == 0
    assert len(data["history"]) == 1
    h = data["history"][0]
    assert h["by"] == "hr_manager"
    assert "punch_in" in h["changes"]
    assert "late_minutes" in h["changes"]
    assert h["changes"]["late_minutes"]["from"] == 35
    assert h["changes"]["late_minutes"]["to"] == 0

    # Changing nothing should return 422
    res_no_change = client.patch(f"/attendance/{emp_code}/2026-07-06", json={
        "reason": "Attempting identical update",
        "regularized_by": "hr_manager"
    })
    assert res_no_change.status_code == 422


def test_analytics_and_explain():
    # Employee monthly
    res_m = client.get("/analytics/employees/EMP0001/monthly?month=2026-07")
    assert res_m.status_code == 200
    m_data = res_m.json()
    assert m_data["emp_code"] == "EMP0001"
    assert m_data["working_days"] == 23

    # Department summary
    res_ds = client.get("/analytics/departments/summary?month=2026-07")
    assert res_ds.status_code == 200
    ds_data = res_ds.json()
    assert "items" in ds_data
    depts = [item["department"] for item in ds_data["items"]]
    assert "Engineering" in depts

    # Late leaderboard
    res_lb = client.get("/analytics/leaderboard/late?month=2026-07")
    assert res_lb.status_code == 200
    lb_data = res_lb.json()
    assert "items" in lb_data

    # Department trend
    res_tr = client.get("/analytics/departments/Engineering/trend?from=2026-07-01&to=2026-07-10")
    assert res_tr.status_code == 200
    tr_data = res_tr.json()
    assert len(tr_data["items"]) == 10  # Exact 10 calendar days

    # Admin Explain
    for ep, q in [
        ("attendance_list", "page=1"),
        ("employee_monthly", "emp_code=EMP0001&month=2026-07"),
        ("department_summary", "month=2026-07"),
        ("late_leaderboard", "month=2026-07"),
        ("department_trend", "department=Engineering&from=2026-07-01&to=2026-07-10"),
    ]:
        res_exp = client.get(f"/admin/explain/{ep}?{q}")
        assert res_exp.status_code == 200, f"Explain failed for {ep}: {res_exp.text}"
        exp_json = res_exp.json()
        assert exp_json["endpoint"] == ep
        assert "explain" in exp_json


from concurrent.futures import ThreadPoolExecutor

def test_concurrency_punch_in():
    emp_code = "EMP8001"
    db.employees.delete_one({"emp_code": emp_code})
    db.attendance_logs.delete_many({"emp_code": emp_code})

    client.post("/employees", json={
        "emp_code": emp_code,
        "name": "Race In Tester",
        "email": "race_in@example.com",
        "department": "QA",
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-07-01"
    })

    in_ms = int(datetime(2026, 7, 10, 4, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
    def do_punch():
        return client.post("/attendance/punch-in", json={
            "emp_code": emp_code,
            "punched_at": in_ms
        }).status_code

    with ThreadPoolExecutor(max_workers=5) as ex:
        statuses = list(ex.map(lambda _: do_punch(), range(5)))

    # Exactly one 201, rest 409
    assert statuses.count(201) == 1
    assert statuses.count(409) == 4


def test_concurrency_punch_out():
    emp_code = "EMP8002"
    db.employees.delete_one({"emp_code": emp_code})
    db.attendance_logs.delete_many({"emp_code": emp_code})

    client.post("/employees", json={
        "emp_code": emp_code,
        "name": "Race Out Tester",
        "email": "race_out@example.com",
        "department": "QA",
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-07-01"
    })

    in_ms = int(datetime(2026, 7, 10, 4, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
    out_ms = int(datetime(2026, 7, 10, 13, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)

    # Punch in first
    client.post("/attendance/punch-in", json={"emp_code": emp_code, "punched_at": in_ms})

    def do_punch_out():
        return client.post("/attendance/punch-out", json={
            "emp_code": emp_code,
            "punched_at": out_ms
        }).status_code

    with ThreadPoolExecutor(max_workers=5) as ex:
        statuses = list(ex.map(lambda _: do_punch_out(), range(5)))

    # Exactly one 200, rest 409
    assert statuses.count(200) == 1
    assert statuses.count(409) == 4


def test_leaderboard_ties_and_cutoff():
    # Setup test department and employees
    dept = "LeaderboardDept"
    emp_a = "EMP7001"
    emp_b = "EMP7002"
    emp_c = "EMP7003"
    emp_d = "EMP7004"
    for e in [emp_a, emp_b, emp_c, emp_d]:
        db.employees.delete_one({"emp_code": e})
        db.attendance_logs.delete_many({"emp_code": e})

    for code, name in [(emp_a, "Alice"), (emp_b, "Bob"), (emp_c, "Charlie"), (emp_d, "Dave")]:
        client.post("/employees", json={
            "emp_code": code,
            "name": name,
            "email": f"{code.lower()}@example.com",
            "department": dept,
            "shift_start": "09:30",
            "shift_end": "18:30",
            "joined_on": "2026-07-01"
        })

    # Add logs in 2026-07:
    # Alice: 50 min late
    # Bob: 40 min late
    # Charlie: 40 min late (tied with Bob)
    # Dave: 20 min late
    # 09:30 shift -> 10:20 IST is 50 min late; 10:10 IST is 40 min late; 09:50 IST is 20 min late
    t_alice = int(datetime(2026, 7, 1, 4, 50, 0, tzinfo=timezone.utc).timestamp() * 1000)
    t_bob = int(datetime(2026, 7, 1, 4, 40, 0, tzinfo=timezone.utc).timestamp() * 1000)
    t_charlie = int(datetime(2026, 7, 1, 4, 40, 0, tzinfo=timezone.utc).timestamp() * 1000)
    t_dave = int(datetime(2026, 7, 1, 4, 20, 0, tzinfo=timezone.utc).timestamp() * 1000)

    client.post("/attendance/punch-in", json={"emp_code": emp_a, "punched_at": t_alice})
    client.post("/attendance/punch-in", json={"emp_code": emp_b, "punched_at": t_bob})
    client.post("/attendance/punch-in", json={"emp_code": emp_c, "punched_at": t_charlie})
    client.post("/attendance/punch-in", json={"emp_code": emp_d, "punched_at": t_dave})

    # Query leaderboard with limit=2 for this department
    res = client.get(f"/analytics/leaderboard/late?month=2026-07&limit=2&department={dept}")
    assert res.status_code == 200
    items = res.json()["items"]

    # Since rank 1 is Alice (50), rank 2 are Bob (40) and Charlie (40),
    # Both Bob and Charlie have rank <= 2, so limit=2 returns 3 records: ranks 1, 2, 2!
    assert len(items) == 3
    assert items[0]["emp_code"] == emp_a
    assert items[0]["rank"] == 1
    assert items[1]["rank"] == 2
    assert items[2]["rank"] == 2
    assert [items[1]["emp_code"], items[2]["emp_code"]] == sorted([emp_b, emp_c])


def test_headcount_with_zero_logs_and_join_date():
    dept = "HeadcountDept"
    emp_joined_jul = "EMP7010"
    emp_joined_aug = "EMP7011"
    for e in [emp_joined_jul, emp_joined_aug]:
        db.employees.delete_one({"emp_code": e})
        db.attendance_logs.delete_many({"emp_code": e})

    # Employee joined in July with ZERO attendance logs
    client.post("/employees", json={
        "emp_code": emp_joined_jul,
        "name": "July Joiner",
        "email": "jul@example.com",
        "department": dept,
        "joined_on": "2026-07-20"
    })

    # Employee joined in August (future relative to July)
    client.post("/employees", json={
        "emp_code": emp_joined_aug,
        "name": "August Joiner",
        "email": "aug@example.com",
        "department": dept,
        "joined_on": "2026-08-01"
    })

    # Query July department summary:
    # Headcount should be 1 (only July joiner), even though they have 0 logs!
    res_jul = client.get(f"/analytics/departments/summary?month=2026-07&department={dept}")
    assert res_jul.status_code == 200
    items_jul = res_jul.json()["items"]
    assert len(items_jul) == 1
    assert items_jul[0]["headcount"] == 1
    assert items_jul[0]["present_days"] == 0.0

    # Query August summary:
    # Headcount should now be 2
    res_aug = client.get(f"/analytics/departments/summary?month=2026-08&department={dept}")
    assert res_aug.status_code == 200
    items_aug = res_aug.json()["items"]
    assert len(items_aug) == 1
    assert items_aug[0]["headcount"] == 2


def test_half_up_rounding_edge_cases():
    emp_code = "EMP7020"
    db.employees.delete_one({"emp_code": emp_code})
    db.attendance_logs.delete_many({"emp_code": emp_code})

    client.post("/employees", json={
        "emp_code": emp_code,
        "name": "Round Tester",
        "email": "round@example.com",
        "department": "QA",
        "joined_on": "2026-07-01"
    })

    # 16182 seconds / 3600 = 4.49500000...
    # Half-up rounded to 2 decimals -> 4.50 (NOT half-day)
    in_dt = datetime(2026, 7, 1, 4, 0, 0, tzinfo=timezone.utc)
    out_dt_450 = in_dt + timedelta(seconds=16182)

    client.post("/attendance/punch-in", json={"emp_code": emp_code, "punched_at": int(in_dt.timestamp() * 1000)})
    res_out = client.post("/attendance/punch-out", json={"emp_code": emp_code, "punched_at": int(out_dt_450.timestamp() * 1000)})
    assert res_out.status_code == 200
    data = res_out.json()
    assert data["work_hours"] == 4.50
    assert data["half_day"] is False

    # Day 2: 16181 seconds / 3600 = 4.49472... -> 4.49 -> half_day is True (< 4.50)
    in_dt2 = datetime(2026, 7, 2, 4, 0, 0, tzinfo=timezone.utc)
    out_dt_449 = in_dt2 + timedelta(seconds=16181)

    client.post("/attendance/punch-in", json={"emp_code": emp_code, "punched_at": int(in_dt2.timestamp() * 1000)})
    res_out2 = client.post("/attendance/punch-out", json={"emp_code": emp_code, "punched_at": int(out_dt_449.timestamp() * 1000)})
    assert res_out2.status_code == 200
    data2 = res_out2.json()
    assert data2["work_hours"] == 4.49
    assert data2["half_day"] is True


def test_concurrency_regularization():
    emp_code = "EMP7030"
    db.employees.delete_one({"emp_code": emp_code})
    db.attendance_logs.delete_many({"emp_code": emp_code})

    client.post("/employees", json={
        "emp_code": emp_code,
        "name": "Reg Concurrency",
        "email": "reg_conc@example.com",
        "department": "Engineering",
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-07-01"
    })

    # Punch in at 09:30 IST on 2026-07-06
    in_ms = int(datetime(2026, 7, 6, 4, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
    client.post("/attendance/punch-in", json={"emp_code": emp_code, "punched_at": in_ms})

    # Punch out at 18:30 IST
    out_ms = int(datetime(2026, 7, 6, 13, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
    client.post("/attendance/punch-out", json={"emp_code": emp_code, "punched_at": out_ms})

    # Two concurrent regularizations attempting to update the same record:
    # One changes status to WFH
    # Another changes status to ON_DUTY
    def reg1():
        return client.patch(f"/attendance/{emp_code}/2026-07-06", json={
            "status": "WFH",
            "reason": "Manager approved WFH",
            "regularized_by": "manager1"
        }).status_code

    def reg2():
        return client.patch(f"/attendance/{emp_code}/2026-07-06", json={
            "status": "ON_DUTY",
            "reason": "Manager approved ON_DUTY",
            "regularized_by": "manager2"
        }).status_code

    with ThreadPoolExecutor(max_workers=2) as ex:
        statuses = list(ex.map(lambda f: f(), [reg1, reg2]))

    # At least one must succeed (200), and if both arrive at same history state, the second gets 409
    assert 200 in statuses
    # History must contain valid audit trail without corruption
    rec = client.get(f"/attendance?emp_code={emp_code}").json()["items"][0]
    assert len(rec["history"]) >= 1


def test_regularization_validation_edge_cases():
    emp_code = "EMP7040"
    db.employees.delete_one({"emp_code": emp_code})
    db.attendance_logs.delete_many({"emp_code": emp_code})

    client.post("/employees", json={
        "emp_code": emp_code,
        "name": "Validation Tester",
        "email": "val_reg@example.com",
        "department": "Engineering",
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-07-01"
    })

    in_ms = int(datetime(2026, 7, 6, 4, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
    client.post("/attendance/punch-in", json={"emp_code": emp_code, "punched_at": in_ms})

    # Setting ABSENT while providing punch_in -> 422
    res_bad1 = client.patch(f"/attendance/{emp_code}/2026-07-06", json={
        "status": "ABSENT",
        "punch_in": in_ms,
        "reason": "Invalid test attempt",
        "regularized_by": "admin"
    })
    assert res_bad1.status_code == 422

    # Setting punch_in to date that does not match record's attendance date -> 422
    wrong_date_ms = int(datetime(2026, 7, 7, 4, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
    res_bad2 = client.patch(f"/attendance/{emp_code}/2026-07-06", json={
        "punch_in": wrong_date_ms,
        "reason": "Date mismatch test",
        "regularized_by": "admin"
    })
    assert res_bad2.status_code == 422


