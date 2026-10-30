from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

DATABASE_URL = "sqlite:///./pj_smartbill.db"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    shop_name = Column(String, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    phone = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    is_verified = Column(Boolean, default=False)
    
    # Subscription tracking (₹1 first month -> ₹299 autopay)
    is_subscribed = Column(Boolean, default=False)
    subscription_plan = Column(String, default="INACTIVE")
    subscription_end = Column(DateTime, nullable=True)
    razorpay_subscription_id = Column(String, nullable=True)
    
    otp = Column(String, nullable=True)
    otp_expiry = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    products = relationship("Product", back_populates="owner")
    bills = relationship("Bill", back_populates="owner")

class Product(Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    barcode = Column(String, index=True, nullable=True)
    name = Column(String, index=True, nullable=False)
    price = Column(Float, nullable=False)
    stock = Column(Integer, default=0)

    owner = relationship("User", back_populates="products")

class Bill(Base):
    __tablename__ = "bills"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    invoice_no = Column(String, unique=True, index=True)
    customer_name = Column(String, default="Walk-in")
    customer_phone = Column(String, default="")
    subtotal = Column(Float, default=0.0)
    discount = Column(Float, default=0.0)
    total = Column(Float, nullable=False)
    payment_mode = Column(String, default="Cash")  # Cash, UPI, Card
    payment_status = Column(String, default="PAID") 
    created_at = Column(DateTime, default=datetime.utcnow)

    owner = relationship("User", back_populates="bills")
    items = relationship("BillItem", back_populates="bill", cascade="all, delete-orphan")

class BillItem(Base):
    __tablename__ = "bill_items"
    id = Column(Integer, primary_key=True, index=True)
    bill_id = Column(Integer, ForeignKey("bills.id"))
    barcode = Column(String, nullable=True)
    product_name = Column(String, nullable=False)
    price = Column(Float, nullable=False)
    quantity = Column(Integer, nullable=False)
    amount = Column(Float, nullable=False)

    bill = relationship("Bill", back_populates="items")

def init_db():
    Base.metadata.create_all(bind=engine)