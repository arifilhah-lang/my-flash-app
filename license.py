import os
from flask import Flask, request, jsonify, render_template_string, redirect, send_file, g
import sqlite3
import random
import string
from datetime import datetime, timedelta

app = Flask(__name__)

UPDATE_FOLDER = "updates"
os.makedirs(UPDATE_FOLDER, exist_ok=True)

# ================= DB =================
def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(
            "master_licenses.db",
            detect_types=sqlite3.PARSE_DECLTYPES,
            check_same_thread=False
        )
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(e=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()

def init_db():
    conn = get_db()
    conn.execute('''CREATE TABLE IF NOT EXISTS licenses 
        (id INTEGER PRIMARY KEY, key TEXT UNIQUE, shop_name TEXT,
         expiry_date TIMESTAMP, domain TEXT, status TEXT DEFAULT 'Active',
         phone TEXT, address TEXT)''')

    conn.execute('''CREATE TABLE IF NOT EXISTS sys_settings 
        (id INTEGER PRIMARY KEY, latest_version TEXT)''')

    if not conn.execute("SELECT * FROM sys_settings").fetchone():
        conn.execute("INSERT INTO sys_settings (id, latest_version) VALUES (1, '1.0')")

    conn.execute('''CREATE TABLE IF NOT EXISTS fraud_logs
        (id INTEGER PRIMARY KEY, key TEXT, attempted_domain TEXT,
         actual_domain TEXT, attempt_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')

    conn.commit()

with app.app_context():
    init_db()

# ================= KEY GENERATOR =================
def generate_key():
    return "PAGLA-" + ''.join(random.choices(string.ascii_uppercase + string.digits, k=4)) + "-" + ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))

# ================= UPDATE =================
@app.route('/check_update', methods=['POST'])
def check_update():
    conn = get_db()
    st = conn.execute("SELECT latest_version FROM sys_settings WHERE id=1").fetchone()
    conn.close()

    version = st['latest_version'] if st else "1.0"

    if os.path.exists(os.path.join(UPDATE_FOLDER, "update.zip")):
        return jsonify({
            "latest_version": version,
            "download_url": request.host_url.rstrip('/') + "/download_update"
        })

    return jsonify({"latest_version": version, "download_url": ""})

@app.route('/download_update')
def download_update():
    file_path = os.path.join(UPDATE_FOLDER, "update.zip")
    if os.path.exists(file_path):
        return send_file(file_path, as_attachment=True)
    return "No file", 404

# ================= DASHBOARD =================
@app.route('/')
def dashboard():
    conn = get_db()

    licenses = conn.execute(
        "SELECT * FROM licenses ORDER BY expiry_date ASC LIMIT 500"
    ).fetchall()

    html = """
    <h2>License Panel</h2>
    <form method="POST" action="/create">
        <input name="shop_name" placeholder="Shop" required>
        <input name="phone" placeholder="Phone" required>
        <input name="address" placeholder="Address">
        <input name="days" type="number" value="365">
        <button>Create</button>
    </form>
    <hr>
    """

    for l in licenses:
        html += f"""
        <p>
        {l['shop_name']} | {l['phone']} | 
        <b>{l['key']}</b> | Exp: {str(l['expiry_date']).split(' ')[0]}
        </p>
        """

    return html

# ================= CREATE =================
@app.route('/create', methods=['POST'])
def create():
    conn = get_db()

    while True:
        key = generate_key()
        exists = conn.execute("SELECT id FROM licenses WHERE key=?", (key,)).fetchone()
        if not exists:
            break

    expiry = datetime.now() + timedelta(days=int(request.form.get('days', 365)))

    conn.execute(
        "INSERT INTO licenses (key, shop_name, phone, address, expiry_date) VALUES (?, ?, ?, ?, ?)",
        (
            key,
            request.form.get('shop_name'),
            request.form.get('phone'),
            request.form.get('address'),
            expiry
        )
    )

    conn.commit()
    return redirect('/')

# ================= VERIFY =================
@app.route('/verify_license', methods=['POST'])
def verify_license():
    data = request.get_json(force=True)

    key = data.get('key')
    domain = data.get('domain')

    if not key or not domain:
        return jsonify({"valid": False})

    conn = get_db()
    lic = conn.execute("SELECT * FROM licenses WHERE key=?", (key,)).fetchone()

    if not lic:
        return jsonify({"valid": False})

    if lic['status'] == 'Blocked':
        return jsonify({"valid": False})

    if not lic['domain']:
        conn.execute("UPDATE licenses SET domain=? WHERE key=?", (domain, key))
        conn.commit()

    elif lic['domain'] != domain:
        conn.execute(
            "INSERT INTO fraud_logs (key, attempted_domain, actual_domain) VALUES (?, ?, ?)",
            (key, domain, lic['domain'])
        )
        conn.commit()
        return jsonify({"valid": False})

    expiry = datetime.strptime(str(lic['expiry_date']).split('.')[0], "%Y-%m-%d %H:%M:%S")

    if datetime.now() > expiry:
        return jsonify({"valid": False, "expired": True})

    return jsonify({
        "valid": True,
        "expiry": expiry.strftime("%Y-%m-%d")
    })

# ================= RUN =================
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=False)
