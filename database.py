import aiosqlite
from datetime import datetime

DB_NAME = "futures_bot.db"

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        # Tabel Sinyal (Raw Signals)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                symbol TEXT,
                side TEXT,
                confidence INTEGER
            )
        """)
        
        # Tabel Wallet (Simulasi Saldo)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS wallet (
                id INTEGER PRIMARY KEY,
                balance REAL DEFAULT 10.0
            )
        """)
        
        # Tabel Trades (Posisi Trading)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                symbol TEXT,
                side TEXT,
                entry_price REAL,
                margin REAL,
                leverage INTEGER,
                size_usdt REAL,
                tp1 REAL,
                tp2 REAL,
                tp3 REAL,
                sl REAL,
                status TEXT DEFAULT 'OPEN'
            )
        """)
        
        # Inisialisasi saldo $10 jika belum ada
        await db.execute("INSERT OR IGNORE INTO wallet (id, balance) VALUES (1, 10.0)")
        await db.commit()

async def get_balance():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT balance FROM wallet WHERE id=1") as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 10.0

async def update_balance(amount):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE wallet SET balance = balance + ? WHERE id=1", (amount,))
        await db.commit()

async def open_trade(data):
    # Kurangi saldo untuk margin
    await update_balance(-data['margin'])
    
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            INSERT INTO trades (timestamp, symbol, side, entry_price, margin, leverage, size_usdt, tp1, tp2, tp3, sl)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (data['timestamp'], data['symbol'], data['side'], data['price'], 
              data['margin'], data['leverage'], data['size'], 
              data['tp1'], data['tp2'], data['tp3'], data['sl']))
        await db.commit()

async def get_report_stats():
    async with aiosqlite.connect(DB_NAME) as db:
        # Hitung total trade
        async with db.execute("SELECT COUNT(*) FROM trades") as cursor:
            total_trades = (await cursor.fetchone())[0]
            
        # Ambil 5 trade terakhir
        async with db.execute("SELECT symbol, side, entry_price, status FROM trades ORDER BY id DESC LIMIT 5") as cursor:
            last_trades = await cursor.fetchall()
            
    return total_trades, last_trades