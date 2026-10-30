import re
import random
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from uuid import uuid4
from typing import List, Optional
import razorpay
from fastapi import FastAPI, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func, or_

import database as db
import auth

db.init_db()

app = FastAPI(title="PJ SmartBill POS Enterprise")
templates = Jinja2Templates(directory="templates")

# Ungaloda Gmail SMTP Details
SMTP_EMAIL = "jeyamuruganmurugan765@gmail.com"
SMTP_PASSWORD = "luta yqru mqhw uobn"

# Razorpay Test Credentials (Subscription paywall-kku mattum)
RAZORPAY_KEY_ID = "rzp_test_1DP5mmOlF5G5ag"
RAZORPAY_KEY_SECRET = "dummy_secret_for_now"
rzp_client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))

def get_db():
    session = db.SessionLocal()
    try:
        yield session
    finally:
        session.close()

def get_current_user(request: Request, session: Session = Depends(get_db)):
    token = request.cookies.get("access_token")
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
          <p style="text-align: center; color: #64748b; font-size: 13px; margin-top: 0;">Departmental Store Retail System</p>
          <hr style="border: none; border-top: 1px solid #f1f5f9; margin: 20px 0;">
          <p style="font-size: 14px; color: #334155;">Vanakkam,</p>
          <p style="font-size: 14px; color: #334155;">Ungal <b>{shop_name}</b> store registration-kaga intha OTP anuppappattullathu:</p>
          <div style="text-align: center; margin: 25px 0;">
            <span style="display: inline-block; font-size: 34px; font-weight: 900; letter-spacing: 8px; color: #0f172a; background: #ecfdf5; padding: 12px 28px; border-radius: 12px; border: 2px dashed #059669;">{otp}</span>
          </div>
          <p style="font-size: 12px; color: #dc2626; text-align: center;">Intha code 10 nimidangalukku mattumey chellum.</p>
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

# Pydantic Schemas
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

class ProductPayload(BaseModel):
    barcode: Optional[str] = ""
    name: str
    price: float
    stock: int

class CartItem(BaseModel):
    product_id: int
    quantity: int

class BillPayload(BaseModel):
    customer_name: Optional[str] = "Walk-in"
    customer_phone: Optional[str] = ""
    discount: float = 0.0
    payment_mode: str = "Cash"  # Cash, UPI, Card (Staff Selection)
    items: List[CartItem]

class SubscriptionVerifyPayload(BaseModel):
    payment_id: str
    plan: str

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"rzp_key": RAZORPAY_KEY_ID}
    )

# --- Register Step 1: Send Real Email OTP ---
@app.post("/api/auth/register-initiate")
def register_initiate(data: RegisterInitiatePayload, session: Session = Depends(get_db)):
    shop_name = data.shop_name.strip()
    email = data.email.strip().lower()
    phone = data.phone.strip()
    pwd = data.password.strip()

    if pwd != data.confirm_password.strip():
        raise HTTPException(status_code=400, detail="Passwords match aagavillai!")

    clean_phone = ''.join(filter(str.isdigit, phone))[-10:]
    existing_user = session.query(db.User).filter(or_(db.User.email == email, db.User.phone == clean_phone)).first()
    if existing_user and existing_user.is_verified:
        raise HTTPException(status_code=400, detail="Intha Email/Phone erkanave register aagiyullathu!")

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
    return {"message": f"OTP verification code {email}-kku anuppappattadhu!", "email": email}

# --- Register Step 2: Verify Real OTP ---
@app.post("/api/auth/register-verify")
def register_verify(data: VerifyRegisterOTPPayload, response: Response, session: Session = Depends(get_db)):
    email = data.email.strip().lower()
    user = session.query(db.User).filter(db.User.email == email).first()
    if not user or user.otp != data.otp.strip():
        raise HTTPException(status_code=400, detail="Thavarana OTP!")

    user.is_verified = True
    user.otp = None
    session.commit()

    token = auth.create_access_token({"sub": str(user.id)})
    response.set_cookie(key="access_token", value=token, httponly=True, max_age=86400 * 7)
    return {"shop_name": user.shop_name, "message": "Registered successfully!"}

# --- Direct Login ---
@app.post("/api/auth/login")
def login(data: LoginPayload, response: Response, session: Session = Depends(get_db)):
    ident = data.identifier.strip().lower()
    user = session.query(db.User).filter(or_(db.User.email == ident, db.User.phone == ident)).first()
    if not user or not user.is_verified or not auth.verify_password(data.password.strip(), user.password_hash):
        raise HTTPException(status_code=400, detail="Thavarana Email/Phone alladhu Password!")

    token = auth.create_access_token({"sub": str(user.id)})
    response.set_cookie(key="access_token", value=token, httponly=True, max_age=86400 * 7)
    return {"shop_name": user.shop_name}

@app.post("/api/logout")
def logout(response: Response):
    response.delete_cookie("access_token")
    return {"message": "Logged out"}

@app.get("/api/me")
def check_me(user: Optional[db.User] = Depends(get_current_user)):
    if not user or not user.is_verified:
        raise HTTPException(status_code=401, detail="Not logged in")

    is_active = False
    days_left = 0
    if user.is_subscribed and user.subscription_end:
        if datetime.utcnow() <= user.subscription_end:
            is_active = True
            days_left = (user.subscription_end - datetime.utcnow()).days + 1
        else:
            is_active = False

    return {
        "shop_name": user.shop_name,
        "email": user.email,
        "phone": user.phone,
        "is_subscribed": is_active,
        "subscription_plan": user.subscription_plan,
        "days_left": days_left,
        "subscription_end": user.subscription_end.strftime("%d %b %Y") if user.subscription_end else None
    }

# --- Subscription Paywall Activation (₹1 first month + ₹299 autopay) ---
@app.post("/api/subscription/activate")
def activate_subscription(data: SubscriptionVerifyPayload, user: db.User = Depends(get_current_user), session: Session = Depends(get_db)):
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    user.is_subscribed = True
    user.subscription_plan = data.plan
    user.subscription_end = datetime.utcnow() + timedelta(days=30)
    user.razorpay_subscription_id = data.payment_id
    session.commit()

    return {
        "message": "Subscription activated! Store is unlocked for 30 days.",
        "subscription_end": user.subscription_end.strftime("%d %b %Y")
    }

# --- Products APIs (With Barcode Scanner Support) ---
@app.get("/api/products")
def get_products(user: db.User = Depends(get_current_user), session: Session = Depends(get_db)):
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return session.query(db.Product).filter(db.Product.user_id == user.id).order_by(db.Product.name.asc()).all()

@app.post("/api/products")
def add_product(item: ProductPayload, user: db.User = Depends(get_current_user), session: Session = Depends(get_db)):
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    clean_barcode = item.barcode.strip() if item.barcode else None
    if clean_barcode:
        existing = session.query(db.Product).filter(db.Product.user_id == user.id, db.Product.barcode == clean_barcode).first()
        if existing:
            raise HTTPException(status_code=400, detail=f"Intha Barcode ({clean_barcode}) erkanave '{existing.name}'-kku irukku!")

    prod = db.Product(
        user_id=user.id,
        barcode=clean_barcode,
        name=item.name.strip(),
        price=item.price,
        stock=item.stock
    )
    session.add(prod)
    session.commit()
    return {"message": "Product serkkappattathu!"}

@app.put("/api/products/{prod_id}")
def update_product(prod_id: int, item: ProductPayload, user: db.User = Depends(get_current_user), session: Session = Depends(get_db)):
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    prod = session.query(db.Product).filter(db.Product.id == prod_id, db.Product.user_id == user.id).first()
    if not prod:
        raise HTTPException(status_code=404, detail="Product kaanavillai!")
    
    clean_barcode = item.barcode.strip() if item.barcode else None
    prod.barcode = clean_barcode
    prod.name = item.name.strip()
    prod.price = item.price
    prod.stock = item.stock
    session.commit()
    return {"message": "Product updated!"}

@app.delete("/api/products/{prod_id}")
def delete_product(prod_id: int, user: db.User = Depends(get_current_user), session: Session = Depends(get_db)):
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    prod = session.query(db.Product).filter(db.Product.id == prod_id, db.Product.user_id == user.id).first()
    if prod:
        session.delete(prod)
        session.commit()
    return {"message": "Deleted"}

# --- POS Billing Checkout (No Razorpay pop-up; Direct Cash/UPI/Card save by staff) ---
@app.post("/api/checkout")
def checkout(payload: BillPayload, user: db.User = Depends(get_current_user), session: Session = Depends(get_db)):
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    # 30-Day Paywall check
    if not user.is_subscribed or (user.subscription_end and datetime.utcnow() > user.subscription_end):
        raise HTTPException(status_code=403, detail="Subscription expire aagivittathu! Please renew panni bill podavum.")

    subtotal = 0.0
    items_to_save = []

    for itm in payload.items:
        p = session.query(db.Product).filter(db.Product.id == itm.product_id, db.Product.user_id == user.id).first()
        if not p:
            raise HTTPException(status_code=404, detail="Product kaanavillai!")
        if p.stock < itm.quantity:
            raise HTTPException(status_code=400, detail=f"'{p.name}' podhumana stock illai! (Current: {p.stock})")
        
        amt = round(p.price * itm.quantity, 2)
        subtotal += amt
        items_to_save.append({"obj": p, "barcode": p.barcode or "-", "name": p.name, "price": p.price, "qty": itm.quantity, "amt": amt})

    total = max(0.0, round(subtotal - payload.discount, 2))
    invoice_no = f"POS-{datetime.now().strftime('%y%m%d')}-{uuid4().hex[:4].upper()}"

    # Stock deduct
    for itm in items_to_save:
        itm["obj"].stock -= itm["qty"]

    bill = db.Bill(
        user_id=user.id,
        invoice_no=invoice_no,
        customer_name=payload.customer_name or "Walk-in Customer",
        customer_phone=payload.customer_phone or "N/A",
        subtotal=subtotal,
        discount=payload.discount,
        total=total,
        payment_mode=payload.payment_mode,
        payment_status="PAID"
    )
    session.add(bill)
    session.commit()
    session.refresh(bill)

    for itm in items_to_save:
        session.add(db.BillItem(
            bill_id=bill.id,
            barcode=itm["barcode"],
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
        "subtotal": subtotal,
        "discount": bill.discount,
        "total": total,
        "payment_mode": payload.payment_mode,
        "shop_name": user.shop_name,
        "phone": user.phone,
        "items": [{"barcode": i["barcode"], "name": i["name"], "price": i["price"], "qty": i["qty"], "amt": i["amt"]} for i in items_to_save]
    }

# --- Analytics Dashboard ---
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
            "subtotal": b.subtotal,
            "discount": b.discount,
            "total": b.total,
            "payment_mode": b.payment_mode,
            "shop_name": user.shop_name,
            "phone": user.phone,
            "items": [{"barcode": it.barcode or "-", "name": it.product_name, "price": it.price, "qty": it.quantity, "amt": it.amount} for it in items]
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