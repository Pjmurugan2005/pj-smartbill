import os
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./pj_smartbill.db")

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

if "sqlite" in DATABASE_URL:
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    shop_name = Column(String(150), default="PJ Smart Store")
    email = Column(String(120), unique=True, index=True, nullable=False)
    phone = Column(String(20), unique=True, index=True, nullable=True)
    address = Column(Text, default="")
    store_type = Column(String(50), default="MOBILE")  # 'DEPARTMENT' or 'MOBILE'
    profile_image = Column(String(255), default="/static/default-shop.png")
    password_hash = Column(String(255), nullable=False)
    is_verified = Column(Boolean, default=False)
    is_subscribed = Column(Boolean, default=False)
    subscription_plan = Column(String(50), default="TRIAL")
    subscription_end = Column(DateTime, nullable=True)
    razorpay_order_id = Column(String(100), nullable=True)
    razorpay_subscription_id = Column(String(100), unique=True, nullable=True) # Re-use attack prevention
    otp = Column(String(6), nullable=True)
    otp_expiry = Column(DateTime, nullable=True)
    otp_attempts = Column(Integer, default=0) # Brute-force block
    created_at = Column(DateTime, default=datetime.utcnow)

    products = relationship("Product", back_populates="user", cascade="all, delete-orphan")
    bills = relationship("Bill", back_populates="user", cascade="all, delete-orphan")
    recharges = relationship("RechargeEntry", back_populates="user", cascade="all, delete-orphan")
    repairs = relationship("RepairJob", back_populates="user", cascade="all, delete-orphan")
    damages = relationship("DamageEntry", back_populates="user", cascade="all, delete-orphan")

class Product(Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    barcode = Column(String(64), index=True, nullable=True)
    imei_serial = Column(String(64), index=True, nullable=True)
    name = Column(String(200), index=True, nullable=False)
    category = Column(String(50), default="General")
    price = Column(Float, default=0.0)
    stock = Column(Integer, default=0)
    warranty_months = Column(Integer, default=0)
    image_url = Column(String(255), default="/static/default-item.png")
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="products")

class Bill(Base):
    __tablename__ = "bills"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    invoice_no = Column(String(64), unique=True, index=True)
    customer_name = Column(String(100), default="Walk-in Customer")
    customer_phone = Column(String(20), default="N/A")
    customer_address = Column(String(255), default="")
    customer_gstin = Column(String(50), default="")
    subtotal = Column(Float, default=0.0)
    tax_amount = Column(Float, default=0.0)
    discount = Column(Float, default=0.0)
    total = Column(Float, default=0.0)
    payment_mode = Column(String(20), default="Cash")
    payment_status = Column(String(20), default="PAID")
    invoice_type = Column(String(20), default="A4")
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="bills")
    items = relationship("BillItem", back_populates="bill", cascade="all, delete-orphan")

class BillItem(Base):
    __tablename__ = "bill_items"
    id = Column(Integer, primary_key=True, index=True)
    bill_id = Column(Integer, ForeignKey("bills.id"))
    barcode = Column(String(64), default="-")
    imei_serial = Column(String(64), default="-")
    product_name = Column(String(200), nullable=False)
    price = Column(Float, default=0.0)
    quantity = Column(Integer, default=1)
    amount = Column(Float, default=0.0)

    bill = relationship("Bill", back_populates="items")

class RechargeEntry(Base):
    __tablename__ = "recharges"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    mobile_number = Column(String(20), nullable=False)
    operator = Column(String(50), nullable=False)
    amount = Column(Float, nullable=False)
    payment_mode = Column(String(20), default="Cash")
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="recharges")

class RepairJob(Base):
    __tablename__ = "repairs"
    id = Column(Integer, primary_key=True, index=True)
    job_no = Column(String(64), unique=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    customer_name = Column(String(100), nullable=False)
    customer_phone = Column(String(20), nullable=False)
    device_model = Column(String(100), nullable=False)
    problem_desc = Column(Text, nullable=False)
    status = Column(String(50), default="Received")
    estimated_cost = Column(Float, default=0.0)
    advance_paid = Column(Float, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="repairs")

class DamageEntry(Base):
    __tablename__ = "damages"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    category = Column(String(50), nullable=False)
    item_name = Column(String(200), nullable=False)
    quantity = Column(Integer, default=1)
    loss_amount = Column(Float, default=0.0)
    reason = Column(String(200), default="Physical Damage")
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="damages")

def init_db():
    Base.metadata.create_all(bind=engine)