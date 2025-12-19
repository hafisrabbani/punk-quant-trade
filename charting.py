import matplotlib.pyplot as plt

def plot_signal_chart(df, signal):
    df = df.tail(100)

    plt.figure(figsize=(12,6))
    plt.plot(df['close'], label="Price")
    plt.plot(df['EMA_20'], label="EMA20")
    plt.plot(df['EMA_50'], label="EMA50")

    plt.axhline(signal['price'], color='blue', linestyle='--', label="Entry")
    plt.axhline(signal['sl'], color='red', linestyle='--', label="SL")
    plt.axhline(signal['tp1'], color='green', linestyle='--', label="TP1")
    plt.axhline(signal['tp2'], color='green', linestyle=':')

    plt.legend()
    plt.title(f"{signal['symbol']} {signal['side']} Setup")

    path = f"/tmp/{signal['symbol'].replace('/', '')}.png"
    plt.savefig(path)
    plt.close()
    return path
