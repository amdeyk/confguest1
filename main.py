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
from PIL import Image, ImageDraw, ImageFont

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
        return False
    return True

def admin_required(kotak_admin_session: str = Cookie(None)):
    """Dependency for admin-only routes"""
    if not check_admin_auth(kotak_admin_session):
        raise HTTPException(status_code=403, detail="Admin authentication required")
    return True

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

def create_conference_badge(guest_data):
    """Create a beautiful, properly sized conference badge"""
    # Badge dimensions - optimized for readability
    width, height = 600, 850

    # Create new image with white background
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)

    # Professional color scheme
    kotak_green = (34, 197, 94)
    dark_text = (31, 41, 55)
    medium_gray = (75, 85, 99)
    light_gray = (156, 163, 175)
    blue_accent = (59, 130, 246)

    # Load fonts with proper sizing
    try:
        # Much larger fonts for better readability
        header_font = ImageFont.truetype("arial.ttf", 36)
        name_font = ImageFont.truetype("arial.ttf", 56)
        detail_font = ImageFont.truetype("arial.ttf", 28)
        prof_font = ImageFont.truetype("arial.ttf", 24)
        footer_font = ImageFont.truetype("arial.ttf", 20)
    except Exception:
        # Fallback fonts
        header_font = ImageFont.load_default()
        name_font = ImageFont.load_default()
        detail_font = ImageFont.load_default()
        prof_font = ImageFont.load_default()
        footer_font = ImageFont.load_default()

    # Header section - larger and more prominent
    header_height = 100
    draw.rectangle([0, 0, width, header_height], fill=kotak_green)

    # Header text - bigger and clearer
    draw.text((width // 2, header_height // 2), "🎫 KOTAK CONFERENCE BADGE",
              font=header_font, fill="white", anchor="mm")

    # Content area starts after header
    content_y = header_height + 60

    # QR Code - larger and more prominent
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=3,
    )
    qr.add_data(guest_data["id"])
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white")

    # Much larger QR code
    qr_size = 220
    qr_img = qr_img.resize((qr_size, qr_size))

    # Center QR code with nice border
    qr_x = (width - qr_size) // 2
    qr_y = content_y

    # Elegant border around QR
    border_size = 15
    draw.rectangle([qr_x - border_size, qr_y - border_size,
                   qr_x + qr_size + border_size, qr_y + qr_size + border_size],
                   fill=(248, 250, 252), outline=(229, 231, 235), width=3)

    img.paste(qr_img, (qr_x, qr_y))

    # Guest name - much larger and more prominent
    name_y = qr_y + qr_size + 60
    draw.text((width // 2, name_y), guest_data["name"],
              font=name_font, fill=kotak_green, anchor="mt")

    # Guest details with better spacing
    details_y = name_y + 80

    # ID with better formatting
    draw.text((width // 2, details_y), f"ID: {guest_data['id']}",
              font=detail_font, fill=medium_gray, anchor="mt")

    # Phone with better spacing
    draw.text((width // 2, details_y + 45), f"Phone: {guest_data['phone']}",
              font=detail_font, fill=medium_gray, anchor="mt")

    # Profession badge - bigger and more readable
    if guest_data.get("profession") and guest_data["profession"].strip():
        prof_y = details_y + 110
        prof_text = guest_data["profession"]

        # Larger profession badge
        prof_bbox = draw.textbbox((0, 0), prof_text, font=prof_font)
        prof_width = prof_bbox[2] - prof_bbox[0] + 40
        prof_height = prof_bbox[3] - prof_bbox[1] + 20
        prof_x = (width - prof_width) // 2

        # Nice rounded badge
        draw.rounded_rectangle([prof_x, prof_y, prof_x + prof_width, prof_y + prof_height],
                             radius=prof_height // 2, fill=blue_accent)

        draw.text((width // 2, prof_y + prof_height // 2), prof_text,
                  font=prof_font, fill="white", anchor="mm")

    # Footer with proper spacing
    footer_y = height - 50
    draw.text((width // 2, footer_y), "Kotak Conference 2025 • Present at Registration",
              font=footer_font, fill=light_gray, anchor="mm")

    buf = BytesIO()
    img.save(buf, format="PNG", quality=95, optimize=True)
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
    return templates.TemplateResponse("admin_login.html", {"request": request, "error": None, "year": datetime.now().year})

@app.post("/admin", response_class=HTMLResponse)
def admin_login_submit(request: Request, password: str = Form(...)):
    if password == ADMIN_PASSWORD:
        response = RedirectResponse("/admin/dashboard", status_code=302)
        response.set_cookie(SESSION_COOKIE_NAME, "authenticated", httponly=True, max_age=86400)  # 24 hours
        return response
    else:
        return templates.TemplateResponse("admin_login.html", {"request": request, "error": "Invalid password. Please try again.", "year": datetime.now().year})

@app.get("/admin/logout")
def admin_logout():
    response = RedirectResponse("/admin", status_code=302)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response

@app.get("/admin/dashboard", response_class=HTMLResponse)
def admin_dashboard(request: Request, auth: bool = Depends(admin_required)):
    return templates.TemplateResponse("admin_dashboard.html", {"request": request, "year": datetime.now().year})

# ---- 1. Registration (Public) ----
@app.get("/register", response_class=HTMLResponse)
def register_form(request: Request):
    return templates.TemplateResponse("register.html", {"request": request, "message": None, "year": datetime.now().year})

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
        return templates.TemplateResponse("register.html", {"request": request, "message": "Phone must be 10 digits.", "year": datetime.now().year})

    if guest_lookup(phone):
        log_with_uid(uid, f"FAILED Registration: Duplicate phone={phone}")
        return templates.TemplateResponse("register.html", {"request": request, "message": "Phone already registered.", "year": datetime.now().year})

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
    return templates.TemplateResponse("register.html", {"request": request, "message": f"Registration successful! Your ID: {gid}", "year": datetime.now().year})

# ---- 2. Download QR Code (Public) ----
@app.get("/download_qr", response_class=HTMLResponse)
def download_qr_form(request: Request):
    return templates.TemplateResponse("download_qr.html", {"request": request, "qr_image": None, "guest": None, "error": None, "year": datetime.now().year})

@app.post("/download_qr", response_class=HTMLResponse)
def download_qr_submit(request: Request, identifier: str = Form(...)):
    uid = str(uuid.uuid4())
    g = guest_lookup(identifier)
    if not g:
        log_with_uid(uid, f"FAILED QR Download: Guest not found identifier={identifier}")
        return templates.TemplateResponse("download_qr.html", {"request": request, "qr_image": None, "guest": None, "error": "Guest not found.", "year": datetime.now().year})
    qr_image = create_conference_badge(g)
    log_with_uid(uid, f"SUCCESS QR Generated: id={g['id']}, phone={g['phone']}")
    return templates.TemplateResponse("download_qr.html", {"request": request, "qr_image": qr_image, "guest": g, "error": None, "year": datetime.now().year})

# ---- 3. Welcome Page (Admin Protected) ----
@app.get("/welcome")
def welcome_form(request: Request, kotak_admin_session: str = Cookie(None)):
    try:
        if not check_admin_auth(kotak_admin_session):
            return RedirectResponse("/admin")
        return templates.TemplateResponse(
            "welcome.html",
            {
                "request": request,
                "guest": None,
                "error": None,
                "already_checked_in": False,
                "year": datetime.now().year,
            },
        )
    except Exception as e:
        logging.error(f"Error in welcome_form: {e}")
        return RedirectResponse("/admin")

@app.post("/welcome")
def welcome_submit(request: Request, lookup: str = Form(...), kotak_admin_session: str = Cookie(None)):
    try:
        if not check_admin_auth(kotak_admin_session):
            return RedirectResponse("/admin")

        uid = str(uuid.uuid4())
        log_with_uid(uid, f"START Welcome Submit: lookup={lookup}")
        g = guest_lookup(lookup)
        if not g:
            log_with_uid(uid, f"FAILED Welcome: Guest not found for lookup={lookup}")
            return templates.TemplateResponse(
                "welcome.html",
                {
                    "request": request,
                    "guest": None,
                    "error": "Guest not found.",
                    "already_checked_in": False,
                    "year": datetime.now().year,
                },
            )

        if g["added"] == "yes":
            log_with_uid(uid, f"WELCOME: Already checked in: id={g['id']}, phone={g['phone']}")
            return templates.TemplateResponse(
                "welcome.html",
                {
                    "request": request,
                    "guest": g,
                    "error": None,
                    "already_checked_in": True,
                    "year": datetime.now().year,
                },
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
                    "year": datetime.now().year,
                },
            )
    except Exception as e:
        logging.error(f"Error in welcome_submit: {e}")
        return RedirectResponse("/admin")

# ---- 4. Add Plus One (Admin Protected) ----
@app.post("/add_plus_one")
def add_plus_one(request: Request, lookup: str = Form(...), kotak_admin_session: str = Cookie(None)):
    try:
        if not check_admin_auth(kotak_admin_session):
            return RedirectResponse("/admin")

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
            return templates.TemplateResponse(
                "welcome.html",
                {
                    "request": request,
                    "guest": guest,
                    "error": None,
                    "already_checked_in": True,
                    "year": datetime.now().year,
                },
            )
        else:
            log_with_uid(uid, f"FAILED Plus One: Not eligible id/lookup={lookup}")
            return templates.TemplateResponse(
                "welcome.html",
                {
                    "request": request,
                    "guest": None,
                    "error": "Guest not found, not checked in, or plus one already added.",
                    "already_checked_in": False,
                    "year": datetime.now().year,
                },
            )
    except Exception as e:
        logging.error(f"Error in add_plus_one: {e}")
        return RedirectResponse("/admin")

# ---- 5. Guest List / Dashboard (Admin Protected) ----
@app.get("/guest_list")
def guest_list(request: Request, kotak_admin_session: str = Cookie(None)):
    try:
        if not check_admin_auth(kotak_admin_session):
            return RedirectResponse("/admin")

        guests = lock_and_read()
        stats = get_dashboard_stats(guests)
        return templates.TemplateResponse(
            "guest_list.html",
            {
                "request": request,
                "guests": guests,
                "stats": stats,
                "year": datetime.now().year,
            },
        )
    except Exception as e:
        logging.error(f"Error in guest_list: {e}")
        return RedirectResponse("/admin")

@app.get("/guest_list.csv")
def guest_list_csv(kotak_admin_session: str = Cookie(None)):
    try:
        if not check_admin_auth(kotak_admin_session):
            raise HTTPException(status_code=403, detail="Admin authentication required")

        if not os.path.exists(CSV_PATH):
            return Response("No guests registered yet.", media_type="text/plain")
        backup_csv()
        return FileResponse(CSV_PATH, filename="guest_list.csv")
    except Exception as e:
        logging.error(f"Error in guest_list_csv: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

# ---- 6. Serve QR as Image (optional) ----
@app.get("/qr/{gid}")
def qr_direct(gid: str):
    try:
        buf = BytesIO()
        img = qrcode.make(gid)
        img.save(buf, format="PNG")
        buf.seek(0)
        return Response(content=buf.read(), media_type="image/png")
    except Exception as e:
        logging.error(f"Error in qr_direct: {e}")
        raise HTTPException(status_code=500, detail="Error generating QR code")

# Custom exception handler for 403 errors
@app.exception_handler(403)
async def forbidden_exception_handler(request: Request, exc: HTTPException):
    return RedirectResponse("/admin")

@app.exception_handler(Exception)
async def custom_exception_handler(request: Request, exc: Exception):
    logging.error(f"Global Error: {exc}")
    return templates.TemplateResponse(
        "error.html",
        {"request": request, "message": "An unexpected error occurred.", "year": datetime.now().year},
    )

# === End of File ===
