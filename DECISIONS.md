# DECISIONS.md

1. **Indexes.**
On `employees`: unique `{"emp_code": 1}` guarantees employee identity; compound `{"department": 1, "joined_on": 1}` and `{"joined_on": 1}` support department filtering and headcount calculations.
On `attendance_logs`: unique compound `{"emp_code": 1, "date": 1}` enforces one record per employee-day and ensures race-safe punch-ins; compound `{"date": -1, "emp_code": 1}` and `{"status": 1, "date": -1, "emp_code": 1}` serve indexed pagination; `{"emp_code": 1, "punch_in": -1}` enables indexed punch-out lookups; `{"date": 1, "late_minutes": 1, "emp_code": 1}` backs monthly late-comer aggregations.
I considered and rejected an index on `history.at` because history is an embedded audit array that is never queried independently.

2. **Punch-in race.**
Two simultaneous punch-in requests validate the payload and compute the identical attendance date. Both attempt `insert_one`. MongoDB's unique compound index on `(emp_code, date)` atomically accepts the first insert (returning 201). The second insert immediately throws a `DuplicateKeyError`, which our handler catches and converts into a 409 Conflict response.

3. **Ties.**
The pipeline calculates standard competition ranking (1, 2, 2, 4) using `$setWindowFields` with `$rank`, applying `rank <= limit` afterward. When employees tie at the cutoff, both satisfy `rank <= limit` and are returned, sorted by `total_late_minutes` descending, then `emp_code` ascending.

4. **Headcount.**
The department summary pipeline queries the `employees` collection first (matching `joined_on <= month_end`), then `$lookup`s `attendance_logs`. Headcount is calculated as `$sum: 1` over matched employees in the department group stage, so employees with zero logs are always counted.

5. **One thing you would change.**
At 100x volume (10M+ records), I would replace dynamic cross-collection monthly aggregations with pre-computed daily departmental rollups updated asynchronously via MongoDB Change Streams.
