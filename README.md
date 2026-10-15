# Employee Attendance & Analytics API

A FastAPI and MongoDB backend service for employee attendance tracking, regularization audit history, and organizational analytics, implementing the complete specification and business rules (R1–R10).

## Requirements
- Python 3.11+
- MongoDB 6.0+ (Local instance or MongoDB Atlas cluster)

## Setup & Running

### 1. Virtual Environment & Dependencies
On Windows (PowerShell):
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

On Linux/macOS:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Environment Variables
Copy `.env.example` to `.env` or set environment variables directly:

```powershell
# Windows PowerShell
$env:MONGO_URI = "mongodb+srv://<username>:<password>@cluster0.6jkj3ig.mongodb.net/?appName=Cluster0"
$env:MONGO_DB  = "hrone_attendance"
```

For local MongoDB:
```env
MONGO_URI=mongodb://localhost:27017
MONGO_DB=hrone_attendance
```

> **Note:** Special characters in MongoDB passwords (e.g. `@`, `:`, `/`, `%`) must be URL-encoded (RFC 3986). Real environment variables override `.env` values.

### 3. Seed Sample Data (Optional)
```bash
python sample_seed.py
```

### 4. Run Application Server
```bash
uvicorn app.main:app --port 8000
```
Interactive API documentation will be available at `http://localhost:8000/docs` and OpenAPI schema at `http://localhost:8000/openapi.json`.

## Testing
Run the comprehensive test suite covering status codes, business rules, concurrency, edge cases, and MongoDB explain plans:
```bash
python -m pytest -v
```

## Implementation Highlights
- **Single File Architecture:** All application code, models, business rules, helpers, and endpoints reside in [app/main.py](file:///d:/OneDrive/Documents/HrOne-Assignment/app/main.py).
- **Audit History & Concurrency:** Concurrency-safe punch-in (unique index) and punch-out (`find_one_and_update`), plus optimistic history versioning on regularization (`PATCH`) to prevent lost updates.
- **Aggregation Pipelines:** Analytics endpoints (`/analytics/employees/{emp_code}/monthly`, `/analytics/departments/summary`, `/analytics/leaderboard/late`, `/analytics/departments/{department}/trend`) run purely as MongoDB aggregation pipelines using `$lookup`, `$densify`, and `$setWindowFields`.
- **Index Scans:** All listing, pagination, and analytics queries execute via index scans (`IXSCAN`) without collection scans (`COLLSCAN`), verifiable through `GET /admin/explain/{endpoint}`.
