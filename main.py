import os 
import csv
import uuid
import base64
import logging
from fastapi import FastAPI, Form, Request, HTTPException, Depends, Cookie
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import qrcode
from io import BytesIO
from filelock import FileLock
from datetime import datetime

# --- Static dependency auto-download, as before ---
import urllib.request
STATIC_FILES = [
    {
        "url": "https://unpkg.com/html5-qrcode@2.3.8/html5-qrcode.min.js",
        "local": os.path.join("static", "html5-qrcode.min.js")
    },
    {
        "url": "https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js",
        "local": os.path.join("static", "bootstrap.bundle.min.js")
    }
]
for file in STATIC_FILES:
    if not os.path.exists(file["local"]):
        print(f"Downloading {file['local']} ...")
        try:
            urllib.request.urlretrieve(file["url"], file["local"])
        except Exception as e:
            print(f"Could not download {file['url']}! Please check your internet connection. Error: {e}")

# === Configuration and Setup ===
DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)
CSV_PATH = os.path.join(DATA_DIR, "guests.csv")
CSV_LOCK = os.path.join(DATA_DIR, "guests.csv.lock")
CSV_FIELDS = ["id", "name", "phone", "address", "profession", "notes", "added", "created", "plus_one"]

# Authentication
ADMIN_PASSWORD = "admin123kotak"
SESSION_COOKIE_NAME = "kotak_admin_session"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# === Authentication Functions ===

def check_admin_auth(kotak_admin_session: str = Cookie(None)):
    """Check if user is authenticated as admin"""
    if kotak_admin_session != "authenticated":
        raise HTTPException(status_code=401, detail="Authentication required")
    return True

def admin_required(kotak_admin_session: str = Cookie(None)):
    """Dependency for admin-only routes"""
    return check_admin_auth(kotak_admin_session)

# === Utility Functions ===

def log_with_uid(uid, msg):
    logging.info(f"[{uid}] {msg}")

def backup_csv():
    if os.path.exists(CSV_PATH):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = f"{CSV_PATH}.bak_{ts}"
        with open(CSV_PATH, "rb") as src, open(dest, "wb") as dst:
            dst.write(src.read())
        logging.info(f"CSV backed up to {dest}")

def lock_and_read():
    with FileLock(CSV_LOCK, timeout=5):
        if not os.path.exists(CSV_PATH):
            return []
        with open(CSV_PATH, newline='', encoding="utf-8") as f:
            return list(csv.DictReader(f))

def lock_and_write(data):
    backup_csv()
    with FileLock(CSV_LOCK, timeout=5):
        with open(CSV_PATH, "w", newline='', encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            w.writeheader()
            w.writerows(data)

def gen_guest_id(name, phone):
    prefix = (name[:2].upper() if name else "GN") + phone[-4:] + uuid.uuid4().hex[:6].upper()
    return prefix

def validate_phone(phone):
    return phone.isdigit() and len(phone) == 10

def guest_lookup(id_or_phone):
    guests = lock_and_read()
    for g in guests:
        if g["id"] == id_or_phone or g["phone"] == id_or_phone:
            return g
    return None

def qr_b64(data):
    img = qrcode.make(data)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")

def mark_checked_in(id_or_phone):
    guests = lock_and_read()
    changed = False
    guest_found = None
    for g in guests:
        if g["id"] == id_or_phone or g["phone"] == id_or_phone:
            if g["added"] != "yes":
                g["added"] = "yes"
                changed = True
            guest_found = g
    if changed:
        lock_and_write(guests)
    return guest_found, changed

def get_dashboard_stats(guests):
    total = len(guests)
    checked_in = sum(1 for g in guests if g["added"] == "yes")
    not_checked_in = total - checked_in
    plus_ones = sum(1 for g in guests if g.get("plus_one") == "yes")
    return dict(total=total, checked_in=checked_in, not_checked_in=not_checked_in, plus_ones=plus_ones)

# === ROUTES ===

@app.get("/", response_class=HTMLResponse)
def root(request: Request):
    return RedirectResponse("/register")

# ---- Admin Login ----
@app.get("/admin", response_class=HTMLResponse)
def admin_login_form(request: Request):
    return templates.TemplateResponse("admin_login.html", {"request": request, "error": None, "year": datetime.utcnow().year})

@app.post("/admin", response_class=HTMLResponse)
def admin_login_submit(request: Request, password: str = Form(...)):
    if password == ADMIN_PASSWORD:
        response = RedirectResponse("/admin/dashboard", status_code=302)
        response.set_cookie(SESSION_COOKIE_NAME, "authenticated", httponly=True)
        return response
    else:
        return templates.TemplateResponse("admin_login.html", {"request": request, "error": "Invalid password", "year": datetime.utcnow().year})

@app.get("/admin/logout")
def admin_logout():
    response = RedirectResponse("/", status_code=302)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response

@app.get("/admin/dashboard", response_class=HTMLResponse)
def admin_dashboard(request: Request, auth: bool = Depends(admin_required)):
    return templates.TemplateResponse("admin_dashboard.html", {"request": request, "year": datetime.utcnow().year})

# ---- 1. Registration (Public) ----
@app.get("/register", response_class=HTMLResponse)
def register_form(request: Request):
    return templates.TemplateResponse("register.html", {"request": request, "message": None, "year": datetime.utcnow().year})

@app.post("/register", response_class=HTMLResponse)
def register_submit(
    request: Request,
    name: str = Form(...),
    phone: str = Form(...),
    address: str = Form(...),
    profession: str = Form(""),
    notes: str = Form("")
):
    uid = str(uuid.uuid4())
    log_with_uid(uid, f"START Registration: name={name}, phone={phone}")
    if not validate_phone(phone):
        log_with_uid(uid, f"FAILED Registration: Invalid phone={phone}")
        return templates.TemplateResponse("register.html", {"request": request, "message": "Phone must be 10 digits.", "year": datetime.utcnow().year})

    if guest_lookup(phone):
        log_with_uid(uid, f"FAILED Registration: Duplicate phone={phone}")
        return templates.TemplateResponse("register.html", {"request": request, "message": "Phone already registered.", "year": datetime.utcnow().year})

    gid = gen_guest_id(name, phone)
    row = dict(
        id=gid,
        name=name.strip(),
        phone=phone,
        address=address.strip(),
        profession=profession.strip(),
        notes=notes.strip(),
        added="no",
        created=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        plus_one="no"
    )
    guests = lock_and_read()
    guests.append(row)
    lock_and_write(guests)
    log_with_uid(uid, f"SUCCESS Registration: {row}")
    return templates.TemplateResponse("register.html", {"request": request, "message": f"Registration successful! Your ID: {gid}", "year": datetime.utcnow().year})

# ---- 2. Download QR Code (Public) ----
@app.get("/download_qr", response_class=HTMLResponse)
def download_qr_form(request: Request):
    return templates.TemplateResponse("download_qr.html", {"request": request, "qr_image": None, "guest": None, "error": None, "year": datetime.utcnow().year})

@app.post("/download_qr", response_class=HTMLResponse)
def download_qr_submit(request: Request, identifier: str = Form(...)):
    uid = str(uuid.uuid4())
    g = guest_lookup(identifier)
    if not g:
        log_with_uid(uid, f"FAILED QR Download: Guest not found identifier={identifier}")
        return templates.TemplateResponse("download_qr.html", {"request": request, "qr_image": None, "guest": None, "error": "Guest not found.", "year": datetime.utcnow().year})
    qr_image = qr_b64(g["id"])
    log_with_uid(uid, f"SUCCESS QR Generated: id={g['id']}, phone={g['phone']}")
    return templates.TemplateResponse("download_qr.html", {"request": request, "qr_image": qr_image, "guest": g, "error": None, "year": datetime.utcnow().year})

# ---- 3. Welcome Page (Admin Protected) ----
@app.get("/welcome", response_class=HTMLResponse)
def welcome_form(request: Request, auth: bool = Depends(admin_required)):
    return templates.TemplateResponse("welcome.html", {"request": request, "guest": None, "error": None, "already_checked_in": False, "year": datetime.utcnow().year})

@app.post("/welcome", response_class=HTMLResponse)
def welcome_submit(request: Request, lookup: str = Form(...), auth: bool = Depends(admin_required)):
    uid = str(uuid.uuid4())
    log_with_uid(uid, f"START Welcome Submit: lookup={lookup}")
    g = guest_lookup(lookup)
    if not g:
        log_with_uid(uid, f"FAILED Welcome: Guest not found for lookup={lookup}")
        return templates.TemplateResponse("welcome.html", {"request": request, "guest": None, "error": "Guest not found.", "already_checked_in": False, "year": datetime.utcnow().year})

    if g["added"] == "yes":
        log_with_uid(uid, f"WELCOME: Already checked in: id={g['id']}, phone={g['phone']}")
        return templates.TemplateResponse(
            "welcome.html",
            {
                "request": request,
                "guest": g,
                "error": None,
                "already_checked_in": True,
                "year": datetime.utcnow().year
            }
        )
    else:
        mark_checked_in(g["id"])
        log_with_uid(uid, f"SUCCESS Check-in: id={g['id']} name={g['name']}")
        g = guest_lookup(g["id"])
        return templates.TemplateResponse(
            "welcome.html",
            {
                "request": request,
                "guest": g,
                "error": None,
                "already_checked_in": False,
                "year": datetime.utcnow().year
            }
        )

# ---- 4. Add Plus One (Admin Protected) ----
@app.post("/add_plus_one", response_class=HTMLResponse)
def add_plus_one(request: Request, lookup: str = Form(...), auth: bool = Depends(admin_required)):
    uid = str(uuid.uuid4())
    log_with_uid(uid, f"START Add Plus One: lookup={lookup}")
    guests = lock_and_read()
    found = False
    updated = False
    for g in guests:
        if g["id"] == lookup:
            if g["added"] == "yes" and g["plus_one"] == "no":
                g["plus_one"] = "yes"
                found = True
                updated = True
                log_with_uid(uid, f"SUCCESS Plus One Added: id={g['id']}")
            elif g["plus_one"] == "yes":
                found = True
                log_with_uid(uid, f"IGNORED Plus One: Already added id={g['id']}")
    if updated:
        lock_and_write(guests)
    if found:
        guest = guest_lookup(lookup)
        return templates.TemplateResponse("welcome.html", {"request": request, "guest": guest, "error": None, "already_checked_in": True, "year": datetime.utcnow().year})
    else:
        log_with_uid(uid, f"FAILED Plus One: Not eligible id/lookup={lookup}")
        return templates.TemplateResponse("welcome.html", {"request": request, "guest": None, "error": "Guest not found, not checked in, or plus one already added.", "already_checked_in": False, "year": datetime.utcnow().year})

# ---- 5. Guest List / Dashboard (Admin Protected) ----
@app.get("/guest_list", response_class=HTMLResponse)
def guest_list(request: Request, auth: bool = Depends(admin_required)):
    guests = lock_and_read()
    stats = get_dashboard_stats(guests)
    return templates.TemplateResponse("guest_list.html", {"request": request, "guests": guests, "stats": stats, "year": datetime.utcnow().year})

@app.get("/guest_list.csv")
def guest_list_csv(auth: bool = Depends(admin_required)):
    if not os.path.exists(CSV_PATH):
        return Response("No guests registered yet.", media_type="text/plain")
    backup_csv()
    return FileResponse(CSV_PATH, filename="guest_list.csv")

# ---- 6. Serve QR as Image (optional) ----
@app.get("/qr/{gid}")
def qr_direct(gid: str):
    buf = BytesIO()
    img = qrcode.make(gid)
    img.save(buf, format="PNG")
    buf.seek(0)
    return Response(content=buf.read(), media_type="image/png")

@app.exception_handler(Exception)
async def custom_exception_handler(request: Request, exc: Exception):
    logging.error(f"Error: {exc}")
    return Response("An unexpected error occurred.", status_code=500)

# === End of File ===
