"""
Sureline — Mahakash Synthetic Data Generator

Creates a realistic SQLite database for Mahakash — India's most
"ambitious" space company. Data is realistic and queryable.
The DOCUMENTS are where the goofiness lives.

Tables:
  - employees
  - sales
  - products
  - expenses
  - clients
"""

import sqlite3
import csv
import random
from pathlib import Path
from datetime import datetime, timedelta

# ─── Seed for reproducibility ───────────────────────────────────
random.seed(42)

DATA_DIR = Path(__file__).parent
DB_PATH = DATA_DIR / "mahakash.db"
CSV_PATH = DATA_DIR / "mahakash_sales.csv"


# ─── Reference data ─────────────────────────────────────────────
DEPARTMENTS = [
    "Rocket Propulsion", "Satellite Division", "Mission Control",
    "Space Suit Design", "Astro-Catering", "Ground Operations",
    "Payload Engineering", "HR & Culture", "Finance", "Marketing"
]

DESIGNATIONS = [
    "Junior Engineer", "Senior Engineer", "Lead Engineer",
    "Manager", "Senior Manager", "Director", "VP",
    "Chief Rocket Officer", "Intern (with dreams)"
]

FIRST_NAMES = [
    "Arjun", "Priya", "Vikram", "Neha", "Rohit", "Ananya", "Karthik",
    "Deepa", "Sanjay", "Meera", "Amit", "Pooja", "Ravi", "Sunita",
    "Harish", "Kavita", "Manoj", "Shreya", "Suresh", "Divya",
    "Nikhil", "Asha", "Rajesh", "Swati", "Gaurav", "Lakshmi",
    "Praveen", "Nandini", "Arun", "Tara"
]

LAST_NAMES = [
    "Sharma", "Patel", "Reddy", "Iyer", "Khan", "Mehta", "Nair",
    "Gupta", "Joshi", "Singh", "Das", "Banerjee", "Rao",
    "Mukherjee", "Choudhury"
]

PRODUCTS = [
    ("MahaSat-1", "Satellite", 45000000),
    ("MahaSat-Nano", "Satellite", 8500000),
    ("Pushpak Mk-I", "Launch Vehicle", 120000000),
    ("Pushpak Mk-II", "Launch Vehicle", 185000000),
    ("Akash Shield", "Debris Tracking", 12000000),
    ("Orbit Express", "Rideshare Service", 3500000),
    ("StarLink-Desi", "Communication Module", 6700000),
    ("Gaganyaan Suit v2", "Space Suit", 2500000),
    ("MahaFuel Booster", "Propulsion Component", 18000000),
    ("Ground Station Kit", "Ground Equipment", 5500000),
]

REGIONS = ["North India", "South India", "West India", "East India", "International"]

CLIENT_NAMES = [
    ("ISRO", "Government", "Bengaluru"),
    ("Indian Defence Ministry", "Government", "New Delhi"),
    ("Bharti Airtel", "Telecom", "Gurugram"),
    ("Tata Communications", "Telecom", "Mumbai"),
    ("Reliance Jio", "Telecom", "Mumbai"),
    ("OneWeb India", "Satellite Internet", "New Delhi"),
    ("Pixxel", "Earth Observation", "Bengaluru"),
    ("Dhruva Space", "Satellite", "Hyderabad"),
    ("AgriSat Corp", "Agriculture Tech", "Pune"),
    ("Maritime Watch Ltd", "Shipping", "Chennai"),
    ("GlobalVu Defence", "Defence", "International"),
    ("European Space Agency", "Government", "International"),
    ("SpaceBridge Japan", "Telecom", "International"),
]

EXPENSE_CATEGORIES = [
    "Fuel & Propellants", "Equipment & Machinery", "Salaries & Wages",
    "R&D", "Testing & Simulation", "Office Supplies",
    "Astro-Catering (samosas for astronauts)", "Travel",
    "Insurance (rocket insurance is wild)", "Software Licenses"
]


def _random_date(start_year: int = 2023, end_year: int = 2025) -> str:
    """Generate a random date string."""
    start = datetime(start_year, 1, 1)
    end = datetime(end_year, 12, 31)
    delta = (end - start).days
    return (start + timedelta(days=random.randint(0, delta))).strftime("%Y-%m-%d")


def create_database():
    """Create the Mahakash SQLite database with all tables and sample data."""
    # Remove old DB if exists
    if DB_PATH.exists():
        DB_PATH.unlink()

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # ── Employees ────────────────────────────────────────────────
    cursor.execute("""
        CREATE TABLE employees (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            department TEXT NOT NULL,
            designation TEXT NOT NULL,
            salary_monthly INTEGER NOT NULL,
            join_date TEXT NOT NULL,
            email TEXT NOT NULL
        )
    """)

    employees = []
    used_names = set()
    for i in range(80):
        while True:
            first = random.choice(FIRST_NAMES)
            last = random.choice(LAST_NAMES)
            full = f"{first} {last}"
            if full not in used_names:
                used_names.add(full)
                break
        dept = random.choice(DEPARTMENTS)
        desig = random.choice(DESIGNATIONS)
        salary = random.randint(25000, 350000)  # INR monthly
        join_date = _random_date(2015, 2025)
        email = f"{first.lower()}.{last.lower()}@mahakash.space"
        employees.append((full, dept, desig, salary, join_date, email))

    cursor.executemany(
        "INSERT INTO employees (name, department, designation, salary_monthly, join_date, email) VALUES (?, ?, ?, ?, ?, ?)",
        employees,
    )

    # ── Products ─────────────────────────────────────────────────
    cursor.execute("""
        CREATE TABLE products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            base_price INTEGER NOT NULL,
            units_in_stock INTEGER NOT NULL
        )
    """)

    for pname, category, price in PRODUCTS:
        stock = random.randint(1, 15)
        cursor.execute(
            "INSERT INTO products (name, category, base_price, units_in_stock) VALUES (?, ?, ?, ?)",
            (pname, category, price, stock),
        )

    # ── Clients ──────────────────────────────────────────────────
    cursor.execute("""
        CREATE TABLE clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            industry TEXT NOT NULL,
            region TEXT NOT NULL,
            contact_person TEXT NOT NULL
        )
    """)

    for cname, industry, region in CLIENT_NAMES:
        contact = f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"
        cursor.execute(
            "INSERT INTO clients (name, industry, region, contact_person) VALUES (?, ?, ?, ?)",
            (cname, industry, region, contact),
        )

    # ── Sales ────────────────────────────────────────────────────
    cursor.execute("""
        CREATE TABLE sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            client_id INTEGER NOT NULL,
            salesperson_id INTEGER NOT NULL,
            region TEXT NOT NULL,
            amount INTEGER NOT NULL,
            sale_date TEXT NOT NULL,
            quarter TEXT NOT NULL,
            FOREIGN KEY (product_id) REFERENCES products(id),
            FOREIGN KEY (client_id) REFERENCES clients(id),
            FOREIGN KEY (salesperson_id) REFERENCES employees(id)
        )
    """)

    sales_rows = []
    for _ in range(200):
        product_idx = random.randint(1, len(PRODUCTS))
        client_idx = random.randint(1, len(CLIENT_NAMES))
        salesperson_idx = random.randint(1, 80)
        region = random.choice(REGIONS)
        base = PRODUCTS[product_idx - 1][2]
        # Add some variance (±20%)
        amount = int(base * random.uniform(0.8, 1.2))
        sale_date = _random_date(2023, 2025)
        # Derive quarter
        month = int(sale_date.split("-")[1])
        year = sale_date.split("-")[0]
        q = f"Q{(month - 1) // 3 + 1} {year}"
        sales_rows.append((product_idx, client_idx, salesperson_idx, region, amount, sale_date, q))

    cursor.executemany(
        "INSERT INTO sales (product_id, client_id, salesperson_id, region, amount, sale_date, quarter) VALUES (?, ?, ?, ?, ?, ?, ?)",
        sales_rows,
    )

    # ── Expenses ─────────────────────────────────────────────────
    cursor.execute("""
        CREATE TABLE expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            department TEXT NOT NULL,
            amount INTEGER NOT NULL,
            expense_date TEXT NOT NULL,
            description TEXT
        )
    """)

    expense_descriptions = {
        "Fuel & Propellants": ["Liquid hydrogen shipment", "Solid booster fuel Q3", "Emergency propellant restock"],
        "Equipment & Machinery": ["CNC lathe for nozzle fabrication", "Vibration test rig", "Thermal vacuum chamber"],
        "Salaries & Wages": ["Monthly payroll", "Bonus disbursement", "Contractor payments"],
        "R&D": ["Reusable rocket R&D", "Ion thruster prototype", "AI navigation system"],
        "Testing & Simulation": ["Static fire test", "Wind tunnel simulation", "Payload deployment test"],
        "Office Supplies": ["Ergonomic chairs (for rocket scientists)", "Whiteboards for brainstorming", "Post-its (essential)"],
        "Astro-Catering (samosas for astronauts)": ["500 samosas for launch day", "Biryani for mission control night shift", "Chai supply chain management"],
        "Travel": ["Sriharikota launch site visit", "Conference in Toulouse", "Client visit to ISRO"],
        "Insurance (rocket insurance is wild)": ["Annual vehicle insurance", "Payload insurance Q2", "Launch liability coverage"],
        "Software Licenses": ["MATLAB license renewal", "ANSYS simulation suite", "Jira (the real rocket fuel)"],
    }

    for _ in range(150):
        category = random.choice(EXPENSE_CATEGORIES)
        dept = random.choice(DEPARTMENTS)
        if category == "Salaries & Wages":
            amount = random.randint(500000, 5000000)
        elif category in ("Fuel & Propellants", "Equipment & Machinery", "Insurance (rocket insurance is wild)"):
            amount = random.randint(1000000, 25000000)
        elif category == "Astro-Catering (samosas for astronauts)":
            amount = random.randint(5000, 100000)
        else:
            amount = random.randint(50000, 3000000)
        date = _random_date(2023, 2025)
        desc = random.choice(expense_descriptions.get(category, ["General expense"]))
        cursor.execute(
            "INSERT INTO expenses (category, department, amount, expense_date, description) VALUES (?, ?, ?, ?, ?)",
            (category, dept, amount, date, desc),
        )

    conn.commit()

    # ── Export sales to CSV ──────────────────────────────────────
    cursor.execute("""
        SELECT s.id, p.name as product, c.name as client, e.name as salesperson,
               s.region, s.amount, s.sale_date, s.quarter
        FROM sales s
        JOIN products p ON s.product_id = p.id
        JOIN clients c ON s.client_id = c.id
        JOIN employees e ON s.salesperson_id = e.id
    """)

    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "product", "client", "salesperson", "region", "amount", "sale_date", "quarter"])
        writer.writerows(cursor.fetchall())

    # ── Summary ──────────────────────────────────────────────────
    counts = {}
    for table in ["employees", "products", "clients", "sales", "expenses"]:
        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        counts[table] = cursor.fetchone()[0]

    conn.close()

    print("✅ Mahakash database created successfully!")
    print(f"   📍 {DB_PATH}")
    print(f"   📊 Tables: {counts}")
    print(f"   📄 CSV export: {CSV_PATH}")
    return counts


if __name__ == "__main__":
    create_database()
