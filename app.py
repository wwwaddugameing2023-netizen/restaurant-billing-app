
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
import sqlite3
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date

app = Flask(__name__)
app.secret_key = "change-this-secret-key-before-deployment"

DB = "restaurant.db"


def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def initialize_database():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS owners (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS food_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            price REAL NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer TEXT,
            total REAL NOT NULL,
            order_date TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS order_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            food_name TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            price REAL NOT NULL
        )
    """)

    owner = cur.execute("SELECT id FROM owners LIMIT 1").fetchone()
    if not owner:
        cur.execute(
            "INSERT INTO owners (username, password) VALUES (?, ?)",
            ("admin", generate_password_hash("Admin@123"))
        )

    count = cur.execute("SELECT COUNT(*) FROM food_items").fetchone()[0]
    if count == 0:
        menu = [
            ("Zinger Burger", 450),
            ("Chicken Burger", 350),
            ("Pizza", 1200),
            ("French Fries", 250),
            ("Chicken Biryani", 300),
            ("Cold Drink", 100)
        ]
        cur.executemany(
            "INSERT INTO food_items (name, price) VALUES (?, ?)", menu
        )

    conn.commit()
    conn.close()


def owner_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if "owner_id" not in session:
            return redirect(url_for("login"))
        return func(*args, **kwargs)
    return wrapper


@app.route("/")
def worker():
    conn = get_db()
    foods = conn.execute("SELECT * FROM food_items ORDER BY name").fetchall()
    conn.close()
    return render_template("worker.html", foods=foods)


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        conn = get_db()
        owner = conn.execute(
            "SELECT * FROM owners WHERE username = ?", (username,)
        ).fetchone()
        conn.close()

        if owner and check_password_hash(owner["password"], password):
            session["owner_id"] = owner["id"]
            session["owner_username"] = owner["username"]
            return redirect(url_for("owner_dashboard"))

        flash("Username ya password ghalat hai.")

    return render_template("login.html")


@app.route("/logout")
@owner_required
def logout():
    session.pop("owner_id", None)
    session.pop("owner_username", None)
    return redirect(url_for("worker"))


@app.route("/create-order", methods=["POST"])
def create_order():
    data = request.get_json(silent=True) or {}
    customer = (data.get("customer") or "Walk-in Customer").strip()
    items = data.get("items", [])

    if not isinstance(items, list) or not items:
        return jsonify({"error": "Cart khali hai."}), 400

    conn = get_db()
    total = 0
    prepared = []

    for item in items:
        try:
            food_id = int(item.get("id"))
            quantity = int(item.get("quantity"))
        except (TypeError, ValueError):
            conn.close()
            return jsonify({"error": "Invalid item ya quantity."}), 400

        if quantity < 1 or quantity > 1000:
            conn.close()
            return jsonify({"error": "Quantity 1 se 1000 ke darmiyan honi chahiye."}), 400

        food = conn.execute(
            "SELECT * FROM food_items WHERE id = ?", (food_id,)
        ).fetchone()

        if not food:
            conn.close()
            return jsonify({"error": "Food item nahi mila."}), 400

        line_total = float(food["price"]) * quantity
        total += line_total
        prepared.append((food["name"], quantity, float(food["price"])))

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO orders (customer, total, order_date) VALUES (?, ?, ?)",
        (customer, total, now)
    )
    order_id = cur.lastrowid

    for name, quantity, price in prepared:
        cur.execute("""
            INSERT INTO order_items (order_id, food_name, quantity, price)
            VALUES (?, ?, ?, ?)
        """, (order_id, name, quantity, price))

    conn.commit()
    conn.close()

    session["last_worker_bill"] = order_id
    return jsonify({"success": True, "bill_url": url_for("bill", order_id=order_id)})


@app.route("/bill/<int:order_id>")
def bill(order_id):
    if "owner_id" not in session and session.get("last_worker_bill") != order_id:
        return "Is bill ko dekhne ki permission nahi hai.", 403

    conn = get_db()
    order = conn.execute(
        "SELECT * FROM orders WHERE id = ?", (order_id,)
    ).fetchone()

    if not order:
        conn.close()
        return "Bill nahi mila.", 404

    items = conn.execute(
        "SELECT * FROM order_items WHERE order_id = ?", (order_id,)
    ).fetchall()
    conn.close()

    return render_template("bill.html", order=order, items=items)


@app.route("/owner")
@owner_required
def owner_dashboard():
    conn = get_db()
    today = date.today().isoformat()

    today_orders = conn.execute(
        "SELECT COUNT(*) FROM orders WHERE substr(order_date, 1, 10) = ?",
        (today,)
    ).fetchone()[0]

    today_sales = conn.execute(
        "SELECT COALESCE(SUM(total), 0) FROM orders WHERE substr(order_date, 1, 10) = ?",
        (today,)
    ).fetchone()[0]

    all_orders = conn.execute(
        "SELECT COUNT(*) FROM orders"
    ).fetchone()[0]

    all_sales = conn.execute(
        "SELECT COALESCE(SUM(total), 0) FROM orders"
    ).fetchone()[0]

    foods = conn.execute(
        "SELECT * FROM food_items ORDER BY name"
    ).fetchall()

    orders = conn.execute(
        "SELECT * FROM orders ORDER BY id DESC LIMIT 100"
    ).fetchall()

    conn.close()

    return render_template(
        "owner.html",
        today_orders=today_orders,
        today_sales=today_sales,
        all_orders=all_orders,
        all_sales=all_sales,
        foods=foods,
        orders=orders
    )


@app.route("/add-food", methods=["POST"])
@owner_required
def add_food():
    name = request.form.get("name", "").strip()
    try:
        price = float(request.form.get("price", ""))
    except ValueError:
        flash("Valid price enter karein.")
        return redirect(url_for("owner_dashboard"))

    if not name or price <= 0:
        flash("Food name aur positive price zaroori hain.")
        return redirect(url_for("owner_dashboard"))

    conn = get_db()
    conn.execute(
        "INSERT INTO food_items (name, price) VALUES (?, ?)", (name, price)
    )
    conn.commit()
    conn.close()
    flash("Food item add ho gaya.")
    return redirect(url_for("owner_dashboard"))


@app.route("/update-food/<int:food_id>", methods=["POST"])
@owner_required
def update_food(food_id):
    name = request.form.get("name", "").strip()
    try:
        price = float(request.form.get("price", ""))
    except ValueError:
        flash("Valid price enter karein.")
        return redirect(url_for("owner_dashboard"))

    if not name or price <= 0:
        flash("Food name aur positive price zaroori hain.")
        return redirect(url_for("owner_dashboard"))

    conn = get_db()
    conn.execute(
        "UPDATE food_items SET name = ?, price = ? WHERE id = ?",
        (name, price, food_id)
    )
    conn.commit()
    conn.close()
    flash("Food item update ho gaya.")
    return redirect(url_for("owner_dashboard"))


@app.route("/delete-food/<int:food_id>", methods=["POST"])
@owner_required
def delete_food(food_id):
    conn = get_db()
    conn.execute("DELETE FROM food_items WHERE id = ?", (food_id,))
    conn.commit()
    conn.close()
    flash("Food item delete ho gaya.")
    return redirect(url_for("owner_dashboard"))


@app.route("/change-password", methods=["GET", "POST"])
@owner_required
def change_password():
    if request.method == "POST":
        current = request.form.get("current_password", "")
        new = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")

        conn = get_db()
        owner = conn.execute(
            "SELECT * FROM owners WHERE id = ?", (session["owner_id"],)
        ).fetchone()

        if not check_password_hash(owner["password"], current):
            flash("Current password ghalat hai.")
        elif len(new) < 8:
            flash("New password kam az kam 8 characters ka ho.")
        elif new != confirm:
            flash("New passwords match nahi karte.")
        else:
            conn.execute(
                "UPDATE owners SET password = ? WHERE id = ?",
                (generate_password_hash(new), session["owner_id"])
            )
            conn.commit()
            flash("Password successfully change ho gaya.")
            conn.close()
            return redirect(url_for("owner_dashboard"))

        conn.close()

    return render_template("change_password.html")


@app.route("/sales")
@owner_required
def sales():
    selected_date = request.args.get("date") or date.today().isoformat()
    conn = get_db()
    orders = conn.execute("""
        SELECT * FROM orders
        WHERE substr(order_date, 1, 10) = ?
        ORDER BY id DESC
    """, (selected_date,)).fetchall()

    total = sum(float(order["total"]) for order in orders)
    conn.close()

    return render_template(
        "sales.html", orders=orders, total=total,
        selected_date=selected_date
    )


@app.route("/delete-order/<int:order_id>", methods=["POST"])
@owner_required
def delete_order(order_id):
    conn = get_db()
    conn.execute("DELETE FROM order_items WHERE order_id = ?", (order_id,))
    conn.execute("DELETE FROM orders WHERE id = ?", (order_id,))
    conn.commit()
    conn.close()
    flash("Order delete ho gaya.")
    return redirect(url_for("owner_dashboard"))


@app.route("/delete-today", methods=["POST"])
@owner_required
def delete_today():
    today = date.today().isoformat()
    conn = get_db()
    ids = conn.execute(
        "SELECT id FROM orders WHERE substr(order_date, 1, 10) = ?",
        (today,)
    ).fetchall()

    for row in ids:
        conn.execute("DELETE FROM order_items WHERE order_id = ?", (row["id"],))

    conn.execute(
        "DELETE FROM orders WHERE substr(order_date, 1, 10) = ?", (today,)
    )
    conn.commit()
    conn.close()
    flash("Aaj ke orders delete ho gaye.")
    return redirect(url_for("owner_dashboard"))


@app.route("/delete-all-history", methods=["POST"])
@owner_required
def delete_all_history():
    conn = get_db()
    conn.execute("DELETE FROM order_items")
    conn.execute("DELETE FROM orders")
    conn.commit()
    conn.close()
    flash("Tamam order history delete ho gayi.")
    return redirect(url_for("owner_dashboard"))


initialize_database()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
