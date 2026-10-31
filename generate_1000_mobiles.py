import csv
import random

# Real market base catalog (Current Indian market pricing)
BASE_DEVICES = [
    # --- Budget & Entry Level (₹7,000 - ₹14,000) ---
    ("Redmi 13C 5G", [("4GB/128GB", 10499), ("6GB/128GB", 11999), ("8GB/256GB", 13499)], 12, "Smartphones"),
    ("Redmi 12 5G", [("4GB/128GB", 11999), ("6GB/128GB", 12499), ("8GB/256GB", 13999)], 12, "Smartphones"),
    ("Realme 12x 5G", [("4GB/128GB", 11999), ("6GB/128GB", 12999), ("8GB/128GB", 13999)], 12, "Smartphones"),
    ("Realme C65 5G", [("4GB/64GB", 9999), ("4GB/128GB", 10999), ("6GB/128GB", 11999)], 12, "Smartphones"),
    ("Samsung Galaxy M15 5G", [("4GB/128GB", 11999), ("6GB/128GB", 13499), ("8GB/128GB", 14999)], 12, "Smartphones"),
    ("Samsung Galaxy F15 5G", [("4GB/128GB", 11999), ("6GB/128GB", 13499)], 12, "Smartphones"),
    ("Vivo T3x 5G", [("4GB/128GB", 12999), ("6GB/128GB", 13999), ("8GB/128GB", 15499)], 12, "Smartphones"),
    ("Vivo Y28 5G", [("4GB/128GB", 13499), ("6GB/128GB", 14999)], 12, "Smartphones"),
    ("Moto G34 5G", [("4GB/128GB", 10999), ("8GB/128GB", 11999)], 12, "Smartphones"),
    ("Moto G64 5G", [("8GB/128GB", 13999), ("12GB/256GB", 15999)], 12, "Smartphones"),
    ("Poco M6 Pro 5G", [("4GB/128GB", 9999), ("6GB/128GB", 10999), ("8GB/256GB", 12499)], 12, "Smartphones"),
    ("Infinix Hot 40i", [("8GB/128GB", 8999), ("8GB/256GB", 9999)], 12, "Smartphones"),
    ("Infinix Note 40X 5G", [("8GB/256GB", 13499), ("12GB/256GB", 14999)], 12, "Smartphones"),
    
    # --- Mid-Range & Flagship Killers (₹15,000 - ₹35,000) ---
    ("Redmi Note 13 5G", [("6GB/128GB", 15999), ("8GB/256GB", 17999), ("12GB/256GB", 19999)], 12, "Smartphones"),
    ("Redmi Note 13 Pro 5G", [("8GB/128GB", 23999), ("8GB/256GB", 25999), ("12GB/256GB", 27999)], 12, "Smartphones"),
    ("Realme Narzo 70 Pro 5G", [("8GB/128GB", 17999), ("8GB/256GB", 19999)], 12, "Smartphones"),
    ("Realme 12 Pro 5G", [("8GB/128GB", 21999), ("8GB/256GB", 23999)], 12, "Smartphones"),
    ("Realme 12 Pro Plus 5G", [("8GB/128GB", 27999), ("8GB/256GB", 29999), ("12GB/256GB", 31999)], 12, "Smartphones"),
    ("Samsung Galaxy A15 5G", [("6GB/128GB", 17499), ("8GB/128GB", 19499), ("8GB/256GB", 21499)], 12, "Smartphones"),
    ("Samsung Galaxy A35 5G", [("8GB/128GB", 27999), ("8GB/256GB", 30999)], 12, "Smartphones"),
    ("Samsung Galaxy A55 5G", [("8GB/128GB", 36999), ("8GB/256GB", 39999), ("12GB/256GB", 42999)], 12, "Smartphones"),
    ("Vivo T3 5G", [("8GB/128GB", 18999), ("8GB/256GB", 20999)], 12, "Smartphones"),
    ("Vivo V30e 5G", [("8GB/128GB", 25999), ("8GB/256GB", 27999)], 12, "Smartphones"),
    ("Vivo V30 5G", [("8GB/128GB", 31999), ("8GB/256GB", 33999), ("12GB/512GB", 39999)], 12, "Smartphones"),
    ("Oppo A59 5G", [("4GB/128GB", 13999), ("6GB/128GB", 15499)], 12, "Smartphones"),
    ("Oppo F25 Pro 5G", [("8GB/128GB", 23999), ("8GB/256GB", 25999)], 12, "Smartphones"),
    ("Oppo Reno 11 5G", [("8GB/128GB", 27999), ("8GB/256GB", 29999)], 12, "Smartphones"),
    ("OnePlus Nord CE4 Lite 5G", [("8GB/128GB", 17999), ("8GB/256GB", 20999)], 12, "Smartphones"),
    ("OnePlus Nord CE4 5G", [("8GB/128GB", 24999), ("8GB/256GB", 26999)], 12, "Smartphones"),
    ("OnePlus Nord 4 5G", [("8GB/128GB", 29999), ("8GB/256GB", 32999), ("12GB/256GB", 35999)], 12, "Smartphones"),
    ("Poco X6 Pro 5G", [("8GB/256GB", 23999), ("12GB/512GB", 25999)], 12, "Smartphones"),
    ("Poco F6 5G", [("8GB/256GB", 29999), ("12GB/256GB", 31999), ("12GB/512GB", 33999)], 12, "Smartphones"),
    ("iQOO Z9 5G", [("8GB/128GB", 17999), ("8GB/256GB", 19999)], 12, "Smartphones"),
    ("iQOO Neo 9 Pro", [("8GB/128GB", 34999), ("8GB/256GB", 36999), ("12GB/256GB", 38999)], 12, "Smartphones"),
    
    # --- Premium & Flagship (₹40,000 - ₹1,40,000) ---
    ("OnePlus 12R", [("8GB/128GB", 37999), ("16GB/256GB", 42999)], 12, "Smartphones"),
    ("OnePlus 12", [("12GB/256GB", 61999), ("16GB/512GB", 67999)], 12, "Smartphones"),
    ("Samsung Galaxy S24", [("8GB/128GB", 64999), ("8GB/256GB", 69999)], 12, "Smartphones"),
    ("Samsung Galaxy S24 Plus", [("12GB/256GB", 84999), ("12GB/512GB", 94999)], 12, "Smartphones"),
    ("Samsung Galaxy S24 Ultra", [("12GB/256GB", 119999), ("12GB/512GB", 129999)], 12, "Smartphones"),
    ("Apple iPhone 13", [("128GB", 47999), ("256GB", 57999)], 12, "Smartphones"),
    ("Apple iPhone 14", [("128GB", 56999), ("256GB", 66999)], 12, "Smartphones"),
    ("Apple iPhone 15", [("128GB", 65999), ("256GB", 75999), ("512GB", 95999)], 12, "Smartphones"),
    ("Apple iPhone 15 Plus", [("128GB", 75999), ("256GB", 85999)], 12, "Smartphones"),
    ("Apple iPhone 15 Pro", [("128GB", 124999), ("256GB", 134999)], 12, "Smartphones"),
    ("Apple iPhone 15 Pro Max", [("256GB", 144999), ("512GB", 164999)], 12, "Smartphones"),

    # --- Feature Keypad Phones (₹1,000 - ₹3,500) ---
    ("Nokia 105 Single SIM", [("Charcoal", 1249), ("Cyan", 1249)], 12, "Keypad"),
    ("Nokia 105 Dual SIM", [("Charcoal", 1399), ("Blue", 1399)], 12, "Keypad"),
    ("Nokia 110 4G", [("Midnight Blue", 2399), ("Rose Gold", 2399)], 12, "Keypad"),
    ("JioPhone Prima 4G", [("Yellow", 2499), ("Blue", 2499)], 12, "Keypad"),
    ("Itel MagicX Pro 4G", [("Black", 2399), ("Blue", 2399)], 12, "Keypad"),
    ("Itel Power 450", [("Deep Blue", 1199)], 12, "Keypad"),
    ("Lava Flip", [("Red", 1999), ("Blue", 1999)], 12, "Keypad"),
    ("Samsung Guru Music 2", [("White", 2199), ("Gold", 2199), ("Black", 2199)], 12, "Keypad"),
]

COLORS = ["Midnight Black", "Starlight Blue", "Emerald Green", "Glacier White", "Titanium Gray", "Sunset Gold", "Purple Mist"]

# Accessories & Audio (Crucial revenue for mobile retail)
ACCESSORIES = [
    ("Boat Airdopes 141 TWS Earbuds", 1199, 12, "Audio"),
    ("Boat Airdopes 161 TWS Earbuds", 999, 12, "Audio"),
    ("Boat Rockerz 255 Pro+ Neckband", 1299, 12, "Audio"),
    ("OnePlus Nord Buds 2 TWS", 2499, 12, "Audio"),
    ("OnePlus Bullets Z2 Wireless Neckband", 1799, 12, "Audio"),
    ("Realme Buds Air 5 Pro TWS", 4499, 12, "Audio"),
    ("Realme Buds Wireless 3 Neckband", 1699, 12, "Audio"),
    ("Noise ColorFit Pulse 2 Max Smartwatch", 1499, 12, "Wearables"),
    ("Fire-Boltt Ninja Call Pro Plus", 1299, 12, "Wearables"),
    ("Fastrack Limitless FS1 Smartwatch", 1699, 12, "Wearables"),
    ("Boat Wave Call Smartwatch", 1399, 12, "Wearables"),
    ("Mi 22.5W Fast Charging Power Bank 10000mAh", 1299, 6, "Accessories"),
    ("Ambrane 20000mAh Power Bank 20W Fast Charge", 1799, 6, "Accessories"),
    ("Realme 33W SuperVOOC Charger Adapter", 899, 6, "Accessories"),
    ("OnePlus 65W Warp Charge Power Adapter", 1999, 6, "Accessories"),
    ("Samsung 25W Type-C Original Fast Adapter", 1299, 6, "Accessories"),
    ("Apple 20W USB-C Power Adapter", 1899, 6, "Accessories"),
    ("Portronics Konnect Type-C Braided Cable 1M", 199, 6, "Accessories"),
    ("Boat Deuce USB-C to Lightning Cable", 399, 6, "Accessories"),
    ("Universal 11D Edge-to-Edge Tempered Glass", 99, 0, "Protection"),
    ("Matte Finish Shockproof Silicone Back Case", 149, 0, "Protection"),
    ("Ultra-Clear Transparent Gel Bumper Case", 99, 0, "Protection"),
    ("Metal Ring Magnetic Car Phone Holder", 249, 3, "Accessories"),
    ("Sandisk 64GB Ultra MicroSDXC Memory Card", 499, 60, "Storage"),
    ("Sandisk 128GB Ultra MicroSDXC Memory Card", 899, 60, "Storage"),
]

def generate_csv(target_count=1000, filename="mobile_products_1000.csv"):
    rows = []
    barcode_base = 890123400000
    imei_base = 864201060000000

    # 1. Expand device models across storage and color combinations
    device_catalog = []
    for model, variants, warranty, cat in BASE_DEVICES:
        for storage, price in variants:
            for color in COLORS:
                title = f"{model} ({color}, {storage})"
                device_catalog.append((title, price, warranty, cat))

    random.seed(42)
    random.shuffle(device_catalog)

    curr_idx = 0
    # Generate devices
    for i in range(780):
        if curr_idx >= len(device_catalog):
            curr_idx = 0
        title, price, warranty, cat = device_catalog[curr_idx]
        curr_idx += 1
        
        barcode_base += 1
        imei_base += 1
        barcode = str(barcode_base)
        imei = str(imei_base)
        stock = random.randint(3, 20)
        
        rows.append({
            "barcode": barcode,
            "imei_serial": imei,
            "name": title,
            "price": f"{price:.2f}",
            "stock": stock,
            "warranty_months": warranty,
            "image_url": "/static/default-item.png"
        })

    # Generate accessories & wearables (IMEI is '-' for accessories)
    acc_idx = 0
    while len(rows) < target_count:
        item_name, price, warranty, cat = ACCESSORIES[acc_idx % len(ACCESSORIES)]
        acc_idx += 1
        barcode_base += 1
        barcode = str(barcode_base)
        imei = "-"
        stock = random.randint(15, 60)
        
        # Color or brand variation for accessories
        var_color = COLORS[acc_idx % len(COLORS)]
        full_title = f"{item_name} - {var_color}" if "Case" in item_name or "Tempered" in item_name or "Earbuds" in item_name else item_name
        
        rows.append({
            "barcode": barcode,
            "imei_serial": imei,
            "name": full_title,
            "price": f"{price:.2f}",
            "stock": stock,
            "warranty_months": warranty,
            "image_url": "/static/default-item.png"
        })

    # Write exactly 1000 rows
    headers = ["barcode", "imei_serial", "name", "price", "stock", "warranty_months", "image_url"]
    with open(filename, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows[:target_count])

    print(f"Success: {len(rows[:target_count])} items dataset exported to '{filename}'!")

if __name__ == "__main__":
    generate_csv(1000)