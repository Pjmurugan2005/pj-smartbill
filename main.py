import os
import re
import csv
import io
import html
import secrets
import logging
import json
import urllib.request
from datetime import datetime, timedelta
from uuid import uuid4
from typing import List, Optional

import razorpay
from fastapi import FastAPI, Depends, HTTPException, Request, Response, UploadFile, File, Form, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import func, or_, desc
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

import database as db
import auth

db.init_db()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("PJSmartBill")

UPLOAD_DIR = os.path.join("static", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Remote IP resolution behind reverse proxy (Render / Cloudflare)
def get_client_ip(request: Request):
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return get_remote_address(request)

limiter = Limiter(key_func=get_client_ip)
app = FastAPI(
    title="PJ SmartBill POS Multi-Enterprise",
    docs_url=None,       # Disabled in production for security
    redoc_url=None,
    openapi_url=None
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# SECRETS FROM ENVIRONMENT ONLY
SMTP_EMAIL = os.getenv("SMTP_EMAIL")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET")

if not all([SMTP_EMAIL, SMTP_PASSWORD, RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET]):
    logger.warning("WARNING: Production credentials (SMTP / Razorpay) not fully set in ENV!")

rzp_client = razorpay.Client(auth=(RAZORPAY_KEY_ID or "placeholder", RAZORPAY_KEY_SECRET or "placeholder"))

@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response

def get_db():
    session = db.SessionLocal()
    try:
        yield session
    finally:
        session.close()

def get_current_user(request: Request, session: Session = Depends(get_db)):
    token = request.cookies.get("access_token")
    if not token:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1]
    if not token:
        return None
    data = auth.decode_token(token)
    if not data or "sub" not in data:
        return None
    try:
        user_id = int(data["sub"])
    except (ValueError, TypeError):
        return None
    return session.query(db.User).filter(db.User.id == user_id).first()

def require_active_subscription(user: db.User = Depends(get_current_user)):
    if not user or not user.is_verified:
        raise HTTPException(status_code=401, detail="Authentication required")
    now = datetime.utcnow()
    if not user.is_subscribed or (user.subscription_end and now > user.subscription_end):
        raise HTTPException(status_code=403, detail="Active subscription required to perform this action.")
    return user

def send_real_email_otp(to_email: str, otp: str, shop_name: str) -> bool:
    resend_api_key = os.getenv("RESEND_API_KEY", "").strip()

    if not resend_api_key:
        logger.error("[Resend Missing] RESEND_API_KEY environment variable is not set!")
        return False

    safe_shop_name = html.escape(shop_name)
    html_body = f"""
    <div style="font-family: Arial, sans-serif; max-width: 500px; margin: auto; padding: 25px; border: 1px solid #e2e8f0; border-radius: 16px;">
      <h2 style="color: #2563eb; text-align: center;">🧾 PJ SmartBill POS</h2>
      <p>Your verification code for <b>{safe_shop_name}</b> is:</p>
      <div style="text-align: center; margin: 20px 0;">
        <span style="font-size: 32px; font-weight: 900; letter-spacing: 6px; color: #1e3a8a; background: #eff6ff; padding: 10px 24px; border-radius: 12px; border: 2px dashed #3b82f6;">{otp}</span>
      </div>
      <p style="font-size: 11px; color: #64748b; text-align: center;">This code expires in 10 minutes.</p>
    </div>
    """

    payload = {
        "from": "PJ SmartBill <onboarding@resend.dev>",
        "to": [to_email],
        "subject": f"{otp} is your PJ SmartBill Verification Code",
        "html": html_body
    }

    try:
        req = urllib.request.Request(
            "https://api.resend.com/emails",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {resend_api_key}",
                "Content-Type": "application/json",
                "User-Agent": "PJSmartBill/1.0"
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status in [200, 201]:
                logger.info(f"[Email Success] OTP sent via HTTP API to {to_email}")
                return True
            return False
    except Exception as e:
        logger.error(f"[Resend API Error]: {e}")
        return False

# ----------------- VALIDATED SCHEMAS -----------------
class RegisterInitiatePayload(BaseModel):
    shop_name: str = Field(..., min_length=2, max_length=150)
    email: str = Field(..., min_length=5, max_length=120)
    phone: str = Field(..., min_length=10, max_length=15)
    password: str = Field(..., min_length=8, max_length=128)
    confirm_password: str

class VerifyRegisterOTPPayload(BaseModel):
    email: str
    otp: str = Field(..., min_length=6, max_length=6)

class LoginPayload(BaseModel):
    identifier: str
    password: str

class StoreSetupPayload(BaseModel):
    address: str = Field(..., max_length=300)
    store_type: str

class CartItem(BaseModel):
    product_id: int
    quantity: int = Field(..., gt=0, le=5000)

class BillPayload(BaseModel):
    customer_name: Optional[str] = Field("Walk-in", max_length=100)
    customer_phone: Optional[str] = Field("", max_length=20)
    customer_address: Optional[str] = Field("", max_length=250)
    customer_gstin: Optional[str] = Field("", max_length=30)
    discount: float = Field(0.0, ge=0.0)
    payment_mode: str = Field("Cash", max_length=20)
    items: List[CartItem]

class VerifyPaymentPayload(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str

class RechargeCreate(BaseModel):
    mobile_number: str = Field(..., min_length=10, max_length=15)
    operator: str = Field(..., max_length=30)
    amount: float = Field(..., gt=0)
    payment_mode: str = "Cash"

class RepairCreate(BaseModel):
    customer_name: str = Field(..., max_length=100)
    customer_phone: str = Field(..., max_length=20)
    device_model: str = Field(..., max_length=100)
    problem_desc: str = Field(..., max_length=500)
    estimated_cost: float = Field(0.0, ge=0.0)
    advance_paid: float = Field(0.0, ge=0.0)

class DamageCreate(BaseModel):
    category: str
    item_name: str = Field(..., max_length=200)
    quantity: int = Field(1, gt=0)
    loss_amount: float = Field(..., ge=0.0)
    reason: str = "Physical Damage"

# ----------------- ROUTES -----------------
@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"rzp_key": RAZORPAY_KEY_ID or ""}
    )

@app.post("/api/auth/register-initiate")
@limiter.limit("5/minute")
def register_initiate(request: Request, data: RegisterInitiatePayload, session: Session = Depends(get_db)):
    if data.password != data.confirm_password:
        raise HTTPException(status_code=400, detail="Passwords do not match!")

    email = data.email.strip().lower()
    clean_phone = ''.join(filter(str.isdigit, data.phone))[-10:]
    if len(clean_phone) != 10:
        raise HTTPException(status_code=400, detail="Invalid 10-digit phone number!")

    existing = session.query(db.User).filter(or_(db.User.email == email, db.User.phone == clean_phone)).first()
    if existing and existing.is_verified:
        raise HTTPException(status_code=400, detail="Email or Phone already registered!")

    # Cryptographically secure random 6-digit OTP
    otp = f"{secrets.randbelow(900000) + 100000}"
    expiry = datetime.utcnow() + timedelta(minutes=10)
    p_hash = auth.hash_password(data.password)

    if existing and not existing.is_verified:
        existing.shop_name = data.shop_name.strip()
        existing.email = email
        existing.phone = clean_phone
        existing.password_hash = p_hash
        existing.otp = otp
        existing.otp_expiry = expiry
        existing.otp_attempts = 0
    else:
        new_user = db.User(
            shop_name=data.shop_name.strip(),
            email=email,
            phone=clean_phone,
            password_hash=p_hash,
            otp=otp,
            otp_expiry=expiry,
            otp_attempts=0
        )
        session.add(new_user)
    session.commit()

    if not send_real_email_otp(email, otp, data.shop_name):
        raise HTTPException(status_code=500, detail="Failed to dispatch verification email. Verify SMTP setup.")

    return {"message": f"Verification code sent to {email}!", "email": email}

@app.post("/api/auth/register-verify")
@limiter.limit("5/minute")
def register_verify(request: Request, data: VerifyRegisterOTPPayload, response: Response, session: Session = Depends(get_db)):
    email = data.email.strip().lower()
    user = session.query(db.User).filter(db.User.email == email).first()
    if not user:
        raise HTTPException(status_code=404, detail="User registration record not found")

    if user.otp_attempts >= 5:
        user.otp = None
        session.commit()
        raise HTTPException(status_code=429, detail="Too many invalid attempts. Request a new OTP.")

    if not user.otp_expiry or datetime.utcnow() > user.otp_expiry:
        raise HTTPException(status_code=400, detail="OTP has expired. Please request a new one.")

    if not secrets.compare_digest(user.otp or "", data.otp.strip()):
        user.otp_attempts += 1
        session.commit()
        raise HTTPException(status_code=400, detail="Invalid verification code!")

    user.is_verified = True
    user.otp = None
    user.otp_attempts = 0
    session.commit()

    token = auth.create_access_token({"sub": str(user.id)})
    is_prod = not bool(os.getenv("LOCAL_DEV", False))
    response.set_cookie(key="access_token", value=token, httponly=True, max_age=86400 * 30, secure=is_prod, samesite="lax")
    return {"shop_name": user.shop_name, "token": token, "message": "Registered successfully!"}

@app.post("/api/auth/login")
@limiter.limit("10/minute")
def login(request: Request, data: LoginPayload, response: Response, session: Session = Depends(get_db)):
    ident = data.identifier.strip().lower()
    clean_phone = ''.join(filter(str.isdigit, ident))[-10:]
    user = session.query(db.User).filter(or_(db.User.email == ident, db.User.phone == clean_phone)).first()
    if not user or not user.is_verified or not auth.verify_password(data.password, user.password_hash):
        raise HTTPException(status_code=400, detail="Invalid Email/Phone or Password!")

    token = auth.create_access_token({"sub": str(user.id)})
    is_prod = not bool(os.getenv("LOCAL_DEV", False))
    response.set_cookie(key="access_token", value=token, httponly=True, max_age=86400 * 30, secure=is_prod, samesite="lax")
    return {"shop_name": user.shop_name, "token": token}

@app.post("/api/logout")
def logout(response: Response):
    response.delete_cookie("access_token")
    return {"message": "Logged out successfully"}

@app.get("/api/me")
def check_me(user: Optional[db.User] = Depends(get_current_user)):
    if not user or not user.is_verified:
        raise HTTPException(status_code=401, detail="Not logged in")

    is_active = False
    days_left = 0
    if user.is_subscribed:
        if user.subscription_end:
            now = datetime.utcnow()
            if now <= user.subscription_end:
                is_active = True
                days_left = max(0, (user.subscription_end - now).days + 1)
        else:
            is_active = True
            days_left = 30

    return {
        "shop_name": user.shop_name,
        "email": user.email,
        "phone": user.phone,
        "address": user.address or "",
        "store_type": user.store_type or "MOBILE",
        "profile_image": getattr(user, "profile_image", "/static/default-shop.png"),
        "is_subscribed": is_active,
        "subscription_plan": user.subscription_plan or "TRIAL",
        "days_left": days_left,
        "subscription_end": user.subscription_end.strftime("%d %b %Y") if user.subscription_end else "Active"
    }

@app.post("/api/user/store-setup")
def setup_store(data: StoreSetupPayload, user: db.User = Depends(get_current_user), session: Session = Depends(get_db)):
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if data.store_type not in ["DEPARTMENT", "MOBILE"]:
        raise HTTPException(status_code=400, detail="Invalid store category")

    user.address = data.address.strip()
    user.store_type = data.store_type
    session.commit()
    return {"status": "success", "store_type": user.store_type}

# --- RAZORPAY SUBSCRIPTION ORDER & REUSE-SAFE VERIFICATION ---
@app.post("/api/subscription/create-order")
def create_subscription_order(user: db.User = Depends(get_current_user), session: Session = Depends(get_db)):
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    order_payload = {
        "amount": 100,  # ₹1.00
        "currency": "INR",
        "receipt": f"sub_{user.id}_{int(datetime.utcnow().timestamp())}",
        "notes": {"user_id": str(user.id)}
    }
    try:
        order = rzp_client.order.create(data=order_payload)
        user.razorpay_order_id = order["id"]
        session.commit()
        return {
            "order_id": order["id"],
            "amount": order["amount"],
            "currency": order["currency"],
            "key": RAZORPAY_KEY_ID
        }
    except Exception as e:
        logger.error(f"Razorpay order creation failure: {e}")
        raise HTTPException(status_code=500, detail="Payment gateway communication error")

@app.post("/api/subscription/verify-payment")
def verify_payment(
    data: VerifyPaymentPayload,
    user: db.User = Depends(get_current_user),
    session: Session = Depends(get_db)
):
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    # Order ownership check
    if not user.razorpay_order_id or user.razorpay_order_id != data.razorpay_order_id:
        raise HTTPException(status_code=400, detail="Order ID mismatch or invalid order")

    # Unique payment_id check to prevent replay attacks
    existing_pay = session.query(db.User).filter(db.User.razorpay_subscription_id == data.razorpay_payment_id).first()
    if existing_pay:
        raise HTTPException(status_code=400, detail="This payment ID has already been utilized")

    try:
        rzp_client.utility.verify_payment_signature({
            'razorpay_order_id': data.razorpay_order_id,
            'razorpay_payment_id': data.razorpay_payment_id,
            'razorpay_signature': data.razorpay_signature
        })
    except razorpay.errors.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Signature verification rejected!")

    user.is_subscribed = True
    user.subscription_plan = "TRIAL_1RS"
    user.subscription_end = datetime.utcnow() + timedelta(days=30)
    user.razorpay_subscription_id = data.razorpay_payment_id
    session.commit()

    return {
        "status": "success",
        "message": "Payment verified! Store unlocked for 30 days.",
        "subscription_end": user.subscription_end.strftime("%d %b %Y")
    }

# --- PRODUCTS (Subscription-Protected & Anti-Hang) ---
@app.get("/api/products")
def get_products(
    search: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = 40,
    user: db.User = Depends(require_active_subscription),
    session: Session = Depends(get_db)
):
    query = session.query(db.Product).filter(db.Product.user_id == user.id)
    if category and category != "All":
        query = query.filter(db.Product.category == category)
    if search:
        st = f"%{search.strip()}%"
        query = query.filter(
            or_(
                db.Product.barcode == search.strip(),
                db.Product.imei_serial == search.strip(),
                db.Product.name.ilike(st)
            )
        )

    prods = query.order_by(desc(db.Product.id)).limit(min(limit, 100)).all()
    return [{
        "id": p.id,
        "barcode": p.barcode or "-",
        "imei_serial": p.imei_serial or "-",
        "name": p.name,
        "category": p.category or "General",
        "price": p.price,
        "stock": p.stock,
        "warranty_months": p.warranty_months or 0,
        "image_url": p.image_url if (p.image_url.startswith("/static/") or p.image_url.startswith("https://")) else "/static/default-item.png"
    } for p in prods]

@app.post("/api/products/create")
def add_product_with_image(
    barcode: Optional[str] = Form(""),
    imei_serial: Optional[str] = Form(""),
    name: str = Form(...),
    category: Optional[str] = Form("Mobile Cases"),
    price: float = Form(...),
    stock: int = Form(...),
    warranty_months: Optional[int] = Form(0),
    image: Optional[UploadFile] = File(None),
    user: db.User = Depends(require_active_subscription),
    session: Session = Depends(get_db)
):
    image_url = "/static/default-item.png"
    if image and image.filename:
        ext = os.path.splitext(image.filename)[1].lower()
        if ext not in [".jpg", ".jpeg", ".png", ".webp"]:
            raise HTTPException(status_code=400, detail="Allowed image formats: JPG, PNG, WEBP")
        
        file_bytes = image.file.read(2 * 1024 * 1024 + 1)
        if len(file_bytes) > 2 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="Image size exceeds 2MB limit")
        
        filename = f"prod_{user.id}_{uuid4().hex[:10]}{ext}"
        filepath = os.path.join(UPLOAD_DIR, filename)
        with open(filepath, "wb") as f:
            f.write(file_bytes)
        image_url = f"/{filepath}"

    prod = db.Product(
        user_id=user.id,
        barcode=barcode.strip() if barcode else None,
        imei_serial=imei_serial.strip() if imei_serial else None,
        name=name.strip(),
        category=category.strip(),
        price=max(0.0, price),
        stock=stock,
        warranty_months=max(0, warranty_months or 0),
        image_url=image_url
    )
    session.add(prod)
    session.commit()
    return {"message": "Product saved successfully!"}

@app.delete("/api/products/{prod_id}")
def delete_product(prod_id: int, user: db.User = Depends(require_active_subscription), session: Session = Depends(get_db)):
    prod = session.query(db.Product).filter(db.Product.id == prod_id, db.Product.user_id == user.id).first()
    if prod:
        session.delete(prod)
        session.commit()
    return {"message": "Deleted"}

@app.post("/api/products/import-csv")
async def import_products_csv(
    file: UploadFile = File(...),
    user: db.User = Depends(require_active_subscription),
    session: Session = Depends(get_db)
):
    if not file.filename.endswith('.csv'):
        raise HTTPException(status_code=400, detail="Only CSV files are supported")

    content = await file.read(5 * 1024 * 1024 + 1)  # 5MB Max
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="CSV file exceeds 5MB limit")

    decoded = content.decode('utf-8', errors='ignore')
    reader = csv.DictReader(io.StringIO(decoded))

    count = 0
    bulk = []
    for row in reader:
        pname = row.get("name") or row.get("product_name")
        if not pname:
            continue
        try:
            price = float(row.get("price", 0.0) or 0.0)
            stock = int(row.get("stock", 50) or 50)
            warranty = int(row.get("warranty_months", 0) or 0)
        except (ValueError, TypeError):
            continue

        raw_img = row.get("image_url", "/static/default-item.png").strip()
        safe_img = raw_img if (raw_img.startswith("/static/") or raw_img.startswith("https://")) else "/static/default-item.png"

        new_p = db.Product(
            user_id=user.id,
            barcode=row.get("barcode", "").strip() or str(uuid4().int)[:12],
            imei_serial=row.get("imei_serial", "-").strip(),
            name=pname.strip()[:200],
            category=row.get("category", "General").strip()[:50],
            price=max(0.0, price),
            stock=stock,
            warranty_months=max(0, warranty),
            image_url=safe_img
        )
        bulk.append(new_p)
        count += 1
        if len(bulk) >= 1000:
            session.bulk_save_objects(bulk)
            session.commit()
            bulk = []

    if bulk:
        session.bulk_save_objects(bulk)
        session.commit()

    return {"status": "success", "message": f"Successfully imported {count} items!"}

# --- ATOMIC POS BILLING CHECKOUT ---
@app.post("/api/checkout")
def checkout(payload: BillPayload, user: db.User = Depends(require_active_subscription), session: Session = Depends(get_db)):
    if not payload.items:
        raise HTTPException(status_code=400, detail="Cart is empty!")

    subtotal = 0.0
    items_to_save = []

    for itm in payload.items:
        p = session.query(db.Product).filter(db.Product.id == itm.product_id, db.Product.user_id == user.id).first()
        if not p:
            raise HTTPException(status_code=404, detail=f"Product SKU ID {itm.product_id} no longer exists!")
        
        amt = round(p.price * itm.quantity, 2)
        subtotal += amt
        p.stock -= itm.quantity
        items_to_save.append({
            "barcode": p.barcode or "-",
            "imei_serial": p.imei_serial or "-",
            "warranty_months": p.warranty_months or 0,
            "name": p.name,
            "price": p.price,
            "qty": itm.quantity,
            "amt": amt
        })

    if payload.discount > subtotal:
        raise HTTPException(status_code=400, detail="Discount cannot exceed total subtotal amount!")

    is_mobile_shop = (user.store_type == "MOBILE")
    tax_amount = round(subtotal - (subtotal / 1.18), 2) if is_mobile_shop else 0.0
    total = max(0.0, round(subtotal - payload.discount, 2))
    prefix = "MOB" if is_mobile_shop else "POS"
    invoice_no = f"{prefix}-{datetime.utcnow().strftime('%y%m%d')}-{uuid4().hex[:6].upper()}"

    # Atomic single-transaction save
    try:
        bill = db.Bill(
            user_id=user.id,
            invoice_no=invoice_no,
            customer_name=payload.customer_name or "Walk-in Customer",
            customer_phone=payload.customer_phone or "N/A",
            customer_address=payload.customer_address or "",
            customer_gstin=payload.customer_gstin or "",
            subtotal=subtotal,
            tax_amount=tax_amount,
            discount=payload.discount,
            total=total,
            payment_mode=payload.payment_mode,
            payment_status="PAID",
            invoice_type="A4" if is_mobile_shop else "THERMAL"
        )
        session.add(bill)
        session.flush()

        for itm in items_to_save:
            session.add(db.BillItem(
                bill_id=bill.id,
                barcode=itm["barcode"],
                imei_serial=itm["imei_serial"],
                product_name=itm["name"],
                price=itm["price"],
                quantity=itm["qty"],
                amount=itm["amt"]
            ))
        session.commit()
    except Exception as e:
        session.rollback()
        logger.error(f"Transaction checkout failure: {e}")
        raise HTTPException(status_code=500, detail="Checkout processing failed.")

    return {
        "invoice_no": invoice_no,
        "date": (bill.created_at + timedelta(hours=5, minutes=30)).strftime("%d-%m-%Y %I:%M %p"), # IST Time
        "customer_name": bill.customer_name,
        "customer_phone": bill.customer_phone,
        "customer_address": bill.customer_address,
        "customer_gstin": bill.customer_gstin,
        "subtotal": subtotal,
        "tax_amount": tax_amount,
        "discount": bill.discount,
        "total": total,
        "payment_mode": payload.payment_mode,
        "shop_name": user.shop_name,
        "shop_address": user.address or "Tamil Nadu, India",
        "phone": user.phone or "",
        "store_type": user.store_type,
        "invoice_type": bill.invoice_type,
        "items": items_to_save
    }

# --- EXTENDED MODULES (Recharges, Repairs, Damages, Reports) ---
@app.get("/api/recharges")
def get_recharges(user: db.User = Depends(require_active_subscription), session: Session = Depends(get_db)):
    # IST date window calculation
    ist_today = (datetime.utcnow() + timedelta(hours=5, minutes=30)).date()
    today_total = session.query(func.sum(db.RechargeEntry.amount)).filter(
        db.RechargeEntry.user_id == user.id,
        func.date(func.datetime(db.RechargeEntry.created_at, '+330 minutes')) == ist_today
    ).scalar() or 0.0

    recs = session.query(db.RechargeEntry).filter(db.RechargeEntry.user_id == user.id).order_by(desc(db.RechargeEntry.id)).limit(20).all()
    return {
        "today_total": today_total,
        "history": [{
            "id": r.id,
            "mobile": r.mobile_number,
            "operator": r.operator,
            "amount": r.amount,
            "time": (r.created_at + timedelta(hours=5, minutes=30)).strftime("%I:%M %p")
        } for r in recs]
    }

@app.post("/api/recharges")
def add_recharge(req: RechargeCreate, user: db.User = Depends(require_active_subscription), session: Session = Depends(get_db)):
    session.add(db.RechargeEntry(
        user_id=user.id,
        mobile_number=req.mobile_number,
        operator=req.operator,
        amount=req.amount,
        payment_mode=req.payment_mode
    ))
    session.commit()
    return {"status": "success"}

@app.get("/api/repairs")
def get_repairs(user: db.User = Depends(require_active_subscription), session: Session = Depends(get_db)):
    jobs = session.query(db.RepairJob).filter(db.RepairJob.user_id == user.id).order_by(desc(db.RepairJob.id)).all()
    return [{
        "job_no": j.job_no,
        "customer": j.customer_name,
        "phone": j.customer_phone,
        "device": j.device_model,
        "problem": j.problem_desc,
        "cost": j.estimated_cost,
        "advance": j.advance_paid,
        "status": j.status
    } for j in jobs]

@app.post("/api/repairs")
def add_repair(req: RepairCreate, user: db.User = Depends(require_active_subscription), session: Session = Depends(get_db)):
    session.add(db.RepairJob(
        job_no=f"JOB-{uuid4().hex[:5].upper()}",
        user_id=user.id,
        customer_name=req.customer_name,
        customer_phone=req.customer_phone,
        device_model=req.device_model,
        problem_desc=req.problem_desc,
        estimated_cost=req.estimated_cost,
        advance_paid=req.advance_paid,
        status="Received"
    ))
    session.commit()
    return {"status": "success"}

@app.get("/api/damages")
def get_damages(user: db.User = Depends(require_active_subscription), session: Session = Depends(get_db)):
    ist_today = (datetime.utcnow() + timedelta(hours=5, minutes=30)).date()
    total_loss = session.query(func.sum(db.DamageEntry.loss_amount)).filter(
        db.DamageEntry.user_id == user.id,
        func.date(func.datetime(db.DamageEntry.created_at, '+330 minutes')) == ist_today
    ).scalar() or 0.0

    items = session.query(db.DamageEntry).filter(db.DamageEntry.user_id == user.id).order_by(desc(db.DamageEntry.id)).limit(20).all()
    return {
        "total_loss": total_loss,
        "items": [{
            "category": d.category,
            "item": d.item_name,
            "qty": d.quantity,
            "loss": d.loss_amount,
            "reason": d.reason
        } for d in items]
    }

@app.post("/api/damages")
def add_damage(req: DamageCreate, user: db.User = Depends(require_active_subscription), session: Session = Depends(get_db)):
    session.add(db.DamageEntry(
        user_id=user.id,
        category=req.category,
        item_name=req.item_name,
        quantity=req.quantity,
        loss_amount=req.loss_amount,
        reason=req.reason
    ))
    session.commit()
    return {"status": "success"}

@app.get("/api/reports")
def get_reports(user: db.User = Depends(require_active_subscription), session: Session = Depends(get_db)):
    ist_today = (datetime.utcnow() + timedelta(hours=5, minutes=30)).date()
    
    sales = session.query(func.sum(db.Bill.total)).filter(
        db.Bill.user_id == user.id,
        func.date(func.datetime(db.Bill.created_at, '+330 minutes')) == ist_today
    ).scalar() or 0.0

    recharges = session.query(func.sum(db.RechargeEntry.amount)).filter(
        db.RechargeEntry.user_id == user.id,
        func.date(func.datetime(db.RechargeEntry.created_at, '+330 minutes')) == ist_today
    ).scalar() or 0.0

    bill_cash = session.query(func.sum(db.Bill.total)).filter(
        db.Bill.user_id == user.id,
        db.Bill.payment_mode == "Cash",
        func.date(func.datetime(db.Bill.created_at, '+330 minutes')) == ist_today
    ).scalar() or 0.0

    rec_cash = session.query(func.sum(db.RechargeEntry.amount)).filter(
        db.RechargeEntry.user_id == user.id,
        db.RechargeEntry.payment_mode == "Cash",
        func.date(func.datetime(db.RechargeEntry.created_at, '+330 minutes')) == ist_today
    ).scalar() or 0.0

    upi_total = session.query(func.sum(db.Bill.total)).filter(
        db.Bill.user_id == user.id,
        db.Bill.payment_mode == "UPI",
        func.date(func.datetime(db.Bill.created_at, '+330 minutes')) == ist_today
    ).scalar() or 0.0

    repairs_count = session.query(func.count(db.RepairJob.id)).filter(db.RepairJob.user_id == user.id).scalar() or 0

    return {
        "sales": sales,
        "recharges": recharges,
        "cash_payments": bill_cash + rec_cash,
        "upi_payments": upi_total,
        "repairs_count": repairs_count
    }

@app.get("/api/dashboard")
def get_dashboard(user: db.User = Depends(require_active_subscription), session: Session = Depends(get_db)):
    total_sales = session.query(func.sum(db.Bill.total)).filter(db.Bill.user_id == user.id).scalar() or 0.0
    total_bills = session.query(func.count(db.Bill.id)).filter(db.Bill.user_id == user.id).scalar() or 0
    low_stock = session.query(func.count(db.Product.id)).filter(db.Product.user_id == user.id, db.Product.stock <= 5).scalar() or 0
    total_products = session.query(func.count(db.Product.id)).filter(db.Product.user_id == user.id).scalar() or 0

    recent_bills = session.query(db.Bill).filter(db.Bill.user_id == user.id).order_by(desc(db.Bill.id)).limit(15).all()
    bills_data = []
    for b in recent_bills:
        items = session.query(db.BillItem).filter(db.BillItem.bill_id == b.id).all()
        bills_data.append({
            "id": b.id,
            "invoice_no": b.invoice_no,
            "date": (b.created_at + timedelta(hours=5, minutes=30)).strftime("%d-%m-%Y %I:%M %p"),
            "customer_name": b.customer_name,
            "customer_phone": b.customer_phone,
            "customer_address": b.customer_address,
            "customer_gstin": b.customer_gstin,
            "subtotal": b.subtotal,
            "tax_amount": b.tax_amount,
            "discount": b.discount,
            "total": b.total,
            "payment_mode": b.payment_mode,
            "shop_name": user.shop_name,
            "shop_address": user.address or "Tamil Nadu, India",
            "phone": user.phone or "",
            "store_type": user.store_type,
            "invoice_type": b.invoice_type,
            "items": [{"barcode": it.barcode or "-", "imei_serial": it.imei_serial or "-", "name": it.product_name, "price": it.price, "qty": it.quantity, "amt": it.amount} for it in items]
        })

    return {
        "stats": {
            "total_sales": total_sales,
            "total_bills": total_bills,
            "low_stock_count": low_stock,
            "total_products": total_products
        },
        "recent_bills": bills_data
    }