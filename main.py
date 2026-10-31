import os
import re
import csv
import io
import shutil
import random
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from uuid import uuid4
from typing import List, Optional

import razorpay
from fastapi import FastAPI, Depends, HTTPException, Request, Response, UploadFile, File, Form, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func, or_, desc
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

import database as db
import auth

db.init_db()

UPLOAD_DIR = os.path.join("static", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="PJ SmartBill POS Multi-Enterprise")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

SMTP_EMAIL = os.getenv("SMTP_EMAIL", "jeyamuruganmurugan765@gmail.com")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "luta yqru mqhw uobn")

RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "rzp_live_TuNjkOUAdy8c23")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "CNe327TPx4eAkaMsMpC3HIYW")
rzp_client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))

@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
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
    return session.query(db.User).filter(db.User.id == int(data["sub"])).first()

def send_real_email_otp(to_email: str, otp: str, shop_name: str):
    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = f"PJ SmartBill <{SMTP_EMAIL}>"
        msg["To"] = to_email
        msg["Subject"] = f"{otp} is your PJ SmartBill Verification Code"

        html_body = f"""
        <div style="font-family: Arial, sans-serif; max-width: 520px; margin: auto; padding: 25px; border: 1px solid #e2e8f0; border-radius: 16px; background: #ffffff;">
          <h2 style="color: #059669; text-align: center; margin-bottom: 4px;">🧾 PJ SmartBill POS</h2>
          <p style="text-align: center; color: #64748b; font-size: 13px; margin-top: 0;">Multi-Enterprise Cloud Billing</p>
          <hr style="border: none; border-top: 1px solid #f1f5f9; margin: 20px 0;">
          <p style="font-size: 14px; color: #334155;">Hello,</p>
          <p style="font-size: 14px; color: #334155;">Your verification code for <b>{shop_name}</b> is:</p>
          <div style="text-align: center; margin: 25px 0;">
            <span style="display: inline-block; font-size: 34px; font-weight: 900; letter-spacing: 8px; color: #0f172a; background: #ecfdf5; padding: 12px 28px; border-radius: 12px; border: 2px dashed #059669;">{otp}</span>
          </div>
          <p style="font-size: 12px; color: #dc2626; text-align: center;">This code is valid for 10 minutes only.</p>
        </div>
        """
        msg.attach(MIMEText(html_body, "html"))
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(SMTP_EMAIL.strip(), SMTP_PASSWORD.strip())
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        print(f"[SMTP Error]: {e}")
        return False

# ----------------- SCHEMAS -----------------
class RegisterInitiatePayload(BaseModel):
    shop_name: str
    email: str
    phone: str
    password: str
    confirm_password: str

class VerifyRegisterOTPPayload(BaseModel):
    email: str
    otp: str

class LoginPayload(BaseModel):
    identifier: str
    password: str

class StoreSetupPayload(BaseModel):
    address: str
    store_type: str  # 'DEPARTMENT' or 'MOBILE'

class CartItem(BaseModel):
    product_id: int
    quantity: int

class BillPayload(BaseModel):
    customer_name: Optional[str] = "Walk-in"
    customer_phone: Optional[str] = ""
    customer_address: Optional[str] = ""
    customer_gstin: Optional[str] = ""
    discount: float = 0.0
    payment_mode: str = "Cash"
    items: List[CartItem]

class VerifyPaymentPayload(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str

class RechargeCreate(BaseModel):
    mobile_number: str
    operator: str
    amount: float
    payment_mode: str = "Cash"

class RepairCreate(BaseModel):
    customer_name: str
    customer_phone: str
    device_model: str
    problem_desc: str
    estimated_cost: float = 0.0
    advance_paid: float = 0.0

class DamageCreate(BaseModel):
    category: str
    item_name: str
    quantity: int = 1
    loss_amount: float
    reason: str = "Screen / Body Damage"

# ----------------- ROUTES -----------------
@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"rzp_key": RAZORPAY_KEY_ID}
    )

@app.post("/api/auth/register-initiate")
@limiter.limit("5/minute")
def register_initiate(request: Request, data: RegisterInitiatePayload, session: Session = Depends(get_db)):
    shop_name = data.shop_name.strip()
    email = data.email.strip().lower()
    phone = data.phone.strip()
    pwd = data.password.strip()

    if pwd != data.confirm_password.strip():
        raise HTTPException(status_code=400, detail="Passwords do not match!")

    clean_phone = ''.join(filter(str.isdigit, phone))[-10:]
    existing_user = session.query(db.User).filter(or_(db.User.email == email, db.User.phone == clean_phone)).first()
    if existing_user and existing_user.is_verified:
        raise HTTPException(status_code=400, detail="This Email or Phone is already registered!")

    otp = f"{random.randint(100000, 999999)}"
    expiry = datetime.utcnow() + timedelta(minutes=10)
    p_hash = auth.hash_password(pwd)

    if existing_user and not existing_user.is_verified:
        existing_user.shop_name = shop_name
        existing_user.email = email
        existing_user.phone = clean_phone
        existing_user.password_hash = p_hash
        existing_user.otp = otp
        existing_user.otp_expiry = expiry
    else:
        new_user = db.User(
            shop_name=shop_name,
            email=email,
            phone=clean_phone,
            password_hash=p_hash,
            is_verified=False,
            is_subscribed=False,
            otp=otp,
            otp_expiry=expiry
        )
        session.add(new_user)
    session.commit()

    send_real_email_otp(email, otp, shop_name)
    return {"message": f"Verification code sent to {email}!", "email": email}

@app.post("/api/auth/register-verify")
def register_verify(data: VerifyRegisterOTPPayload, response: Response, session: Session = Depends(get_db)):
    email = data.email.strip().lower()
    user = session.query(db.User).filter(db.User.email == email).first()
    if not user or user.otp != data.otp.strip():
        raise HTTPException(status_code=400, detail="Invalid verification code!")

    user.is_verified = True
    user.otp = None
    session.commit()

    token = auth.create_access_token({"sub": str(user.id)})
    response.set_cookie(key="access_token", value=token, httponly=True, max_age=86400 * 30, samesite="lax")
    return {"shop_name": user.shop_name, "token": token, "message": "Registered successfully!"}

@app.post("/api/auth/login")
@limiter.limit("10/minute")
def login(request: Request, data: LoginPayload, response: Response, session: Session = Depends(get_db)):
    ident = data.identifier.strip().lower()
    user = session.query(db.User).filter(or_(db.User.email == ident, db.User.phone == ident)).first()
    if not user or not user.is_verified or not auth.verify_password(data.password.strip(), user.password_hash):
        raise HTTPException(status_code=400, detail="Invalid Email/Phone or Password!")

    token = auth.create_access_token({"sub": str(user.id)})
    response.set_cookie(key="access_token", value=token, httponly=True, max_age=86400 * 30, samesite="lax")
    return {"shop_name": user.shop_name, "token": token}

@app.post("/api/logout")
def logout(response: Response):
    response.delete_cookie("access_token")
    return {"message": "Logged out successfully"}

# --- FIXED: 30 DAYS SUBSCRIPTION VALIDITY CHECK ---
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
                is_active = False
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
        raise HTTPException(status_code=400, detail="Invalid store type selected")

    user.address = data.address.strip()
    user.store_type = data.store_type
    session.commit()
    return {"status": "success", "message": "Store configuration saved!", "store_type": user.store_type}

@app.post("/api/user/profile-update")
def update_profile(
    shop_name: str = Form(...),
    phone: str = Form(...),
    email: str = Form(...),
    address: str = Form(...),
    store_type: Optional[str] = Form(None),
    photo: Optional[UploadFile] = File(None),
    user: db.User = Depends(get_current_user),
    session: Session = Depends(get_db)
):
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    user.shop_name = shop_name.strip()
    user.phone = phone.strip()
    user.email = email.strip()
    user.address = address.strip()
    if store_type in ["DEPARTMENT", "MOBILE"]:
        user.store_type = store_type

    profile_img_url = getattr(user, "profile_image", "/static/default-shop.png")
    if photo and photo.filename:
        filename = f"shop_{user.id}_{int(datetime.utcnow().timestamp())}_{photo.filename}"
        filepath = os.path.join(UPLOAD_DIR, filename)
        with open(filepath, "wb") as buffer:
            shutil.copyfileobj(photo.file, buffer)
        profile_img_url = f"/{filepath}"
        user.profile_image = profile_img_url

    session.commit()
    session.refresh(user)

    return {
        "status": "success",
        "message": "Store profile updated successfully",
        "shop_name": user.shop_name,
        "profile_image": profile_img_url,
        "store_type": user.store_type
    }

# --- RAZORPAY SUBSCRIPTION ---
@app.post("/api/subscription/create-order")
def create_subscription_order(user: db.User = Depends(get_current_user)):
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    order_payload = {
        "amount": 100,  # ₹1.00
        "currency": "INR",
        "receipt": f"sub_rcpt_{user.id}_{int(datetime.utcnow().timestamp())}",
        "notes": {
            "user_id": str(user.id),
            "email": user.email,
            "plan": "TRIAL_1RS_THEN_299",
            "store_type": user.store_type or "MOBILE"
        }
    }
    try:
        order = rzp_client.order.create(data=order_payload)
        return {
            "order_id": order["id"],
            "amount": order["amount"],
            "currency": order["currency"],
            "key": RAZORPAY_KEY_ID
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Razorpay Order Error: {str(e)}")

# --- FIXED: STRICTLY ADD 30 DAYS IN DATABASE ---
@app.post("/api/subscription/verify-payment")
def verify_payment(
    data: VerifyPaymentPayload,
    user: db.User = Depends(get_current_user),
    session: Session = Depends(get_db)
):
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    params_dict = {
        'razorpay_order_id': data.razorpay_order_id,
        'razorpay_payment_id': data.razorpay_payment_id,
        'razorpay_signature': data.razorpay_signature
    }

    try:
        rzp_client.utility.verify_payment_signature(params_dict)
    except razorpay.errors.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Payment signature verification failed!")

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

# --- ANTI-HANG FAST CATALOG SEARCH (Limit 30 items) ---
@app.get("/api/products")
def get_products(
    search: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = 40,
    user: db.User = Depends(get_current_user),
    session: Session = Depends(get_db)
):
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
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

    prods = query.order_by(desc(db.Product.id)).limit(limit).all()
    return [{
        "id": p.id,
        "barcode": p.barcode or "-",
        "imei_serial": p.imei_serial or "-",
        "name": p.name,
        "category": getattr(p, "category", "General"),
        "price": p.price,
        "stock": p.stock,
        "warranty_months": p.warranty_months or 0,
        "image_url": getattr(p, "image_url", "/static/default-item.png")
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
    user: db.User = Depends(get_current_user),
    session: Session = Depends(get_db)
):
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    clean_barcode = barcode.strip() if barcode else None
    clean_imei = imei_serial.strip() if imei_serial else None

    image_url = "/static/default-item.png"
    if image and image.filename:
        filename = f"prod_{user.id}_{int(datetime.utcnow().timestamp())}_{image.filename}"
        filepath = os.path.join(UPLOAD_DIR, filename)
        with open(filepath, "wb") as buffer:
            shutil.copyfileobj(image.file, buffer)
        image_url = f"/{filepath}"

    prod = db.Product(
        user_id=user.id,
        barcode=clean_barcode,
        imei_serial=clean_imei,
        name=name.strip(),
        category=category.strip(),
        price=price,
        stock=stock,
        warranty_months=warranty_months or 0,
        image_url=image_url
    )
    session.add(prod)
    session.commit()
    return {"message": "Product saved successfully!"}

@app.delete("/api/products/{prod_id}")
def delete_product(prod_id: int, user: db.User = Depends(get_current_user), session: Session = Depends(get_db)):
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    prod = session.query(db.Product).filter(db.Product.id == prod_id, db.Product.user_id == user.id).first()
    if prod:
        session.delete(prod)
        session.commit()
    return {"message": "Deleted"}

@app.post("/api/products/import-csv")
async def import_products_csv(
    file: UploadFile = File(...),
    user: db.User = Depends(get_current_user),
    session: Session = Depends(get_db)
):
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if not file.filename.endswith('.csv'):
        raise HTTPException(status_code=400, detail="Only CSV files are supported")

    content = await file.read()
    decoded = content.decode('utf-8', errors='ignore')
    reader = csv.DictReader(io.StringIO(decoded))

    count = 0
    bulk = []
    for row in reader:
        pname = row.get("name") or row.get("product_name")
        price = float(row.get("price", 0.0) or 0.0)
        if not pname or price <= 0:
            continue

        bcode = row.get("barcode", "").strip() or str(uuid4().int)[:12]
        imei = row.get("imei_serial", "-").strip()
        cat = row.get("category", "General").strip()

        new_p = db.Product(
            user_id=user.id,
            barcode=bcode,
            imei_serial=imei,
            name=pname.strip(),
            category=cat,
            price=price,
            stock=int(row.get("stock", 50) or 50),
            warranty_months=int(row.get("warranty_months", 12) or 12),
            image_url=row.get("image_url", "/static/default-item.png").strip()
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

    return {"status": "success", "message": f"Successfully imported {count} items into catalog!"}

# --- POS BILLING CHECKOUT (A4 or Thermal + Negative Stock Support) ---
@app.post("/api/checkout")
def checkout(payload: BillPayload, user: db.User = Depends(get_current_user), session: Session = Depends(get_db)):
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    if not payload.items:
        raise HTTPException(status_code=400, detail="Cart is empty!")

    subtotal = 0.0
    items_to_save = []

    for itm in payload.items:
        p = session.query(db.Product).filter(db.Product.id == itm.product_id, db.Product.user_id == user.id).first()
        if not p:
            continue
        amt = round(p.price * itm.quantity, 2)
        subtotal += amt
        p.stock -= itm.quantity  # Allows selling even if zero stock (negative billing)
        items_to_save.append({
            "obj": p,
            "barcode": p.barcode or "-",
            "imei_serial": p.imei_serial or "-",
            "warranty_months": p.warranty_months or 0,
            "name": p.name,
            "price": p.price,
            "qty": itm.quantity,
            "amt": amt
        })

    is_mobile_shop = (user.store_type == "MOBILE")
    tax_amount = 0.0
    if is_mobile_shop:
        base_subtotal = round(subtotal / 1.18, 2)
        tax_amount = round(subtotal - base_subtotal, 2)

    total = max(0.0, round(subtotal - payload.discount, 2))
    prefix = "MOB" if is_mobile_shop else "POS"
    invoice_no = f"{prefix}-{datetime.now().strftime('%y%m%d')}-{uuid4().hex[:4].upper()}"

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
    session.commit()
    session.refresh(bill)

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

    return {
        "invoice_no": invoice_no,
        "date": bill.created_at.strftime("%d-%m-%Y %I:%M %p"),
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
        "phone": user.phone,
        "store_type": user.store_type,
        "invoice_type": bill.invoice_type,
        "items": [{
            "barcode": i["barcode"],
            "imei_serial": i["imei_serial"],
            "warranty_months": i["warranty_months"],
            "name": i["name"],
            "price": i["price"],
            "qty": i["qty"],
            "amt": i["amt"]
        } for i in items_to_save]
    }

# --- MOBIBILLER FEATURE 1: RECHARGE BOOK ---
@app.get("/api/recharges")
def get_recharges(user: db.User = Depends(get_current_user), session: Session = Depends(get_db)):
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    recs = session.query(db.RechargeEntry).filter(db.RechargeEntry.user_id == user.id).order_by(desc(db.RechargeEntry.id)).limit(20).all()
    today_total = sum(r.amount for r in recs if r.created_at.date() == datetime.utcnow().date())
    return {
        "today_total": today_total,
        "history": [{
            "id": r.id,
            "mobile": r.mobile_number,
            "operator": r.operator,
            "amount": r.amount,
            "time": r.created_at.strftime("%I:%M %p")
        } for r in recs]
    }

@app.post("/api/recharges")
def add_recharge(req: RechargeCreate, user: db.User = Depends(get_current_user), session: Session = Depends(get_db)):
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    entry = db.RechargeEntry(
        user_id=user.id,
        mobile_number=req.mobile_number,
        operator=req.operator,
        amount=req.amount,
        payment_mode=req.payment_mode
    )
    session.add(entry)
    session.commit()
    return {"status": "success", "message": "Recharge logged"}

# --- MOBIBILLER FEATURE 2: REPAIRS / JOB SHEET TRACKER ---
@app.get("/api/repairs")
def get_repairs(user: db.User = Depends(get_current_user), session: Session = Depends(get_db)):
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
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
def add_repair(req: RepairCreate, user: db.User = Depends(get_current_user), session: Session = Depends(get_db)):
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    job_no = f"JOB-{uuid4().hex[:5].upper()}"
    job = db.RepairJob(
        job_no=job_no,
        user_id=user.id,
        customer_name=req.customer_name,
        customer_phone=req.customer_phone,
        device_model=req.device_model,
        problem_desc=req.problem_desc,
        estimated_cost=req.estimated_cost,
        advance_paid=req.advance_paid,
        status="Received"
    )
    session.add(job)
    session.commit()
    return {"status": "success", "job_no": job_no}

# --- MOBIBILLER FEATURE 3: DAMAGE RECORDING MODULE ---
@app.get("/api/damages")
def get_damages(user: db.User = Depends(get_current_user), session: Session = Depends(get_db)):
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    items = session.query(db.DamageEntry).filter(db.DamageEntry.user_id == user.id).order_by(desc(db.DamageEntry.id)).all()
    today_loss = sum(d.loss_amount for d in items if d.created_at.date() == datetime.utcnow().date())
    return {
        "total_loss": today_loss,
        "items": [{
            "category": d.category,
            "item": d.item_name,
            "qty": d.quantity,
            "loss": d.loss_amount,
            "reason": d.reason
        } for d in items]
    }

@app.post("/api/damages")
def add_damage(req: DamageCreate, user: db.User = Depends(get_current_user), session: Session = Depends(get_db)):
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    entry = db.DamageEntry(
        user_id=user.id,
        category=req.category,
        item_name=req.item_name,
        quantity=req.quantity,
        loss_amount=req.loss_amount,
        reason=req.reason
    )
    session.add(entry)
    session.commit()
    return {"status": "success", "message": "Damage loss recorded"}

# --- MOBIBILLER FEATURE 4: BUSINESS REPORTS & PAYMENT BREAKDOWN ---
@app.get("/api/reports")
def get_reports(user: db.User = Depends(get_current_user), session: Session = Depends(get_db)):
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    today = datetime.utcnow().date()
    sales = session.query(func.sum(db.Bill.total)).filter(db.Bill.user_id == user.id, func.date(db.Bill.created_at) == today).scalar() or 0.0
    recharges = session.query(func.sum(db.RechargeEntry.amount)).filter(db.RechargeEntry.user_id == user.id, func.date(db.RechargeEntry.created_at) == today).scalar() or 0.0
    cash_total = session.query(func.sum(db.Bill.total)).filter(db.Bill.user_id == user.id, db.Bill.payment_mode == "Cash", func.date(db.Bill.created_at) == today).scalar() or 0.0
    upi_total = session.query(func.sum(db.Bill.total)).filter(db.Bill.user_id == user.id, db.Bill.payment_mode == "UPI", func.date(db.Bill.created_at) == today).scalar() or 0.0
    repairs_count = session.query(func.count(db.RepairJob.id)).filter(db.RepairJob.user_id == user.id).scalar() or 0
    return {
        "sales": sales,
        "recharges": recharges,
        "cash_payments": cash_total + recharges,
        "upi_payments": upi_total,
        "repairs_count": repairs_count
    }

# --- ORIGINAL ANALYTICS DASHBOARD ---
@app.get("/api/dashboard")
def get_dashboard(user: db.User = Depends(get_current_user), session: Session = Depends(get_db)):
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    total_sales = session.query(func.sum(db.Bill.total)).filter(db.Bill.user_id == user.id).scalar() or 0.0
    total_bills = session.query(func.count(db.Bill.id)).filter(db.Bill.user_id == user.id).scalar() or 0
    low_stock = session.query(func.count(db.Product.id)).filter(db.Product.user_id == user.id, db.Product.stock <= 5).scalar() or 0
    total_products = session.query(func.count(db.Product.id)).filter(db.Product.user_id == user.id).scalar() or 0

    recent_bills = session.query(db.Bill).filter(db.Bill.user_id == user.id).order_by(db.Bill.id.desc()).limit(15).all()
    bills_data = []
    for b in recent_bills:
        items = session.query(db.BillItem).filter(db.BillItem.bill_id == b.id).all()
        bills_data.append({
            "id": b.id,
            "invoice_no": b.invoice_no,
            "date": b.created_at.strftime("%d-%m-%Y %I:%M %p"),
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
            "phone": user.phone,
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